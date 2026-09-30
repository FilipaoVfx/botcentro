-- SECOP II: concepto completo, versiones por estado y misión/visión de la entidad (auditoría 2026-09-30).
--
-- * `descripcion_del_proceso` llega cortada a 300 caracteres en la fuente; el parser usa ahora
--   `objeto_del_contrato` (completo). El texto descriptivo se actualiza en el contrato sin crear versión.
-- * Una versión de contrato representa un cambio de ESTADO (estado, fechas, valores, modalidad, tipo,
--   identidad del contratista): contract_state_hash(). Los hashes existentes se recalculan con esa regla;
--   ningún contrato estaba publicado (fuente en modo sombra), así que no hay avisos que corregir.
-- * SRC-25 (PAA encabezado, b6m4-qgqv): misión, visión (perspectiva estratégica) y presupuesto de cada
--   entidad por año, enlazados por el código de entidad de SECOP. Sin datos de contacto de funcionarios.

create or replace function public.contract_state_hash(v jsonb) returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select encode(sha256(convert_to(jsonb_build_object(
    'status', v ->> 'status', 'signed_on', v ->> 'signed_on', 'starts_on', v ->> 'starts_on', 'ends_on', v ->> 'ends_on',
    'value_initial', v ->> 'value_initial', 'value_paid', v ->> 'value_paid', 'value_invoiced', v ->> 'value_invoiced',
    'days_added', v ->> 'days_added', 'modality', v ->> 'modality', 'contract_type', v ->> 'contract_type',
    'contractor', coalesce(v -> 'contractor' ->> 'id_public', v -> 'contractor' ->> 'id_hmac'))::text, 'UTF8')), 'hex')
$$;

do $$
begin
  if exists (select 1 from public.contract_versions v join public.observations o on o.id = v.observation_id
              group by v.contract_id, public.contract_state_hash(o.value_json) having count(*) > 1) then
    raise exception 'recalcular hashes uniría versiones distintas de un contrato; revisar antes de migrar';
  end if;
end
$$;
alter table public.contract_versions disable trigger contract_versions_append_only;
update public.contract_versions v set content_hash = public.contract_state_hash(o.value_json)
  from public.observations o where o.id = v.observation_id;
alter table public.contract_versions enable trigger contract_versions_append_only;

create table public.entity_plans (
  id                    uuid primary key default gen_random_uuid(),
  entity_code           text not null,
  year                  integer not null check (year between 2000 and 2100),
  entity_name           text,
  mission_vision        text,
  strategic_perspective text,
  general_budget        numeric(20, 2),
  plan_version          integer,
  published_on          date,
  modified_on           date,
  native_id             text not null,
  source_id             uuid not null references public.sources (id),
  observation_id        uuid not null references public.observations (id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (entity_code, year)
);
alter table public.entity_plans enable row level security;
revoke all on public.entity_plans from public, anon, authenticated;

-- Perfil de una entidad: el plan más reciente de una fuente publicada (no en modo sombra).
create or replace function public.entity_profile(p_actor uuid) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select jsonb_build_object('year', p.year, 'mission_vision', p.mission_vision,
           'strategic_perspective', p.strategic_perspective, 'general_budget', p.general_budget,
           'published_on', p.published_on, 'source', s.name,
           'url', 'https://www.datos.gov.co/resource/b6m4-qgqv.json?identificador_unico=' || p.native_id)
    from public.entity_plans p join public.sources s on s.id = p.source_id and not s.shadow_mode
   where p.entity_code in (select ai.value_public from public.actor_identifiers ai
                            where ai.actor_id = p_actor and ai.issuer = 'SECOP' and ai.id_type = 'codigo_entidad')
     and (coalesce(p.mission_vision, '') <> '' or coalesce(p.strategic_perspective, '') <> '')
   order by p.year desc, p.plan_version desc nulls last
   limit 1
$$;

-- Ficha de un contrato (solo fuentes publicadas): concepto completo, partes, estado vigente y perfil de la entidad.
create or replace function public.public_contract(p_contract_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'contract_id', ct.id, 'native_id', ct.native_id, 'object', ct.object, 'official_url', ct.official_url,
      'source', s.code, 'source_name', s.name,
      'territory', (select jsonb_build_object('territory_id', t.id, 'name', t.name,
                      'department', (select d.name from public.territories d where d.id = t.parent_id))
                      from public.territories t where t.id = ct.territory_id),
      'entity', jsonb_build_object('actor_id', e.id, 'name', e.display_name,
                  'nit', (select value_public from public.actor_identifiers i where i.actor_id = e.id and i.issuer = 'DIAN' limit 1)),
      'entity_profile', public.entity_profile(e.id),
      'contractor', jsonb_build_object('actor_id', k.id, 'name', coalesce(k.display_name, ct.contractor_name_source),
                      'identified', k.id is not null, 'type', k.actor_type),
      'status', v.status_original, 'signed_on', v.signed_on, 'starts_on', v.starts_on, 'ends_on', v.ends_on,
      'value_initial', v.value_initial, 'value_current', v.value_current, 'value_paid', v.value_paid,
      'modality', v.modality, 'contract_type', v.contract_type,
      'versions', (select count(*) from public.contract_versions x where x.contract_id = ct.id))
      from public.contracts ct
      join public.sources s on s.id = ct.source_id and not s.shadow_mode
      left join public.contract_versions v on v.id = ct.current_version_id
      left join public.actors e on e.id = ct.entity_actor_id
      left join public.actors k on k.id = ct.contractor_actor_id
     where ct.id = p_contract_id);
end
$$;

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
      if v_entity is not null and nullif(r.v -> 'entity' ->> 'code', '') is not null
         and not exists (select 1 from public.actor_identifiers ai where ai.issuer = 'SECOP' and ai.id_type = 'codigo_entidad'
                                                                     and ai.value_public = r.v -> 'entity' ->> 'code') then
        insert into public.actor_identifiers (actor_id, issuer, id_type, value_public, value_masked, source_id, observation_id)
        values (v_entity, 'SECOP', 'codigo_entidad', r.v -> 'entity' ->> 'code', r.v -> 'entity' ->> 'code', r.source_id, r.id);
      end if;
      v_party := case r.v -> 'contractor' ->> 'kind'
                   when 'persona' then public.actor_by_identifier('persona', r.v -> 'contractor' ->> 'name', 'SECOP',
                          r.v -> 'contractor' ->> 'id_type', r.v -> 'contractor' ->> 'id_hmac', null,
                          r.v -> 'contractor' ->> 'id_masked', null, r.source_id, r.id)
                   when 'organizacion' then public.actor_by_identifier('organizacion_privada', r.v -> 'contractor' ->> 'name',
                          coalesce(r.v -> 'contractor' ->> 'issuer', 'DIAN'),
                          case when r.v -> 'contractor' ? 'issuer' then r.v -> 'contractor' ->> 'id_type' else 'NIT' end,
                          null, r.v -> 'contractor' ->> 'id_public', r.v -> 'contractor' ->> 'id_masked',
                          null, r.source_id, r.id)
                 end;
      insert into public.contracts (source_id, native_id, entity_actor_id, contractor_actor_id, territory_id, currency,
                                    object, official_url, contractor_name_source)
      values (r.source_id, r.v ->> 'native_id', v_entity, v_party, v_terr, coalesce(r.v ->> 'currency', 'COP'),
              r.v ->> 'object', r.v ->> 'url', r.v -> 'contractor' ->> 'name')
      on conflict (source_id, native_id) do update
         set entity_actor_id = coalesce(excluded.entity_actor_id, contracts.entity_actor_id),
             contractor_actor_id = coalesce(excluded.contractor_actor_id, contracts.contractor_actor_id),
             contractor_name_source = coalesce(excluded.contractor_name_source, contracts.contractor_name_source),
             territory_id = coalesce(excluded.territory_id, contracts.territory_id),
             object = coalesce(excluded.object, contracts.object), official_url = coalesce(excluded.official_url, contracts.official_url)
      returning id into v_contract;
      v_hash := public.contract_state_hash(r.v);
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

  elsif p_step = 'entity_plans' then
    select id into v_source from public.sources where code = 'SRC-25';
    for r in
      select o.id, o.value_json v from public.observations o
       where o.source_id = v_source and o.predicate = 'entity_plan' and o.status = 'published'
         and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
       order by o.last_observed_at
       limit p_limit
    loop
      insert into public.entity_plans (entity_code, year, entity_name, mission_vision, strategic_perspective, general_budget,
                                       plan_version, published_on, modified_on, native_id, source_id, observation_id)
      values (r.v ->> 'entity_code', (r.v ->> 'year')::int, r.v ->> 'entity_name', r.v ->> 'mission_vision',
              r.v ->> 'strategic_perspective', nullif(r.v ->> 'general_budget', '')::numeric,
              nullif(r.v ->> 'plan_version', '')::int, nullif(r.v ->> 'published_on', '')::date,
              nullif(r.v ->> 'modified_on', '')::date, r.v ->> 'native_id', v_source, r.id)
      on conflict (entity_code, year) do update
         set entity_name = excluded.entity_name, mission_vision = excluded.mission_vision,
             strategic_perspective = excluded.strategic_perspective, general_budget = excluded.general_budget,
             plan_version = excluded.plan_version, published_on = excluded.published_on,
             modified_on = excluded.modified_on, native_id = excluded.native_id, observation_id = excluded.observation_id,
             updated_at = now()
       where coalesce(excluded.plan_version, 0) >= coalesce(entity_plans.plan_version, 0);
      insert into public.normalized_observations (observation_id, step) values (r.id, p_step);
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'entity_plans', v_n);

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

create or replace function public.public_actor(p_actor_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'actor_id', a.id, 'name', a.display_name, 'actor_type', a.actor_type,
      'territory', (select jsonb_build_object('territory_id', t.id, 'name', t.name, 'code', t.code,
                      'department', (select d.name from public.territories d where d.id = t.parent_id))
                      from public.territories t where t.id = a.territory_id),
      'profile', public.entity_profile(a.id),
      'identifiers', (select coalesce(jsonb_agg(jsonb_build_object('type', i.id_type, 'value', i.value_masked)), '[]')
                        from public.actor_identifiers i where i.actor_id = a.id and a.actor_type <> 'persona'),
      'positions', (select coalesce(jsonb_agg(jsonb_build_object('title', po.title_original, 'entity', e.display_name,
                      'entity_id', e.id, 'person', pp.display_name, 'person_id', pp.id, 'starts_on', po.starts_on,
                      'ends_on', po.ends_on) order by po.starts_on desc nulls last), '[]')
                      from public.positions po join public.actors pp on pp.id = po.person_actor_id
                      left join public.actors e on e.id = po.entity_actor_id
                     where (po.person_actor_id = a.id or po.entity_actor_id = a.id) and public.claim_is_public(po.claim_id)),
      'affiliations', (select coalesce(jsonb_agg(jsonb_build_object('person', pp.display_name, 'person_id', pp.id,
                         'organization', o.display_name, 'organization_id', o.id, 'type', af.affiliation_type,
                         'starts_on', af.starts_on, 'ends_on', af.ends_on)), '[]')
                         from public.affiliations af join public.actors pp on pp.id = af.person_actor_id
                         join public.actors o on o.id = af.organization_actor_id
                        where (af.person_actor_id = a.id or af.organization_actor_id = a.id)
                          and public.claim_is_public(af.claim_id)),
      'participations', (select coalesce(jsonb_agg(jsonb_build_object('proceeding_id', p.id,
                           'proceeding', concat_ws(' · ', p.authority, p.radicado_original), 'role_original', pa.role_original,
                           'role', pa.role_normalized, 'status', p.status_normalized,
                           'freshness', public.proceeding_freshness(p.system, p.last_verified_at))), '[]')
                           from public.participations pa join public.proceedings p on p.id = pa.proceeding_id
                          where pa.actor_id = a.id and public.claim_is_public(pa.claim_id)),
      -- Casos relacionados con el tipo de relación explícito (BUS-02): sujeto procesal directo o por afiliación.
      'cases', (select coalesce(jsonb_agg(distinct jsonb_build_object('case_id', c.id, 'title', r.title, 'relation', rel.relation)), '[]')
                  from (select cp.case_id, 'sujeto_procesal' as relation
                          from public.participations pa join public.case_proceedings cp on cp.proceeding_id = pa.proceeding_id
                         where pa.actor_id = a.id and public.claim_is_public(pa.claim_id) and public.claim_is_public(cp.claim_id)
                        union
                        select cp.case_id, 'por_afiliacion_de_' || pp.display_name
                          from public.affiliations af join public.actors pp on pp.id = af.person_actor_id
                          join public.participations pa on pa.actor_id = af.person_actor_id
                          join public.case_proceedings cp on cp.proceeding_id = pa.proceeding_id
                         where af.organization_actor_id = a.id and public.claim_is_public(af.claim_id)
                           and public.claim_is_public(pa.claim_id) and public.claim_is_public(cp.claim_id)) rel
                  join public.cases c on c.id = rel.case_id join public.case_revisions r on r.id = c.published_revision_id))
      from public.actors a where a.id = p_actor_id and a.merged_into_id is null);
end
$$;

create or replace function public.public_contracts(p_filters jsonb, p_limit integer default 200) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    with f as (
      select ct.id, ct.native_id, ct.object, ct.official_url, s.code as source, v.value_current, v.value_initial,
             v.status_original, v.signed_on, e.display_name as entity, coalesce(k.display_name, ct.contractor_name_source) as contractor,
             ct.contractor_actor_id is not null as contractor_identified,
             (select count(*) from public.contract_versions x where x.contract_id = ct.id) as versions
        from public.contracts ct join public.sources s on s.id = ct.source_id and not s.shadow_mode
        left join public.contract_versions v on v.id = ct.current_version_id
        left join public.actors e on e.id = ct.entity_actor_id
        left join public.actors k on k.id = ct.contractor_actor_id
       where (p_filters ->> 'territory_id' is null or ct.territory_id = (p_filters ->> 'territory_id')::uuid)
         and (p_filters ->> 'actor_id' is null or (p_filters ->> 'actor_id')::uuid in (ct.entity_actor_id, ct.contractor_actor_id))
         and (p_filters ->> 'case_id' is null or exists (select 1 from public.case_contracts cc
               where cc.contract_id = ct.id and cc.case_id = (p_filters ->> 'case_id')::uuid and public.claim_is_public(cc.claim_id)))
    )
    select jsonb_build_object(
      'known_total', (select count(*) from f),
      'items', coalesce((select jsonb_agg(to_jsonb(x) order by x.signed_on desc nulls last, x.id)
                          from (select * from f order by signed_on desc nulls last, id limit least(p_limit, 200)) x), '[]')));
end
$$;

revoke execute on function public.contract_state_hash(jsonb) from public, anon, authenticated;
revoke execute on function public.entity_profile(uuid) from public, anon, authenticated;
revoke execute on function public.public_contract(uuid) from public, anon;
grant execute on function public.public_contract(uuid) to authenticated;
