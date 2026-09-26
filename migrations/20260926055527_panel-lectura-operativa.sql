-- Panel de operación: lectura operativa (srs-panel-web.md §4, §11, §13; PW-01..PW-13).
--
-- Las funciones ops_* son SECURITY INVOKER: RLS y privilegios del operador deciden qué ve.
-- Una sección cuyo rol no alcanza se devuelve como {"access": false}, nunca como ceros
-- (PRD del panel §13: «0 pendientes» solo con medición válida). Todas incluyen as_of.
-- Capacidades sin instrumentación todavía (workers, intentos, alertas, incidentes, logs)
-- se declaran como no instrumentadas en ops_capabilities.

-- ---------------------------------------------------------------------------------------------
-- Rol inicial por email verificado
-- ---------------------------------------------------------------------------------------------
-- Permite registrar el rol de alguien antes de que exista su usuario (el login por código crea
-- el usuario al verificar). Solo se reclama con el email verificado de la sesión.

create table public.pending_role_grants (
  email      text not null check (email = lower(btrim(email)) and email like '%@%'),
  role       text not null
    check (role in ('ingest_service', 'query_service', 'reviewer', 'operator', 'admin')),
  reason     text not null check (length(btrim(reason)) >= 5),
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  claimed_by uuid references auth.users (id),
  primary key (email, role),
  constraint pending_role_grants_claim check ((claimed_at is null) = (claimed_by is null))
);

create index pending_role_grants_claimed_by_fk_idx on public.pending_role_grants (claimed_by);
alter table public.pending_role_grants enable row level security;
revoke all on public.pending_role_grants from anon, authenticated;

create or replace function public.claim_pending_roles() returns text[]
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid   uuid := (select auth.uid());
  v_email text;
  v_roles text[];
begin
  if v_uid is null then
    return '{}';
  end if;
  select lower(u.email) into v_email from auth.users u where u.id = v_uid and u.email_verified;
  if v_email is null then
    return '{}';
  end if;

  with claimed as (
    update public.pending_role_grants g
       set claimed_at = now(), claimed_by = v_uid
     where g.email = v_email and g.claimed_at is null
    returning g.role, g.reason
  ), granted as (
    insert into public.app_roles (user_id, role, note)
    select v_uid, c.role, c.reason from claimed c
    on conflict (user_id, role) do nothing
    returning role
  )
  select coalesce(array_agg(role), '{}') into v_roles from granted;

  if cardinality(v_roles) > 0 then
    perform public.write_audit(v_uid, 'role.claim_pending', 'user', v_uid::text, null,
      jsonb_build_object('roles', v_roles), 'rol asignado previamente por email verificado');
  end if;
  return v_roles;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Helpers de lectura
-- ---------------------------------------------------------------------------------------------

-- Capacidades y permisos efectivos (srs-panel-web.md §13.1 GET /capabilities).
create or replace function public.ops_capabilities() returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select jsonb_build_object(
    'as_of', now(),
    'user_id', (select auth.uid()),
    'roles', coalesce((select array_agg(r.role order by r.role) from public.app_roles r
                       where r.user_id = (select auth.uid())), '{}'),
    'is_admin', public.has_app_role(array['admin']),
    'views', jsonb_build_object(
      'ops', public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin']),
      'queries', public.has_app_role(array['query_service', 'admin']),
      'telegram', public.has_app_role(array['query_service', 'operator', 'admin']),
      'review', public.has_app_role(array['reviewer', 'operator', 'admin']),
      'costs', public.has_app_role(array['ingest_service', 'query_service', 'operator', 'admin']),
      'audit', public.has_app_role(array['admin'])
    ),
    'actions_enabled', false,
    'actions_reason', 'Las acciones operativas llegan en el hito P-H3; esta versión es de solo lectura.',
    'not_instrumented', jsonb_build_array(
      jsonb_build_object('capability', 'workers', 'reason', 'Aún no hay workers desplegados que emitan heartbeat.'),
      jsonb_build_object('capability', 'job_attempts', 'reason', 'El historial por intento no está instrumentado; solo el intento vigente en jobs.'),
      jsonb_build_object('capability', 'alerts', 'reason', 'El evaluador de alertas no está implementado.'),
      jsonb_build_object('capability', 'incidents', 'reason', 'La gestión de incidentes no está implementada.'),
      jsonb_build_object('capability', 'logs', 'reason', 'No hay recolección de logs saneados conectada.'),
      jsonb_build_object('capability', 'latency', 'reason', 'Aún no hay consultas registradas con latencia.')
    )
  )
$$;

-- ---------------------------------------------------------------------------------------------
-- Resumen: una fila por etapa del pipeline (PW-02, PW-03)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_overview() returns jsonb
language plpgsql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_ops boolean := public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin']);
  v_q   boolean := public.has_app_role(array['query_service', 'admin']);
  v_tg  boolean := public.has_app_role(array['query_service', 'operator', 'admin']);
  v_rev boolean := public.has_app_role(array['reviewer', 'operator', 'admin', 'ingest_service']);
  v_cost boolean := public.has_app_role(array['ingest_service', 'query_service', 'operator', 'admin']);
  stages jsonb := '[]';
  attention jsonb := '[]';
  r record;
begin
  if not v_ops then
    return jsonb_build_object('as_of', now(), 'access', false);
  end if;

  -- Adquisición
  select count(*) filter (where status = 'running') as running,
         count(*) filter (where status in ('failed', 'partial') and started_at > now() - interval '24 hours') as failed,
         count(*) as total,
         max(greatest(started_at, coalesce(finished_at, started_at))) as last_movement
    into r from public.ingestion_runs;
  stages := stages || jsonb_build_object(
    'stage', 'acquisition', 'label', 'Adquisición', 'access', true, 'total', r.total,
    'pending', (select count(*) from public.jobs where kind like 'ingest.%' and state in ('pending', 'retry_wait')),
    'running', r.running, 'failed', r.failed, 'last_movement', r.last_movement, 'unit', 'ejecuciones');

  -- Normalización y evidencia
  select count(*) filter (where status = 'candidate') as pending,
         count(*) filter (where status = 'quarantined') as failed,
         count(*) as total, max(updated_at) as last_movement
    into r from public.observations;
  stages := stages || jsonb_build_object(
    'stage', 'normalization', 'label', 'Normalización', 'access', true, 'total', r.total,
    'pending', r.pending, 'running', null, 'failed', r.failed, 'last_movement', r.last_movement,
    'unit', 'observaciones');

  -- Documentos
  select count(*) filter (where status = 'pending') as pending,
         count(*) filter (where status = 'running') as running,
         count(*) filter (where status = 'failed' or quality_status = 'failed') as failed,
         count(*) as total, max(greatest(created_at, coalesce(finished_at, created_at))) as last_movement
    into r from public.extraction_runs;
  stages := stages || jsonb_build_object(
    'stage', 'documents', 'label', 'Documentos', 'access', true, 'total', r.total,
    'pending', r.pending, 'running', r.running, 'failed', r.failed, 'last_movement', r.last_movement,
    'unit', 'extracciones');

  -- Índice
  select count(*) filter (where state = 'pending') as pending,
         count(*) filter (where state = 'stale') as failed,
         count(*) as total, max(coalesce(indexed_at, created_at)) as last_movement
    into r from public.chunk_embeddings;
  stages := stages || jsonb_build_object(
    'stage', 'index', 'label', 'Índice', 'access', true, 'total', r.total,
    'pending', r.pending, 'running', null, 'failed', r.failed, 'last_movement', r.last_movement,
    'unit', 'embeddings');

  -- Consultas
  if v_q then
    select count(*) filter (where status = 'queued') as pending,
           count(*) filter (where status = 'running') as running,
           count(*) filter (where status = 'failed' and created_at > now() - interval '24 hours') as failed,
           count(*) as total, max(coalesce(finished_at, created_at)) as last_movement
      into r from public.query_runs;
    stages := stages || jsonb_build_object(
      'stage', 'queries', 'label', 'Consultas', 'access', true, 'total', r.total,
      'pending', r.pending, 'running', r.running, 'failed', r.failed, 'last_movement', r.last_movement,
      'unit', 'consultas');
  else
    stages := stages || jsonb_build_object('stage', 'queries', 'label', 'Consultas', 'access', false);
  end if;

  -- Entrega Telegram
  if v_tg then
    select count(*) filter (where state = 'pending') as pending,
           count(*) filter (where state = 'uncertain') as uncertain,
           count(*) filter (where state = 'failed') as failed,
           count(*) as total, max(updated_at) as last_movement
      into r from public.delivery_attempts;
    stages := stages || jsonb_build_object(
      'stage', 'delivery', 'label', 'Entrega', 'access', true, 'total', r.total,
      'pending', r.pending, 'running', null, 'failed', r.failed, 'uncertain', r.uncertain,
      'last_movement', r.last_movement, 'unit', 'segmentos');
  else
    stages := stages || jsonb_build_object('stage', 'delivery', 'label', 'Entrega', 'access', false);
  end if;

  -- Revisión
  if v_rev then
    select count(*) filter (where state = 'open') as pending,
           count(*) filter (where state = 'in_review') as running,
           count(*) as total, max(updated_at) as last_movement
      into r from public.review_cases;
    stages := stages || jsonb_build_object(
      'stage', 'review', 'label', 'Revisión', 'access', true, 'total', r.total,
      'pending', r.pending, 'running', r.running, 'failed', null, 'last_movement', r.last_movement,
      'unit', 'casos');
  else
    stages := stages || jsonb_build_object('stage', 'review', 'label', 'Revisión', 'access', false);
  end if;

  -- Requiere atención (srs-panel-web.md §9.1): impacto, origen, antigüedad.
  attention := attention || coalesce((
    select jsonb_agg(jsonb_build_object(
      'severity', 'critical', 'kind', 'dead_letter', 'resource', j.kind, 'resource_id', j.id,
      'title', 'Trabajo en dead-letter', 'detail', coalesce(j.last_error_code, 'sin código'), 'since', j.updated_at)
      order by j.updated_at desc)
      from (select * from public.jobs where state = 'dead_letter' order by updated_at desc limit 10) j), '[]');
  attention := attention || coalesce((
    select jsonb_agg(jsonb_build_object(
      'severity', 'warning', 'kind', 'run_failed', 'resource', s.code, 'resource_id', ir.id,
      'title', 'Ingesta ' || ir.status, 'detail', coalesce(ir.error_code, 'sin código'), 'since', ir.finished_at)
      order by ir.finished_at desc)
      from public.ingestion_runs ir join public.sources s on s.id = ir.source_id
     where ir.status in ('failed', 'partial') and ir.finished_at > now() - interval '24 hours'), '[]');
  attention := attention || coalesce((
    select jsonb_agg(jsonb_build_object(
      'severity', 'warning', 'kind', 'source_suspended', 'resource', s.code, 'resource_id', s.id,
      'title', 'Fuente suspendida', 'detail', coalesce(s.state_reason, 'sin motivo'), 'since', s.updated_at))
      from public.sources s where s.state in ('suspended', 'degraded')), '[]');
  if v_cost and not exists (select 1 from public.budgets where active) then
    attention := attention || jsonb_build_object(
      'severity', 'info', 'kind', 'no_budget', 'resource', 'presupuesto', 'resource_id', null,
      'title', 'Sin presupuesto configurado',
      'detail', 'Toda operación cobrable (OCR, embeddings, generación) queda bloqueada hasta definirlo (DEC-07).',
      'since', null);
  end if;
  attention := attention || coalesce((
    select jsonb_build_object(
      'severity', 'info', 'kind', 'sources_pending_discovery', 'resource', 'fuentes', 'resource_id', null,
      'title', count(*) || ' fuentes pendientes de descubrimiento',
      'detail', 'Candidatas sin perfil de uso validado; no se ingiere nada de ellas (H0).', 'since', min(created_at))
      from public.sources where state = 'candidate' having count(*) > 0), '[]');

  return jsonb_build_object(
    'as_of', now(),
    'access', true,
    'stages', stages,
    'attention', attention,
    'sources', (select jsonb_build_object(
        'total', count(*),
        'by_state', coalesce(jsonb_object_agg(state, n), '{}'))
      from (select state, count(*) as n from public.sources group by state) x),
    'jobs', (select jsonb_build_object(
        'ready', count(*) filter (where state in ('pending', 'retry_wait') and run_at <= now()),
        'scheduled', count(*) filter (where state in ('pending', 'retry_wait') and run_at > now()),
        'leased', count(*) filter (where state = 'leased'),
        'lease_expired', count(*) filter (where state = 'leased' and lease_until < now()),
        'dead_letter', count(*) filter (where state = 'dead_letter'),
        'oldest_ready_at', min(run_at) filter (where state in ('pending', 'retry_wait') and run_at <= now()))
      from public.jobs)
  );
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Fuentes (PW-04)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_sources() returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object('as_of', now(), 'access', true, 'items', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', s.id, 'code', s.code, 'name', s.name, 'authority', s.authority, 'phase', s.phase,
        'state', s.state, 'state_reason', s.state_reason, 'base_url', s.base_url,
        'supported_objects', s.supported_objects, 'owner', s.owner,
        'last_checked_at', s.last_checked_at, 'last_success_at', s.last_success_at,
        'poll_interval_seconds', extract(epoch from s.poll_interval),
        'policy_version', p.version, 'policy_reviewed_at', p.reviewed_at,
        'coverage_scopes', (select count(*) from public.coverage_scopes c where c.source_id = s.id),
        'last_run', (select jsonb_build_object('id', ir.id, 'status', ir.status, 'mode', ir.mode,
                                              'started_at', ir.started_at, 'finished_at', ir.finished_at,
                                              'error_code', ir.error_code)
                       from public.ingestion_runs ir where ir.source_id = s.id
                      order by ir.started_at desc limit 1),
        'last_change_at', (select max(sn.created_at) from public.source_snapshots sn
                             join public.source_records sr on sr.id = sn.record_id where sr.source_id = s.id),
        'records', (select count(*) from public.source_records sr where sr.source_id = s.id))
        order by s.code)
      from public.sources s left join public.source_policies p on p.id = s.policy_id), '[]'))
  end
$$;

create or replace function public.ops_source(p_id uuid) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else (select jsonb_build_object(
      'as_of', now(), 'access', true,
      'source', jsonb_build_object(
        'id', s.id, 'code', s.code, 'name', s.name, 'authority', s.authority, 'phase', s.phase,
        'state', s.state, 'state_reason', s.state_reason, 'base_url', s.base_url,
        'allowed_domains', s.allowed_domains, 'adapter', s.adapter, 'adapter_version', s.adapter_version,
        'supported_objects', s.supported_objects, 'owner', s.owner, 'notes', s.notes,
        'request_timeout_seconds', s.request_timeout_seconds, 'max_concurrency', s.max_concurrency,
        'poll_interval_seconds', extract(epoch from s.poll_interval),
        'last_checked_at', s.last_checked_at, 'last_success_at', s.last_success_at,
        'created_at', s.created_at, 'updated_at', s.updated_at),
      'policy', (select to_jsonb(p) - 'source_id' from public.source_policies p where p.id = s.policy_id),
      'policy_versions', (select count(*) from public.source_policies p where p.source_id = s.id),
      'coverage', coalesce((select jsonb_agg(to_jsonb(c) order by c.object_type)
                              from public.coverage_status c where c.source_code = s.code), '[]'),
      'runs', coalesce((select jsonb_agg(jsonb_build_object(
                  'id', ir.id, 'mode', ir.mode, 'status', ir.status, 'started_at', ir.started_at,
                  'finished_at', ir.finished_at, 'items_discovered', ir.items_discovered,
                  'items_fetched', ir.items_fetched, 'items_unchanged', ir.items_unchanged,
                  'items_failed', ir.items_failed, 'error_code', ir.error_code) order by ir.started_at desc)
                from (select * from public.ingestion_runs where source_id = s.id
                       order by started_at desc limit 20) ir), '[]'),
      'open_cases', (select count(*) from public.review_cases rc
                      join public.ingestion_runs ir on ir.id = rc.related_run_id
                     where ir.source_id = s.id and rc.state in ('open', 'in_review')))
      from public.sources s where s.id = p_id)
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Ingestas (PW-05)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_runs(p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object('as_of', now(), 'access', true, 'items', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', ir.id, 'source_code', s.code, 'source_name', s.name, 'mode', ir.mode, 'status', ir.status,
        'connector_version', ir.connector_version, 'started_at', ir.started_at, 'finished_at', ir.finished_at,
        'duration_ms', (extract(epoch from (coalesce(ir.finished_at, now()) - ir.started_at)) * 1000)::bigint,
        'items_discovered', ir.items_discovered, 'items_fetched', ir.items_fetched,
        'items_unchanged', ir.items_unchanged, 'items_normalized', ir.items_normalized,
        'items_quarantined', ir.items_quarantined, 'items_failed', ir.items_failed,
        'error_code', ir.error_code, 'trace_id', ir.trace_id,
        'derived_jobs_open', (select count(*) from public.jobs j where j.run_id = ir.id
                                and j.state in ('pending', 'retry_wait', 'leased')))
        order by ir.started_at desc)
      from (select * from public.ingestion_runs order by started_at desc
             limit least(greatest(coalesce(p_limit, 50), 1), 100)) ir
      join public.sources s on s.id = ir.source_id), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Procesos, colas y dead-letter (PW-06, PW-07)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_jobs(p_state text default null, p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object(
      'as_of', now(), 'access', true,
      'queues', coalesce((
        select jsonb_agg(jsonb_build_object(
          'family', q.family,
          'ready', q.ready, 'scheduled', q.scheduled, 'leased', q.leased, 'lease_expired', q.lease_expired,
          'retry_wait', q.retry_wait, 'dead_letter', q.dead_letter, 'succeeded_24h', q.succeeded_24h,
          'oldest_ready_at', q.oldest_ready_at) order by q.family)
        from (select split_part(kind, '.', 1) as family,
                     count(*) filter (where state = 'pending' and run_at <= now()) as ready,
                     count(*) filter (where state in ('pending', 'retry_wait') and run_at > now()) as scheduled,
                     count(*) filter (where state = 'leased') as leased,
                     count(*) filter (where state = 'leased' and lease_until < now()) as lease_expired,
                     count(*) filter (where state = 'retry_wait' and run_at <= now()) as retry_wait,
                     count(*) filter (where state = 'dead_letter') as dead_letter,
                     count(*) filter (where state = 'succeeded' and finished_at > now() - interval '24 hours') as succeeded_24h,
                     min(run_at) filter (where state in ('pending', 'retry_wait') and run_at <= now()) as oldest_ready_at
                from public.jobs group by 1) q), '[]'),
      'items', coalesce((
        select jsonb_agg(jsonb_build_object(
          'id', j.id, 'kind', j.kind, 'state', j.state, 'attempts', j.attempts, 'max_attempts', j.max_attempts,
          'priority', j.priority, 'run_at', j.run_at, 'lease_owner', j.lease_owner, 'lease_until', j.lease_until,
          'lease_expired', (j.state = 'leased' and j.lease_until < now()),
          'last_error_code', j.last_error_code, 'created_at', j.created_at, 'updated_at', j.updated_at,
          'finished_at', j.finished_at, 'run_id', j.run_id, 'trace_id', j.trace_id)
          order by j.updated_at desc)
        from (select * from public.jobs
               where p_state is null or state = p_state
               order by updated_at desc limit least(greatest(coalesce(p_limit, 50), 1), 100)) j), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Documentos e índice (PW-08)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_documents(p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object(
      'as_of', now(), 'access', true,
      'totals', jsonb_build_object(
        'documents', (select count(*) from public.documents),
        'revisions', (select count(*) from public.document_revisions),
        'withdrawn', (select count(*) from public.document_revisions where withdrawn_at is not null),
        'extractions', (select count(*) from public.extraction_runs),
        'pages', (select count(*) from public.document_pages),
        'pages_ocr', (select count(*) from public.document_pages where method = 'ocr'),
        'chunks', (select count(*) from public.chunks),
        'embeddings_indexed', (select count(*) from public.chunk_embeddings where state = 'indexed'),
        'embeddings_pending', (select count(*) from public.chunk_embeddings where state = 'pending')),
      'items', coalesce((
        select jsonb_agg(jsonb_build_object(
          'revision_id', r.id, 'document_key', d.document_key, 'document_type', d.document_type,
          'title', d.title, 'mime_type', r.mime_type, 'byte_size', r.byte_size, 'blob_hash', r.blob_hash,
          'published_on', r.published_on, 'withdrawn_at', r.withdrawn_at, 'created_at', r.created_at,
          'extraction', (select jsonb_build_object('status', e.status, 'quality', e.quality_status,
                                                   'page_count', e.page_count, 'pages_ocr', e.pages_ocr,
                                                   'extractor_version', e.extractor_version)
                           from public.extraction_runs e where e.revision_id = r.id
                          order by e.created_at desc limit 1),
          'chunks', (select count(*) from public.chunks c join public.extraction_runs e on e.id = c.extraction_id
                      where e.revision_id = r.id))
          order by r.created_at desc)
        from (select * from public.document_revisions order by created_at desc
               limit least(greatest(coalesce(p_limit, 50), 1), 100)) r
        join public.documents d on d.id = r.document_id), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Consultas y Telegram (PW-09). Sin texto de preguntas ni chats: solo metadata seudónima.
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_queries(p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['query_service', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object(
      'as_of', now(), 'access', true,
      'telegram', (select jsonb_build_object(
          'received_24h', count(*),
          'accepted_24h', count(*) filter (where status = 'accepted'),
          'rejected_unauthorized_24h', count(*) filter (where status = 'rejected_unauthorized'),
          'rejected_unsupported_24h', count(*) filter (where status = 'rejected_unsupported'),
          'rejected_rate_limited_24h', count(*) filter (where status = 'rejected_rate_limited'),
          'last_received_at', max(received_at))
        from public.telegram_updates where received_at > now() - interval '24 hours'),
      'authorized_identities', (select count(*) from public.telegram_identities where authorized),
      'items', coalesce((
        select jsonb_agg(jsonb_build_object(
          'id', q.id, 'principal', left(q.principal_id::text, 8), 'channel', q.channel, 'intent', q.intent,
          'status', q.status, 'latency_ms', q.latency_ms, 'created_at', q.created_at,
          'finished_at', q.finished_at, 'model_version', q.model_version, 'trace_id', q.trace_id,
          'support_status', (select a.support_status from public.answer_records a where a.query_id = q.id),
          'evidence_count', (select count(*) from public.query_evidence e where e.query_id = q.id),
          'delivery', (select jsonb_build_object(
                          'segments', count(*),
                          'sent', count(*) filter (where d.state = 'sent'),
                          'pending', count(*) filter (where d.state = 'pending'),
                          'failed', count(*) filter (where d.state = 'failed'),
                          'uncertain', count(*) filter (where d.state = 'uncertain'))
                         from public.delivery_attempts d join public.answer_records a on a.id = d.answer_id
                        where a.query_id = q.id))
          order by q.created_at desc)
        from (select * from public.query_runs order by created_at desc
               limit least(greatest(coalesce(p_limit, 50), 1), 100)) q), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Calidad y revisión (PW-10)
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_review_cases(p_state text default null, p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'reviewer', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object(
      'as_of', now(), 'access', true,
      'by_type', coalesce((select jsonb_object_agg(case_type, n) from (
          select case_type, count(*) as n from public.review_cases where state in ('open', 'in_review')
           group by case_type) x), '{}'),
      'items', coalesce((
        select jsonb_agg(jsonb_build_object(
          'id', c.id, 'case_type', c.case_type, 'state', c.state, 'summary', c.summary,
          'subject_type', c.subject_type, 'opened_at', c.opened_at, 'updated_at', c.updated_at,
          'evidence_count', cardinality(c.evidence_ids), 'related_run_id', c.related_run_id,
          'resolutions', (select count(*) from public.review_resolutions r where r.case_id = c.id))
          order by c.opened_at desc)
        from (select * from public.review_cases where p_state is null or state = p_state
               order by opened_at desc limit least(greatest(coalesce(p_limit, 50), 1), 100)) c), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Costos y presupuesto (PW-13): estimado, confirmado y reservas sin doble conteo.
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_costs() returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['ingest_service', 'query_service', 'operator', 'admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object(
      'as_of', now(), 'access', true,
      'budgets', coalesce((select jsonb_agg(jsonb_build_object(
          'id', b.id, 'name', b.name, 'provider', b.provider, 'period', b.period,
          'limit_amount', b.limit_amount::text, 'currency', b.currency, 'alert_ratio', b.alert_ratio::text,
          'hard_stop', b.hard_stop, 'active', b.active) order by b.name) from public.budgets b), '[]'),
      'by_provider', coalesce((select jsonb_agg(jsonb_build_object(
          'provider', provider, 'operation', operation,
          'settled', settled::text, 'reserved', reserved::text, 'count', n) order by provider, operation)
        from (select provider, operation,
                     coalesce(sum(settled_cost) filter (where state = 'settled'), 0) as settled,
                     coalesce(sum(estimated_cost) filter (where state = 'reserved'), 0) as reserved,
                     count(*) as n
                from public.usage_ledger
               where reserved_at >= date_trunc('month', now() at time zone 'America/Bogota') at time zone 'America/Bogota'
               group by provider, operation) x), '[]'),
      'month_start', date_trunc('month', now() at time zone 'America/Bogota') at time zone 'America/Bogota')
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Auditoría (PW-15). Solo administradores (RLS de audit_log).
-- ---------------------------------------------------------------------------------------------
create or replace function public.ops_audit(p_limit integer default 50) returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select case when not public.has_app_role(array['admin'])
    then jsonb_build_object('as_of', now(), 'access', false)
    else jsonb_build_object('as_of', now(), 'access', true, 'items', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', a.id, 'actor_id', a.actor_id, 'action', a.action,
        'target_type', a.target_type, 'target_id', a.target_id, 'reason', a.reason,
        'occurred_at', a.occurred_at) order by a.id desc)
      from (select * from public.audit_log order by id desc
             limit least(greatest(coalesce(p_limit, 50), 1), 100)) a), '[]'))
  end
$$;

-- ---------------------------------------------------------------------------------------------
-- Permisos de ejecución
-- ---------------------------------------------------------------------------------------------
revoke execute on function
  public.claim_pending_roles(), public.ops_capabilities(), public.ops_overview(), public.ops_sources(),
  public.ops_source(uuid), public.ops_runs(integer), public.ops_jobs(text, integer),
  public.ops_documents(integer), public.ops_queries(integer), public.ops_review_cases(text, integer),
  public.ops_costs(), public.ops_audit(integer)
from public, anon;

grant execute on function
  public.claim_pending_roles(), public.ops_capabilities(), public.ops_overview(), public.ops_sources(),
  public.ops_source(uuid), public.ops_runs(integer), public.ops_jobs(text, integer),
  public.ops_documents(integer), public.ops_queries(integer), public.ops_review_cases(text, integer),
  public.ops_costs(), public.ops_audit(integer)
to authenticated;
