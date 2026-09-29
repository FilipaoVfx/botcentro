-- Normalización idempotente con marca explícita por observación.
--
-- Defecto corregido: los pasos detectaban «ya normalizada» por el `observation_id` guardado en la
-- entidad. Cuando varias observaciones comparten identidad (SIRI publica una fila por sanción con el
-- mismo número; SECOP puede repetir contenido), el upsert reasignaba `observation_id` a la última y
-- la anterior volvía a elegirse en el siguiente lote: en SIRI eso creó una afirmación nueva por
-- vuelta (~318 mil filas en estado `pending_review`, nunca publicadas ni revisadas).
--
-- Modelo SIRI corregido: un expediente por número SIRI; una afirmación y una participación por
-- persona y expediente; cada sanción distinta es una actuación del expediente; las filas repetidas
-- solo añaden su evidencia a la afirmación existente.

create table public.normalized_observations (
  observation_id uuid primary key references public.observations (id),
  step           text not null,
  processed_at   timestamptz not null default now()
);
alter table public.normalized_observations enable row level security;
revoke all on public.normalized_observations from public, anon, authenticated;

-- La limpieza de las filas duplicadas vive en ops/limpieza-siri-duplicados.sql: es un borrado masivo
-- que requiere autorización expresa del responsable y se ejecuta por partes (límite de 30 s).
-- Con la función corregida, las afirmaciones existentes se reutilizan y no se crean más.

-- Lo ya normalizado correctamente por los otros pasos queda marcado.
insert into public.normalized_observations (observation_id, step)
select observation_id, 'territories' from public.territories where observation_id is not null
union select observation_id, 'contracts' from public.contract_versions where observation_id is not null
union select observation_id, 'documents' from public.official_documents where observation_id is not null
on conflict do nothing;

create or replace function public.normalize_investigations(p_step text, p_limit integer default 500) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  r         record;
  v_n       integer := 0;
  v_source  uuid;
  v_dept    uuid;
  v_terr    uuid;
  v_entity  uuid;
  v_party   uuid;
  v_contract uuid;
  v_version uuid;
  v_claim   uuid;
  v_proc    uuid;
  v_hash    text;
begin
  perform public.require_app_role(array['ingest_service']);

  if p_step = 'territories' then
    for r in
      select o.id, o.value_json v from public.observations o join public.sources s on s.id = o.source_id and s.code = 'SRC-20'
       where o.predicate = 'territory' and o.status = 'published'
         and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
       limit p_limit
    loop
      insert into public.territories (code, level, name, normalized_name)
      values (r.v ->> 'department_code', 'departamento', r.v ->> 'department', public.place_key(r.v ->> 'department'))
      on conflict (code) do update set name = excluded.name, normalized_name = excluded.normalized_name
      returning id into v_dept;
      insert into public.territories (code, level, parent_id, name, normalized_name, kind, observation_id)
      values (r.v ->> 'code', 'municipio', v_dept, r.v ->> 'name', public.place_key(r.v ->> 'name'), r.v ->> 'kind', r.id)
      on conflict (code) do update set name = excluded.name, normalized_name = excluded.normalized_name,
                                        parent_id = excluded.parent_id, kind = excluded.kind, observation_id = excluded.observation_id;
      insert into public.normalized_observations (observation_id, step) values (r.id, p_step);
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'territories', v_n);

  elsif p_step = 'contracts' then
    for r in
      select o.id, o.source_id, o.value_json v, o.last_observed_at from public.observations o
        join public.sources s on s.id = o.source_id and s.code in ('SRC-15', 'SRC-17')
       where o.predicate = 'contract_snapshot' and o.status = 'published'
         and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
       order by o.last_observed_at
       limit p_limit
    loop
      v_terr := public.territory_match(r.v -> 'entity' ->> 'departamento', r.v -> 'entity' ->> 'municipio');
      v_entity := public.actor_by_identifier('entidad_publica', r.v -> 'entity' ->> 'name', 'DIAN', 'NIT', null,
                                             r.v -> 'entity' ->> 'nit', r.v -> 'entity' ->> 'nit', v_terr, r.source_id, r.id);
      v_party := case r.v -> 'contractor' ->> 'kind'
                   when 'persona' then public.actor_by_identifier('persona', r.v -> 'contractor' ->> 'name', 'SECOP',
                          r.v -> 'contractor' ->> 'id_type', r.v -> 'contractor' ->> 'id_hmac', null,
                          r.v -> 'contractor' ->> 'id_masked', null, r.source_id, r.id)
                   when 'organizacion' then public.actor_by_identifier('organizacion_privada', r.v -> 'contractor' ->> 'name',
                          'DIAN', 'NIT', null, r.v -> 'contractor' ->> 'id_public', r.v -> 'contractor' ->> 'id_masked',
                          null, r.source_id, r.id)
                 end;
      insert into public.contracts (source_id, native_id, entity_actor_id, contractor_actor_id, territory_id, currency,
                                    object, official_url)
      values (r.source_id, r.v ->> 'native_id', v_entity, v_party, v_terr, coalesce(r.v ->> 'currency', 'COP'),
              r.v ->> 'object', r.v ->> 'url')
      on conflict (source_id, native_id) do update
         set entity_actor_id = coalesce(excluded.entity_actor_id, contracts.entity_actor_id),
             contractor_actor_id = coalesce(excluded.contractor_actor_id, contracts.contractor_actor_id),
             territory_id = coalesce(excluded.territory_id, contracts.territory_id),
             object = coalesce(excluded.object, contracts.object), official_url = coalesce(excluded.official_url, contracts.official_url)
      returning id into v_contract;
      v_hash := encode(sha256(convert_to((r.v - 'updated_on')::text, 'UTF8')), 'hex');
      insert into public.contract_versions (contract_id, content_hash, status_original, signed_on, starts_on, ends_on,
                                            value_initial, value_additions, value_paid, value_current, modality,
                                            contract_type, observation_id)
      values (v_contract, v_hash, r.v ->> 'status', nullif(r.v ->> 'signed_on', '')::date,
              nullif(r.v ->> 'starts_on', '')::date, nullif(r.v ->> 'ends_on', '')::date,
              nullif(r.v ->> 'value_initial', '')::numeric, nullif(r.v ->> 'value_additions', '')::numeric,
              nullif(r.v ->> 'value_paid', '')::numeric,
              coalesce(nullif(r.v ->> 'value_current', '')::numeric, nullif(r.v ->> 'value_initial', '')::numeric),
              r.v ->> 'modality', r.v ->> 'contract_type', r.id)
      on conflict (contract_id, content_hash) do nothing
      returning id into v_version;
      if v_version is not null then
        update public.contracts set current_version_id = v_version where id = v_contract;
      end if;
      insert into public.normalized_observations (observation_id, step) values (r.id, p_step);
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'contract_observations', v_n);

  elsif p_step = 'siri' then
    select id into v_source from public.sources where code = 'SRC-18';
    for r in
      select o.id, o.value_json v, o.last_observed_at from public.observations o
       where o.source_id = v_source and o.predicate = 'siri_record' and o.status = 'published'
         and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
       order by o.last_observed_at
       limit p_limit
    loop
      v_terr := public.territory_match(r.v -> 'place' ->> 'departamento', r.v -> 'place' ->> 'municipio');
      insert into public.normalized_observations (observation_id, step) values (r.id, p_step);
      v_n := v_n + 1;
      v_party := public.actor_by_identifier('persona', r.v -> 'person' ->> 'name', 'RNEC', r.v -> 'person' ->> 'id_type',
                                            r.v -> 'person' ->> 'id_hmac', null, r.v -> 'person' ->> 'id_masked', null,
                                            v_source, r.id);
      if v_party is null then
        continue;  -- sin documento no se atribuye a nadie
      end if;
      select id into v_proc from public.proceedings where system = 'SIRI' and source_identifier = r.v ->> 'native_id';
      if v_proc is null then
        insert into public.proceedings (system, source_identifier, authority, jurisdiction, source_id, radicado_original,
                                        status_original, status_normalized, finality, territory_id, observation_id,
                                        last_verified_at)
        values ('SIRI', r.v ->> 'native_id', coalesce(r.v ->> 'authority', 'Procuraduría General de la Nación'),
                case upper(coalesce(r.v ->> 'record_type', ''))
                  when 'DISCIPLINARIO' then 'disciplinaria' when 'PENAL' then 'penal' when 'FISCAL' then 'fiscal'
                  when 'CONTRACTUAL' then 'administrativa' else 'otra' end,
                v_source, r.v ->> 'proceeding_number', r.v ->> 'sanction', 'sancion_registrada', 'desconocida', v_terr, r.id,
                r.last_observed_at)
        returning id into v_proc;
      else
        update public.proceedings set last_verified_at = greatest(last_verified_at, r.last_observed_at) where id = v_proc;
      end if;
      select claim_id into v_claim from public.participations where actor_id = v_party and proceeding_id = v_proc;
      if v_claim is null then
        insert into public.claims (claim_type, statement, subject_type, subject_id, object_type, object_id, value, sensitive,
                                   origin, state, author_label, submitted_at)
        values ('participation',
                format('SIRI registra el antecedente %s de %s (%s) por %s. Las sanciones figuran como actuaciones del registro.',
                       r.v ->> 'native_id', r.v -> 'person' ->> 'name', coalesce(r.v ->> 'position', 'cargo no informado'),
                       coalesce(r.v ->> 'authority', 'autoridad no informada')),
                'actor', v_party, 'proceeding', v_proc,
                jsonb_build_object('source', 'SRC-18', 'numero_siri', r.v ->> 'native_id', 'record_type', r.v ->> 'record_type'),
                true, 'system_ingest', 'pending_review', 'ingesta SIRI (automática)', now())
        returning id into v_claim;
        insert into public.participations (actor_id, proceeding_id, role_original, role_normalized, claim_id)
        values (v_party, v_proc, coalesce(r.v ->> 'sanction', 'sancionado'), 'sancionado', v_claim);
        insert into public.editorial_reviews (object_type, object_id, version, decision, actor_label)
        values ('claim', v_claim, 1, 'submitted', 'ingesta SIRI (automática)');
        update public.proceedings set claim_id = coalesce(claim_id, v_claim) where id = v_proc;
      end if;
      insert into public.claim_evidence (claim_id, evidence_id)
      select v_claim, oe.evidence_id from public.observation_evidence oe where oe.observation_id = r.id
      on conflict do nothing;
      if not exists (select 1 from public.proceeding_events e
                      where e.proceeding_id = v_proc and e.event_type = 'efectos_juridicos'
                        and e.description = format('Sanción con efectos jurídicos: %s', coalesce(r.v ->> 'sanction', ''))
                        and e.occurred_on is not distinct from nullif(r.v ->> 'effects_on', '')::date) then
        insert into public.proceeding_events (proceeding_id, event_type, description, occurred_on, date_precision,
                                              observation_id, claim_id)
        values (v_proc, 'efectos_juridicos', format('Sanción con efectos jurídicos: %s', coalesce(r.v ->> 'sanction', '')),
                nullif(r.v ->> 'effects_on', '')::date,
                case when nullif(r.v ->> 'effects_on', '') is null then 'unknown' else 'day' end, r.id, v_claim);
      end if;
    end loop;
    return jsonb_build_object('step', p_step, 'siri_records', v_n);

  elsif p_step = 'documents' then
    select id into v_source from public.sources where code = 'SRC-19';
    with pick as (
      select o.id, o.value_json v from public.observations o
       where o.source_id = v_source and o.predicate = 'official_document' and o.status = 'published'
         and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
       order by o.last_observed_at
       limit p_limit
    ), docs as (
      insert into public.official_documents (source_id, native_id, doc_type, number, dependency, topic, subtopic, url, doc_date,
                                             observation_id)
      select distinct on (v ->> 'native_id') v_source, v ->> 'native_id', v ->> 'doc_type', v ->> 'number', v ->> 'dependency',
             v ->> 'topic', v ->> 'subtopic', v ->> 'url', nullif(v ->> 'doc_date', '')::date, id
        from pick order by v ->> 'native_id'
      on conflict (source_id, native_id) do update set observation_id = excluded.observation_id
      returning 1
    )
    insert into public.normalized_observations (observation_id, step) select id, p_step from pick;
    get diagnostics v_n = row_count;
    return jsonb_build_object('step', p_step, 'documents', v_n);
  end if;
  raise exception using errcode = '22023', message = format('paso desconocido: %s', p_step);
end
$$;

revoke execute on function public.normalize_investigations(text, integer) from public, anon;
grant execute on function public.normalize_investigations(text, integer) to authenticated;
