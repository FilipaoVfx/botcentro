-- Funciones RPC (POST /api/database/rpc/<nombre>).
--
-- Las funciones que escriben son SECURITY DEFINER con search_path fijado y comienzan
-- verificando el rol de aplicación del llamante (require_app_role). Así las tablas append-only
-- y de operación no necesitan permisos de escritura directos para los roles de ejecución.
-- Las funciones de búsqueda son SECURITY INVOKER: respetan RLS y privilegios del llamante.
-- Los permisos EXECUTE se otorgan en la migración control-de-acceso.
--
-- Códigos de error propios: BC001 append-only, BC002 activación rechazada, BC003 observación
-- sin evidencia, BC004 estado inválido para la operación. 42501 permiso insuficiente,
-- 22023 parámetro inválido, P0002 objeto inexistente.

-- ---------------------------------------------------------------------------------------------
-- Guardianes append-only sin configuración de sesión
-- ---------------------------------------------------------------------------------------------
-- InsForge no admite modificar parámetros de sesión desde el SQL de las migraciones. La vía de
-- purga por variable de sesión de forbid_mutation se elimina: las tablas
-- append-only son inmutables sin excepciones, y la retención de la auditoría (365 días,
-- SRS-N05) se codifica en su propio guardián, que solo admite borrar filas vencidas.

create or replace function public.forbid_mutation() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  raise exception using
    errcode = 'BC001',
    message = format('%I es append-only: %s no permitido', tg_table_name, tg_op);
end
$$;

create or replace function public.guard_audit_log() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if tg_op = 'DELETE' and old.occurred_at < now() - interval '365 days' then
    return old;
  end if;
  raise exception using
    errcode = 'BC001',
    message = format('audit_log es append-only: %s solo se admite tras la retención de 365 días', tg_op);
end
$$;

drop trigger audit_log_append_only on public.audit_log;
create trigger audit_log_append_only before update or delete on public.audit_log
  for each row execute function public.guard_audit_log();

-- ---------------------------------------------------------------------------------------------
-- Cola de trabajos (SRS §7.3, T-03)
-- ---------------------------------------------------------------------------------------------

-- Rol de servicio que puede procesar cada familia de trabajos (mínimo privilegio).
create or replace function public.job_kind_role(p_kind text) returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select case split_part(p_kind, '.', 1)
    when 'ingest' then 'ingest_service'
    when 'document' then 'ingest_service'
    when 'index' then 'ingest_service'
    when 'maintenance' then 'ingest_service'
    when 'query' then 'query_service'
    when 'telegram' then 'query_service'
    when 'delivery' then 'query_service'
    when 'comparison' then 'query_service'
    else 'admin'
  end
$$;

create or replace function public.jobs_enqueue(
  p_kind               text,
  p_idempotency_key    text,
  p_payload            jsonb default '{}',
  p_processing_version text default '1',
  p_run_at             timestamptz default null,
  p_priority           integer default 100,
  p_max_attempts       integer default 5,
  p_source_id          uuid default null,
  p_run_id             uuid default null
)
returns table (job_id uuid, created boolean)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_id uuid;
begin
  perform public.require_app_role(array['ingest_service', 'query_service', 'operator', 'admin']);
  if not public.has_app_role(array[public.job_kind_role(p_kind), 'operator', 'admin']) then
    raise exception using errcode = '42501', message = format('no puede encolar trabajos %s', p_kind);
  end if;
  if coalesce(length(p_idempotency_key), 0) = 0 then
    raise exception using errcode = '22023', message = 'idempotency_key requerida';
  end if;

  insert into public.jobs (kind, processing_version, idempotency_key, payload, run_at, priority,
                           max_attempts, source_id, run_id)
  values (p_kind, p_processing_version, p_idempotency_key, coalesce(p_payload, '{}'),
          coalesce(p_run_at, now()), p_priority, p_max_attempts, p_source_id, p_run_id)
  on conflict (kind, processing_version, idempotency_key) do nothing
  returning id into v_id;

  if v_id is not null then
    return query select v_id, true;
  else
    return query
      select j.id, false from public.jobs j
      where j.kind = p_kind and j.processing_version = p_processing_version
        and j.idempotency_key = p_idempotency_key;
  end if;
end
$$;

-- Toma hasta p_limit trabajos listos con lease. SKIP LOCKED permite varios workers.
create or replace function public.jobs_claim(
  p_worker        text,
  p_kinds         text[],
  p_limit         integer default 1,
  p_lease_seconds integer default 300
)
returns setof public.jobs
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  if coalesce(length(p_worker), 0) not between 1 and 200 then
    raise exception using errcode = '22023', message = 'identificador de worker inválido';
  end if;
  if coalesce(cardinality(p_kinds), 0) = 0 then
    raise exception using errcode = '22023', message = 'debe indicar los tipos de trabajo';
  end if;
  if p_limit not between 1 and 100 or p_lease_seconds not between 10 and 3600 then
    raise exception using errcode = '22023', message = 'límite o lease fuera de rango';
  end if;
  if exists (select 1 from unnest(p_kinds) k where not public.has_app_role(array[public.job_kind_role(k)])) then
    raise exception using errcode = '42501', message = 'tipos de trabajo no permitidos para este servicio';
  end if;

  return query
    with ready as (
      select j.id
      from public.jobs j
      where j.state in ('pending', 'retry_wait')
        and j.run_at <= now()
        and j.kind = any (p_kinds)
      order by j.priority, j.run_at
      for update skip locked
      limit p_limit
    )
    update public.jobs j
       set state = 'leased',
           lease_owner = p_worker,
           lease_until = now() + make_interval(secs => p_lease_seconds),
           attempts = j.attempts + 1
      from ready
     where j.id = ready.id
    returning j.*;
end
$$;

-- Extiende el lease; false si el worker ya no lo posee (fencing).
create or replace function public.jobs_heartbeat(p_job_id uuid, p_worker text, p_lease_seconds integer default 300)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  if p_lease_seconds not between 10 and 3600 then
    raise exception using errcode = '22023', message = 'lease fuera de rango';
  end if;
  update public.jobs
     set lease_until = now() + make_interval(secs => p_lease_seconds)
   where id = p_job_id and state = 'leased' and lease_owner = p_worker and lease_until > now();
  return found;
end
$$;

create or replace function public.jobs_complete(p_job_id uuid, p_worker text)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  update public.jobs
     set state = 'succeeded', lease_owner = null, lease_until = null, finished_at = now()
   where id = p_job_id and state = 'leased' and lease_owner = p_worker;
  return found;
end
$$;

-- Registra un fallo. p_retry_at null (error no reintentable) o intentos agotados → dead_letter.
-- Devuelve el nuevo estado, o null si el worker perdió el lease.
create or replace function public.jobs_fail(
  p_job_id     uuid,
  p_worker     text,
  p_error_code text,
  p_error      text default null,
  p_retry_at   timestamptz default null
)
returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_job   public.jobs%rowtype;
  v_state text;
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  select * into v_job from public.jobs
   where id = p_job_id and state = 'leased' and lease_owner = p_worker
   for update;
  if not found then
    return null;
  end if;

  v_state := case
    when p_retry_at is null or v_job.attempts >= v_job.max_attempts then 'dead_letter'
    else 'retry_wait'
  end;

  update public.jobs
     set state = v_state,
         lease_owner = null,
         lease_until = null,
         last_error_code = left(p_error_code, 100),
         last_error = left(p_error, 2000),
         run_at = case when v_state = 'retry_wait' then greatest(p_retry_at, now()) else run_at end,
         finished_at = case when v_state = 'dead_letter' then now() end
   where id = p_job_id;
  return v_state;
end
$$;

-- Recupera trabajos cuyo worker murió (lease vencido). T-03.
create or replace function public.jobs_release_expired()
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_count integer;
begin
  perform public.require_app_role(array['ingest_service', 'query_service', 'operator']);
  update public.jobs
     set state = case when attempts >= max_attempts then 'dead_letter' else 'retry_wait' end,
         lease_owner = null,
         lease_until = null,
         last_error_code = 'LEASE_EXPIRED',
         run_at = now(),
         finished_at = case when attempts >= max_attempts then now() end
   where state = 'leased' and lease_until < now();
  get diagnostics v_count = row_count;
  return v_count;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Presupuesto (SRS-N17, T-29)
-- ---------------------------------------------------------------------------------------------

-- Reserva atómica: bloquea los presupuestos aplicables (global y del proveedor) en orden fijo,
-- suma lo consumido en la ventana (día/mes de Bogotá) y registra la reserva si cabe.
-- Sin presupuesto configurado no se autoriza gasto (DEC-07).
create or replace function public.budget_reserve(
  p_provider       text,
  p_operation      text,
  p_estimated_cost numeric,
  p_units          numeric default 0,
  p_job_id         uuid default null,
  p_query_id       uuid default null,
  p_source_id      uuid default null
)
returns table (granted boolean, reservation_id uuid, alert boolean, reason text)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_uid          uuid;
  b              record;
  v_now          timestamptz := now();
  v_local_now    timestamp := now() at time zone 'America/Bogota';
  v_window_start timestamptz;
  v_spent        numeric;
  v_alert        boolean := false;
  v_budgets      integer := 0;
  v_id           uuid;
begin
  v_uid := public.require_app_role(array['ingest_service', 'query_service']);
  if p_estimated_cost is null or p_estimated_cost < 0 or coalesce(length(p_provider), 0) = 0 then
    raise exception using errcode = '22023', message = 'proveedor y costo estimado no negativo requeridos';
  end if;

  for b in
    select * from public.budgets
     where active and (provider is null or provider = p_provider)
     order by id
     for update
  loop
    v_budgets := v_budgets + 1;
    v_window_start := case b.period
      when 'daily' then date_trunc('day', v_local_now) at time zone 'America/Bogota'
      else date_trunc('month', v_local_now) at time zone 'America/Bogota'
    end;

    select coalesce(sum(coalesce(u.settled_cost, u.estimated_cost)), 0)
      into v_spent
      from public.usage_ledger u
     where u.state <> 'released'
       and u.reserved_at >= v_window_start
       and (b.provider is null or u.provider = b.provider);

    if b.hard_stop and v_spent + p_estimated_cost > b.limit_amount then
      return query select false, null::uuid, true,
        format('presupuesto %s agotado (%s + %s > %s %s)', b.name, v_spent, p_estimated_cost,
               b.limit_amount, b.currency);
      return;
    end if;
    if v_spent + p_estimated_cost >= b.alert_ratio * b.limit_amount then
      v_alert := true;
    end if;
  end loop;

  if v_budgets = 0 then
    return query select false, null::uuid, false, format('sin presupuesto configurado para %s', p_provider);
    return;
  end if;

  insert into public.usage_ledger (provider, operation, units, estimated_cost, job_id, query_id,
                                   source_id, reserved_by, reserved_at)
  values (p_provider, p_operation, coalesce(p_units, 0), p_estimated_cost, p_job_id, p_query_id,
          p_source_id, v_uid, v_now)
  returning id into v_id;

  return query select true, v_id, v_alert, null::text;
end
$$;

create or replace function public.budget_settle(p_reservation_id uuid, p_actual_cost numeric, p_units numeric default null)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  if p_actual_cost is null or p_actual_cost < 0 then
    raise exception using errcode = '22023', message = 'costo real no negativo requerido';
  end if;
  update public.usage_ledger
     set state = 'settled', settled_cost = p_actual_cost, settled_at = now(),
         units = coalesce(p_units, units)
   where id = p_reservation_id and state = 'reserved';
  return found;
end
$$;

create or replace function public.budget_release(p_reservation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service', 'query_service']);
  update public.usage_ledger set state = 'released'
   where id = p_reservation_id and state = 'reserved';
  return found;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Ingesta (SRS-F03..F06, T-02, T-12)
-- ---------------------------------------------------------------------------------------------

create or replace function public.ingest_start_run(
  p_source_id         uuid,
  p_mode              text,
  p_connector_version text,
  p_coverage_scope_id uuid default null,
  p_cursor_before     jsonb default null
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid   uuid;
  v_state text;
  v_id    uuid;
begin
  v_uid := public.require_app_role(array['ingest_service']);
  select state into v_state from public.sources where id = p_source_id for share;
  if not found then
    raise exception using errcode = 'P0002', message = 'fuente inexistente';
  end if;
  -- Las ejecuciones de validación (descubrimiento, H0) se permiten antes de activar la fuente;
  -- las productivas exigen fuente activa o degradada.
  if (p_mode = 'validation' and v_state not in ('candidate', 'validated', 'active', 'degraded'))
     or (p_mode <> 'validation' and v_state not in ('active', 'degraded')) then
    raise exception using errcode = 'BC004',
      message = format('la fuente en estado %s no admite ejecuciones %s', v_state, p_mode);
  end if;
  if p_coverage_scope_id is not null and not exists (
    select 1 from public.coverage_scopes where id = p_coverage_scope_id and source_id = p_source_id
  ) then
    raise exception using errcode = '22023', message = 'el alcance de cobertura no pertenece a la fuente';
  end if;

  insert into public.ingestion_runs (source_id, coverage_scope_id, mode, connector_version,
                                     cursor_before, triggered_by)
  values (p_source_id, p_coverage_scope_id, p_mode, p_connector_version, p_cursor_before, v_uid)
  returning id into v_id;
  return v_id;
end
$$;

create or replace function public.ingest_active_run(p_run_id uuid)
returns public.ingestion_runs
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_run public.ingestion_runs%rowtype;
begin
  select * into v_run from public.ingestion_runs where id = p_run_id;
  if not found or v_run.status <> 'running' then
    raise exception using errcode = 'BC004', message = 'la ejecución no existe o ya terminó';
  end if;
  return v_run;
end
$$;

-- Registra una descarga. Devuelve 'new_snapshot' (bytes nunca vistos), 'reverted' (bytes ya
-- capturados pero distintos de la última comprobación) o 'unchanged'.
create or replace function public.ingest_register_fetch(
  p_run_id          uuid,
  p_record_type     text,
  p_logical_key     text,
  p_external_id     text,
  p_canonical_url   text,
  p_requested_url   text,
  p_final_url       text,
  p_http_status     integer,
  p_content_hash    text,
  p_byte_size       bigint,
  p_mime_type       text,
  p_object_key      text,
  p_etag            text,
  p_last_modified   text,
  p_adapter_version text,
  p_fetched_at      timestamptz
)
returns table (record_id uuid, snapshot_id uuid, result text)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_run         public.ingestion_runs%rowtype;
  v_record_id   uuid;
  v_snapshot_id uuid;
  v_last_hash   text;
  v_result      text;
begin
  perform public.require_app_role(array['ingest_service']);
  v_run := public.ingest_active_run(p_run_id);

  insert into public.source_records (source_id, record_type, logical_key, external_id, canonical_url)
  values (v_run.source_id, p_record_type, p_logical_key, p_external_id, p_canonical_url)
  on conflict (source_id, logical_key) do update
     set last_seen_at = now(),
         external_id = coalesce(excluded.external_id, public.source_records.external_id),
         canonical_url = coalesce(excluded.canonical_url, public.source_records.canonical_url)
  returning id into v_record_id;

  -- Serializa comprobaciones concurrentes del mismo registro.
  perform 1 from public.source_records where id = v_record_id for update;

  select s.content_hash into v_last_hash
    from public.source_checks c
    join public.source_snapshots s on s.id = c.snapshot_id
   where c.record_id = v_record_id
   order by c.seq desc
   limit 1;

  select s.id into v_snapshot_id
    from public.source_snapshots s
   where s.record_id = v_record_id and s.content_hash = p_content_hash;

  if v_snapshot_id is null then
    insert into public.source_snapshots (record_id, run_id, requested_url, final_url, http_status,
                                         content_hash, byte_size, mime_type, object_key, etag,
                                         last_modified, adapter_version, fetched_at)
    values (v_record_id, p_run_id, p_requested_url, p_final_url, p_http_status, p_content_hash,
            p_byte_size, p_mime_type, p_object_key, p_etag, p_last_modified, p_adapter_version,
            p_fetched_at)
    returning id into v_snapshot_id;
    v_result := 'new_snapshot';
  elsif v_last_hash is distinct from p_content_hash then
    v_result := 'reverted';
  else
    v_result := 'unchanged';
  end if;

  insert into public.source_checks (record_id, run_id, result, snapshot_id, http_status)
  values (v_record_id, p_run_id, v_result, v_snapshot_id, p_http_status);

  update public.ingestion_runs
     set items_fetched = items_fetched + (v_result <> 'unchanged')::int,
         items_unchanged = items_unchanged + (v_result = 'unchanged')::int
   where id = p_run_id;

  return query select v_record_id, v_snapshot_id, v_result;
end
$$;

-- Comprobación sin descarga nueva: not_modified (304), not_found, error o señal de retiro.
-- Devuelve null si el registro nunca se había visto (solo cuenta en la ejecución).
create or replace function public.ingest_register_check(
  p_run_id      uuid,
  p_logical_key text,
  p_result      text,
  p_http_status integer default null,
  p_error_code  text default null,
  p_detail      text default null
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_run         public.ingestion_runs%rowtype;
  v_record_id   uuid;
  v_snapshot_id uuid;
  v_check_id    uuid;
begin
  perform public.require_app_role(array['ingest_service']);
  v_run := public.ingest_active_run(p_run_id);
  if p_result not in ('not_modified', 'not_found', 'error', 'withdrawal_signal') then
    raise exception using errcode = '22023', message = format('resultado inválido: %s', p_result);
  end if;
  if p_result = 'withdrawal_signal' and coalesce(length(p_detail), 0) = 0 then
    raise exception using errcode = '22023', message = 'un retiro requiere la señal explícita en detail';
  end if;

  select id into v_record_id from public.source_records
   where source_id = v_run.source_id and logical_key = p_logical_key
   for update;

  if v_record_id is null then
    update public.ingestion_runs set items_failed = items_failed + (p_result = 'error')::int
     where id = p_run_id;
    return null;
  end if;

  if p_result = 'not_modified' then
    select c.snapshot_id into v_snapshot_id
      from public.source_checks c
     where c.record_id = v_record_id and c.snapshot_id is not null
     order by c.seq desc limit 1;
    if v_snapshot_id is null then
      raise exception using errcode = 'BC004', message = 'not_modified sin captura previa';
    end if;
  end if;

  insert into public.source_checks (record_id, run_id, result, snapshot_id, http_status, error_code, detail)
  values (v_record_id, p_run_id, p_result, v_snapshot_id, p_http_status, p_error_code, p_detail)
  returning id into v_check_id;

  if p_result = 'withdrawal_signal' then
    update public.source_records
       set withdrawn_at = coalesce(withdrawn_at, now()),
           withdrawal_basis = coalesce(withdrawal_basis, p_detail)
     where id = v_record_id;
  end if;

  update public.ingestion_runs
     set items_unchanged = items_unchanged + (p_result = 'not_modified')::int,
         items_failed = items_failed + (p_result = 'error')::int
   where id = p_run_id;
  return v_check_id;
end
$$;

-- Clave canónica de una observación. El sujeto se identifica preferentemente por la referencia
-- externa (estable aunque luego se resuelva a una entidad canónica). Los instantes se
-- serializan en UTC para no depender de la zona horaria de la sesión.
create or replace function public.observation_key(
  p_source_id      uuid,
  p_subject_type   text,
  p_subject_ref    text,
  p_subject_id     uuid,
  p_predicate      text,
  p_value_json     jsonb,
  p_effective_date date,
  p_date_precision text,
  p_effective_at   timestamptz
)
returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select encode(sha256(convert_to(jsonb_build_array(
    p_source_id::text,
    p_subject_type,
    coalesce(p_subject_ref, p_subject_id::text),
    p_predicate,
    p_value_json,
    p_effective_date::text,
    p_date_precision,
    to_char(p_effective_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  )::text, 'UTF8')), 'hex')
$$;

-- Crea o reencuentra una observación y enlaza la evidencia de la captura. Idempotente (T-02).
create or replace function public.ingest_upsert_observation(
  p_snapshot_id    uuid,
  p_subject_type   text,
  p_subject_ref    text,
  p_predicate      text,
  p_value_json     jsonb,
  p_parser_version text,
  p_subject_id     uuid default null,
  p_value_raw      text default null,
  p_effective_date date default null,
  p_date_precision text default 'unknown',
  p_effective_at   timestamptz default null,
  p_published_on   date default null,
  p_record_pointer text default null,
  p_status         text default 'published',
  p_status_reason  text default null
)
returns table (observation_id uuid, created boolean, evidence_id uuid)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_src     record;
  v_key     text;
  v_obs_id  uuid;
  v_created boolean;
  v_ev_key  text;
  v_ev_id   uuid;
begin
  perform public.require_app_role(array['ingest_service']);
  if p_status not in ('published', 'candidate', 'quarantined') then
    raise exception using errcode = '22023', message = format('estado inicial inválido: %s', p_status);
  end if;
  if p_status = 'quarantined' and coalesce(length(p_status_reason), 0) = 0 then
    raise exception using errcode = '22023', message = 'la cuarentena requiere motivo';
  end if;
  if coalesce(length(p_parser_version), 0) = 0 then
    raise exception using errcode = '22023', message = 'parser_version requerida';
  end if;

  select s.id as source_id, s.authority, sn.fetched_at
    into v_src
    from public.source_snapshots sn
    join public.source_records r on r.id = sn.record_id
    join public.sources s on s.id = r.source_id
   where sn.id = p_snapshot_id;
  if not found then
    raise exception using errcode = 'P0002', message = 'captura inexistente';
  end if;

  v_key := public.observation_key(v_src.source_id, p_subject_type, p_subject_ref, p_subject_id,
                                  p_predicate, p_value_json, p_effective_date, p_date_precision,
                                  p_effective_at);

  insert into public.observations (observation_key, source_id, first_snapshot_id, authority,
                                   subject_type, subject_id, subject_ref, predicate, value_json,
                                   value_raw, effective_date, date_precision, effective_at,
                                   published_on, first_observed_at, last_observed_at, status,
                                   status_reason, parser_version)
  values (v_key, v_src.source_id, p_snapshot_id, v_src.authority, p_subject_type, p_subject_id,
          p_subject_ref, p_predicate, p_value_json, p_value_raw, p_effective_date, p_date_precision,
          p_effective_at, p_published_on, v_src.fetched_at, v_src.fetched_at, p_status,
          p_status_reason, p_parser_version)
  on conflict (observation_key) do update
     set last_observed_at = greatest(public.observations.last_observed_at, excluded.last_observed_at),
         first_observed_at = least(public.observations.first_observed_at, excluded.first_observed_at),
         subject_id = coalesce(public.observations.subject_id, excluded.subject_id)
  returning id, (xmax = 0) into v_obs_id, v_created;

  v_ev_key := encode(sha256(convert_to(jsonb_build_array(
    'source_record', p_snapshot_id::text, coalesce(p_record_pointer, '')
  )::text, 'UTF8')), 'hex');

  insert into public.evidence (evidence_key, kind, snapshot_id, record_pointer)
  values (v_ev_key, 'source_record', p_snapshot_id, p_record_pointer)
  on conflict (evidence_key) do nothing
  returning id into v_ev_id;
  if v_ev_id is null then
    select id into v_ev_id from public.evidence where evidence_key = v_ev_key;
  end if;

  insert into public.observation_evidence (observation_id, evidence_id)
  values (v_obs_id, v_ev_id)
  on conflict do nothing;

  return query select v_obs_id, v_created, v_ev_id;
end
$$;

-- Confirma el cursor solo después de persistir el lote (SRS-F04).
create or replace function public.ingest_commit_cursor(p_run_id uuid, p_cursor jsonb)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_run public.ingestion_runs%rowtype;
begin
  perform public.require_app_role(array['ingest_service']);
  v_run := public.ingest_active_run(p_run_id);
  if p_cursor is null or not (p_cursor ? 'connector_version') then
    raise exception using errcode = '22023', message = 'el cursor debe incluir connector_version (SRS §7.1)';
  end if;
  update public.ingestion_runs set cursor_after = p_cursor where id = v_run.id;
end
$$;

create or replace function public.ingest_add_counters(
  p_run_id      uuid,
  p_discovered  integer default 0,
  p_normalized  integer default 0,
  p_quarantined integer default 0,
  p_failed      integer default 0
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service']);
  perform public.ingest_active_run(p_run_id);
  if least(p_discovered, p_normalized, p_quarantined, p_failed) < 0 then
    raise exception using errcode = '22023', message = 'los contadores no pueden ser negativos';
  end if;
  update public.ingestion_runs
     set items_discovered = items_discovered + p_discovered,
         items_normalized = items_normalized + p_normalized,
         items_quarantined = items_quarantined + p_quarantined,
         items_failed = items_failed + p_failed
   where id = p_run_id;
end
$$;

create or replace function public.ingest_finish_run(p_run_id uuid, p_status text, p_error_code text default null)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_run public.ingestion_runs%rowtype;
begin
  perform public.require_app_role(array['ingest_service']);
  v_run := public.ingest_active_run(p_run_id);
  if p_status not in ('succeeded', 'partial', 'failed', 'cancelled') then
    raise exception using errcode = '22023', message = format('estado final inválido: %s', p_status);
  end if;
  update public.ingestion_runs
     set status = p_status, error_code = p_error_code, finished_at = now()
   where id = p_run_id;
  update public.sources
     set last_checked_at = now(),
         last_success_at = case when p_status = 'succeeded' then now() else last_success_at end
   where id = v_run.source_id;
end
$$;

-- Abre un caso de revisión o devuelve el abierto con la misma clave.
create or replace function public.review_open_case(
  p_case_type    text,
  p_dedupe_key   text,
  p_summary      text,
  p_subject_type text default null,
  p_subject_id   uuid default null,
  p_candidates   jsonb default '[]',
  p_evidence_ids uuid[] default '{}',
  p_run_id       uuid default null
)
returns table (case_id uuid, created boolean)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_uid uuid;
  v_id  uuid;
begin
  v_uid := public.require_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin']);
  insert into public.review_cases (case_type, dedupe_key, summary, subject_type, subject_id,
                                   candidates, evidence_ids, related_run_id, opened_by)
  values (p_case_type, p_dedupe_key, p_summary, p_subject_type, p_subject_id,
          coalesce(p_candidates, '[]'), coalesce(p_evidence_ids, '{}'), p_run_id, v_uid)
  on conflict (dedupe_key) where state in ('open', 'in_review') do nothing
  returning id into v_id;

  if v_id is not null then
    return query select v_id, true;
  else
    return query
      select rc.id, false from public.review_cases rc
       where rc.dedupe_key = p_dedupe_key and rc.state in ('open', 'in_review');
  end if;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Telegram (SRS-F22, §11, T-21)
-- ---------------------------------------------------------------------------------------------

-- Persiste la actualización y, si procede, la consulta y su trabajo, en una sola transacción,
-- antes de que el webhook confirme a Telegram. Duplicados devuelven el registro original.
-- Los usuarios no autorizados no se almacenan (minimización, SRS-N05).
create or replace function public.telegram_accept_update(
  p_bot_id                bigint,
  p_update_id             bigint,
  p_update_kind           text,
  p_user_hash             text,
  p_chat_type             text,
  p_text                  text,
  p_rate_limit_per_minute integer default 10
)
returns table (status text, duplicate boolean, telegram_identity_id uuid, query_run_id uuid, job_id uuid)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_prev   public.telegram_updates%rowtype;
  v_ident  public.telegram_identities%rowtype;
  v_status text;
  v_recent integer;
  v_qr     uuid;
  v_job    uuid;
  v_key    text := format('tg:%s:%s', p_bot_id, p_update_id);
begin
  perform public.require_app_role(array['query_service']);

  select * into v_prev from public.telegram_updates
   where bot_id = p_bot_id and update_id = p_update_id;
  if found then
    return query select v_prev.status, true, v_prev.telegram_identity_id, v_prev.query_run_id, v_prev.job_id;
    return;
  end if;

  select * into v_ident from public.telegram_identities
   where bot_id = p_bot_id and user_hash = p_user_hash;

  if p_update_kind not in ('message', 'callback_query') or p_chat_type is distinct from 'private' then
    v_status := 'rejected_unsupported';
  elsif v_ident.id is null or not v_ident.authorized then
    v_status := 'rejected_unauthorized';
  else
    select count(*) into v_recent from public.query_runs q
     where q.principal_id = v_ident.principal_id and q.created_at > now() - interval '1 minute';
    v_status := case when v_recent >= p_rate_limit_per_minute then 'rejected_rate_limited' else 'accepted' end;
  end if;

  insert into public.telegram_updates (bot_id, update_id, update_kind, telegram_identity_id, status)
  values (p_bot_id, p_update_id,
          case when p_update_kind in ('message', 'callback_query') then p_update_kind else 'other' end,
          case when v_status = 'rejected_unauthorized' then null else v_ident.id end,
          v_status)
  on conflict do nothing;
  if not found then
    -- Entrega concurrente del mismo update: la otra transacción ya lo registró.
    return query
      select u.status, true, u.telegram_identity_id, u.query_run_id, u.job_id
        from public.telegram_updates u where u.bot_id = p_bot_id and u.update_id = p_update_id;
    return;
  end if;

  if v_status = 'accepted' then
    if p_update_kind = 'message' then
      insert into public.query_runs (principal_id, channel, question_text, status, idempotency_key)
      values (v_ident.principal_id, 'telegram', left(p_text, 4096), 'queued', v_key)
      returning id into v_qr;
      insert into public.jobs (kind, idempotency_key, payload, priority)
      values ('query.telegram_message', v_key,
              jsonb_build_object('query_run_id', v_qr, 'telegram_identity_id', v_ident.id), 50)
      returning id into v_job;
    else
      insert into public.jobs (kind, idempotency_key, payload, priority)
      values ('query.telegram_callback', v_key,
              jsonb_build_object('telegram_identity_id', v_ident.id, 'callback_data', left(p_text, 256)), 50)
      returning id into v_job;
    end if;
    update public.telegram_updates set query_run_id = v_qr, job_id = v_job
     where bot_id = p_bot_id and update_id = p_update_id;
  end if;

  return query select v_status, false,
    case when v_status = 'rejected_unauthorized' then null else v_ident.id end, v_qr, v_job;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Administración auditada (SRS-F24, F25, T-23)
-- ---------------------------------------------------------------------------------------------

create or replace function public.write_audit(
  p_actor_id uuid, p_action text, p_target_type text, p_target_id text,
  p_before jsonb, p_after jsonb, p_reason text
)
returns void
language sql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  insert into public.audit_log (actor_id, action, target_type, target_id, before_value, after_value, reason)
  values (p_actor_id, p_action, p_target_type, p_target_id, p_before, p_after, p_reason)
$$;

-- Operador: suspender/degradar. Administrador: cualquier transición (la activación la valida
-- enforce_source_activation).
create or replace function public.admin_set_source_state(p_source_id uuid, p_state text, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
  v_old public.sources%rowtype;
begin
  v_uid := public.require_app_role(array['operator', 'admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  if p_state not in ('suspended', 'degraded') and not public.has_app_role(array['admin']) then
    raise exception using errcode = '42501', message = format('solo un administrador puede pasar a %s', p_state);
  end if;
  select * into v_old from public.sources where id = p_source_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'fuente inexistente';
  end if;
  update public.sources set state = p_state, state_reason = p_reason where id = p_source_id;
  perform public.write_audit(v_uid, 'source.set_state', 'source', p_source_id::text,
    jsonb_build_object('state', v_old.state), jsonb_build_object('state', p_state), p_reason);
end
$$;

-- SRS-F05: un fallo de autenticación/permisos suspende el conector sin esperar a un humano.
-- El servicio de ingesta solo puede suspender o degradar, nunca activar.
create or replace function public.ingest_suspend_source(p_source_id uuid, p_state text, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
  v_old public.sources%rowtype;
begin
  v_uid := public.require_app_role(array['ingest_service']);
  if p_state not in ('suspended', 'degraded') then
    raise exception using errcode = '42501', message = 'el servicio de ingesta solo puede suspender o degradar';
  end if;
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  select * into v_old from public.sources where id = p_source_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'fuente inexistente';
  end if;
  if v_old.state not in ('active', 'degraded') or v_old.state = p_state then
    return;
  end if;
  update public.sources set state = p_state, state_reason = p_reason where id = p_source_id;
  perform public.write_audit(v_uid, 'source.auto_' || p_state, 'source', p_source_id::text,
    jsonb_build_object('state', v_old.state), jsonb_build_object('state', p_state), p_reason);
end
$$;

-- Nueva versión de perfil de uso revisado; queda como política vigente de la fuente.
create or replace function public.admin_add_source_policy(
  p_source_id            uuid,
  p_capture_metadata     text,
  p_download_files       text,
  p_retain_content       text,
  p_generate_derivatives text,
  p_index_content        text,
  p_redistribute         text,
  p_reviewer             text,
  p_evidence_url         text,
  p_retention_policy     text,
  p_reason               text
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid     uuid;
  v_version integer;
  v_id      uuid;
begin
  v_uid := public.require_app_role(array['admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 or coalesce(length(btrim(p_reviewer)), 0) = 0 then
    raise exception using errcode = '22023', message = 'revisor y motivo obligatorios';
  end if;
  perform 1 from public.sources where id = p_source_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'fuente inexistente';
  end if;
  select coalesce(max(version), 0) + 1 into v_version from public.source_policies where source_id = p_source_id;

  insert into public.source_policies (source_id, version, capture_metadata, download_files,
                                      retain_content, generate_derivatives, index_content,
                                      redistribute, retention_policy, evidence_url, reviewer,
                                      reviewed_at, approval_reason)
  values (p_source_id, v_version, p_capture_metadata, p_download_files, p_retain_content,
          p_generate_derivatives, p_index_content, p_redistribute, p_retention_policy,
          p_evidence_url, p_reviewer, now(), p_reason)
  returning id into v_id;

  update public.sources set policy_id = v_id where id = p_source_id;
  perform public.write_audit(v_uid, 'source.add_policy', 'source', p_source_id::text, null,
    jsonb_build_object('policy_id', v_id, 'version', v_version), p_reason);
  return v_id;
end
$$;

create or replace function public.admin_retry_job(p_job_id uuid, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
  v_job public.jobs%rowtype;
begin
  v_uid := public.require_app_role(array['operator', 'admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  select * into v_job from public.jobs where id = p_job_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'trabajo inexistente';
  end if;
  if v_job.state not in ('dead_letter', 'cancelled', 'retry_wait') then
    raise exception using errcode = 'BC004', message = format('no se reintenta un trabajo en estado %s', v_job.state);
  end if;
  update public.jobs
     set state = 'pending', run_at = now(), finished_at = null,
         max_attempts = greatest(max_attempts, attempts + 1)
   where id = p_job_id;
  perform public.write_audit(v_uid, 'job.retry', 'job', p_job_id::text,
    jsonb_build_object('state', v_job.state, 'attempts', v_job.attempts), jsonb_build_object('state', 'pending'),
    p_reason);
end
$$;

create or replace function public.admin_authorize_telegram(
  p_bot_id    bigint,
  p_user_hash text,
  p_chat_id   bigint,
  p_label     text,
  p_reason    text
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid   uuid;
  v_ident public.telegram_identities%rowtype;
  v_pid   uuid;
begin
  v_uid := public.require_app_role(array['admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  select * into v_ident from public.telegram_identities
   where bot_id = p_bot_id and user_hash = p_user_hash for update;

  if found then
    update public.telegram_identities
       set authorized = true, authorized_at = now(), authorized_by = v_uid, revoked_at = null,
           chat_id = p_chat_id
     where id = v_ident.id;
  else
    insert into public.principals (kind, display_label) values ('telegram_user', p_label)
    returning id into v_pid;
    insert into public.telegram_identities (principal_id, bot_id, user_hash, chat_id, authorized,
                                            authorized_at, authorized_by)
    values (v_pid, p_bot_id, p_user_hash, p_chat_id, true, now(), v_uid)
    returning * into v_ident;
  end if;

  perform public.write_audit(v_uid, 'telegram.authorize', 'telegram_identity', v_ident.id::text, null,
    jsonb_build_object('authorized', true), p_reason);
  return v_ident.id;
end
$$;

create or replace function public.admin_revoke_telegram(p_identity_id uuid, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
begin
  v_uid := public.require_app_role(array['admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  update public.telegram_identities
     set authorized = false, revoked_at = now()
   where id = p_identity_id;
  if not found then
    raise exception using errcode = 'P0002', message = 'identidad inexistente';
  end if;
  delete from public.chat_contexts where telegram_identity_id = p_identity_id;
  perform public.write_audit(v_uid, 'telegram.revoke', 'telegram_identity', p_identity_id::text,
    jsonb_build_object('authorized', true), jsonb_build_object('authorized', false), p_reason);
end
$$;

create or replace function public.admin_grant_role(p_user_id uuid, p_role text, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
begin
  v_uid := public.require_app_role(array['admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  insert into public.app_roles (user_id, role, granted_by, note)
  values (p_user_id, p_role, v_uid, p_reason)
  on conflict (user_id, role) do nothing;
  perform public.write_audit(v_uid, 'role.grant', 'user', p_user_id::text, null,
    jsonb_build_object('role', p_role), p_reason);
end
$$;

create or replace function public.admin_revoke_role(p_user_id uuid, p_role text, p_reason text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid;
begin
  v_uid := public.require_app_role(array['admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  if p_user_id = v_uid and p_role = 'admin' then
    raise exception using errcode = 'BC004', message = 'un administrador no puede retirarse su propio rol';
  end if;
  delete from public.app_roles where user_id = p_user_id and role = p_role;
  perform public.write_audit(v_uid, 'role.revoke', 'user', p_user_id::text,
    jsonb_build_object('role', p_role), null, p_reason);
end
$$;

-- Resolución de un caso con motivo obligatorio (SRS-F25). La aplicación del cambio y la
-- invalidación de derivados/cachés la hace un worker a partir del trabajo encolado.
create or replace function public.review_resolve(
  p_case_id      uuid,
  p_decision     text,
  p_reason       text,
  p_before       jsonb default null,
  p_after        jsonb default null,
  p_evidence_ids uuid[] default '{}'
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid;
  v_case public.review_cases%rowtype;
  v_id   uuid;
begin
  v_uid := public.require_app_role(array['reviewer', 'admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  if p_decision = 'revert' then
    raise exception using errcode = '22023', message = 'use review_revert para revertir';
  end if;
  select * into v_case from public.review_cases where id = p_case_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'caso inexistente';
  end if;
  if v_case.state not in ('open', 'in_review') then
    raise exception using errcode = 'BC004', message = format('el caso está %s', v_case.state);
  end if;

  insert into public.review_resolutions (case_id, decision, reason, actor_id, before_value,
                                         after_value, evidence_ids)
  values (p_case_id, p_decision, p_reason, v_uid, p_before, p_after, coalesce(p_evidence_ids, '{}'))
  returning id into v_id;

  update public.review_cases
     set state = case when p_decision = 'dismiss' then 'dismissed' else 'resolved' end,
         resolution_id = v_id
   where id = p_case_id;

  insert into public.jobs (kind, idempotency_key, payload)
  values ('maintenance.apply_resolution', v_id::text, jsonb_build_object('resolution_id', v_id));

  perform public.write_audit(v_uid, 'review.resolve', 'review_case', p_case_id::text, p_before,
    jsonb_build_object('decision', p_decision, 'after', p_after, 'resolution_id', v_id), p_reason);
  return v_id;
end
$$;

-- Revertir es una decisión nueva auditada; el caso vuelve a quedar abierto.
create or replace function public.review_revert(p_resolution_id uuid, p_reason text)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid;
  v_prev public.review_resolutions%rowtype;
  v_id   uuid;
begin
  v_uid := public.require_app_role(array['reviewer', 'admin']);
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  select * into v_prev from public.review_resolutions where id = p_resolution_id;
  if not found then
    raise exception using errcode = 'P0002', message = 'resolución inexistente';
  end if;
  if exists (select 1 from public.review_resolutions where reverts_resolution_id = p_resolution_id) then
    raise exception using errcode = 'BC004', message = 'la resolución ya fue revertida';
  end if;

  insert into public.review_resolutions (case_id, decision, reason, actor_id, before_value,
                                         after_value, reverts_resolution_id)
  values (v_prev.case_id, 'revert', p_reason, v_uid, v_prev.after_value, v_prev.before_value, p_resolution_id)
  returning id into v_id;

  update public.review_cases set state = 'open', resolution_id = v_id where id = v_prev.case_id;

  insert into public.jobs (kind, idempotency_key, payload)
  values ('maintenance.apply_resolution', v_id::text, jsonb_build_object('resolution_id', v_id));

  perform public.write_audit(v_uid, 'review.revert', 'review_case', v_prev.case_id::text,
    v_prev.after_value, v_prev.before_value, p_reason);
  return v_id;
end
$$;

-- Retención (SRS-N05): texto de consultas/respuestas 30 días, contexto de chat 24 h,
-- actualizaciones de Telegram 30 días, auditoría 365 días.
create or replace function public.maintenance_purge_expired()
returns table (questions integer, answers integer, contexts integer, updates integer, audit_rows integer)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_q integer; v_a integer; v_c integer; v_u integer; v_l integer;
begin
  perform public.require_app_role(array['ingest_service', 'query_service', 'operator']);

  update public.query_runs set question_text = null
   where expires_at < now() and question_text is not null;
  get diagnostics v_q = row_count;

  delete from public.answer_records where expires_at < now();
  get diagnostics v_a = row_count;

  delete from public.chat_contexts where expires_at < now();
  get diagnostics v_c = row_count;

  delete from public.telegram_updates where received_at < now() - interval '30 days';
  get diagnostics v_u = row_count;

  delete from public.audit_log where occurred_at < now() - interval '365 days';
  get diagnostics v_l = row_count;

  return query select v_q, v_a, v_c, v_u, v_l;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Recuperación (SRS-F18, §9.2). SECURITY INVOKER: aplica RLS y privilegios del llamante.
-- Excluye chunks de baja calidad y revisiones retiradas antes de construir contexto.
-- ---------------------------------------------------------------------------------------------

create or replace function public.search_chunks_lexical(
  p_query      text,
  p_project_id uuid default null,
  p_limit      integer default 30
)
returns table (
  chunk_id       uuid,
  score          real,
  revision_id    uuid,
  extraction_id  uuid,
  pdf_page_start integer,
  pdf_page_end   integer,
  section_label  text
)
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select c.id, ts_rank_cd(c.search_vector, q.tsq), e.revision_id, c.extraction_id,
         c.pdf_page_start, c.pdf_page_end, c.section_label
    from public.chunks c
    join public.extraction_runs e on e.id = c.extraction_id
    join public.document_revisions r on r.id = e.revision_id
   cross join (select websearch_to_tsquery('spanish'::regconfig, p_query) as tsq) q
   where c.search_vector @@ q.tsq
     and c.quality_status = 'accepted'
     and r.withdrawn_at is null
     and (p_project_id is null or exists (
           select 1 from public.chunk_project_links l
            where l.chunk_id = c.id and l.project_id = p_project_id))
   order by 2 desc, c.id
   limit least(greatest(coalesce(p_limit, 30), 1), 100)
$$;

-- Búsqueda exacta por distancia coseno dentro de un modelo/versión. Cuando se elija el modelo
-- (DEC-06) se añadirá un índice HNSW parcial y una variante que use su expresión.
create or replace function public.search_chunks_vector(
  p_embedding     vector,
  p_model_id      text,
  p_model_version text,
  p_project_id    uuid default null,
  p_limit         integer default 30
)
returns table (
  chunk_id       uuid,
  distance       double precision,
  revision_id    uuid,
  extraction_id  uuid,
  pdf_page_start integer,
  pdf_page_end   integer,
  section_label  text
)
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select c.id, (ce.embedding <=> p_embedding)::double precision, e.revision_id, c.extraction_id,
         c.pdf_page_start, c.pdf_page_end, c.section_label
    from public.chunk_embeddings ce
    join public.chunks c on c.id = ce.chunk_id
    join public.extraction_runs e on e.id = c.extraction_id
    join public.document_revisions r on r.id = e.revision_id
   where ce.model_id = p_model_id
     and ce.model_version = p_model_version
     and ce.state = 'indexed'
     and ce.dimensions = vector_dims(p_embedding)
     and c.quality_status = 'accepted'
     and r.withdrawn_at is null
     and (p_project_id is null or exists (
           select 1 from public.chunk_project_links l
            where l.chunk_id = c.id and l.project_id = p_project_id))
   order by ce.embedding <=> p_embedding, c.id
   limit least(greatest(coalesce(p_limit, 30), 1), 100)
$$;
