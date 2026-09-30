-- SECOP II: contratistas que son consorcios o uniones temporales (auditoría de la muestra, 2026-09-30).
-- La fuente publica su documento como «No Definido» (es_grupo = Si); el normalizador los descartaba
-- y 50 de 6.044 contratos quedaban sin contratista, incluidos los de mayor valor. Ahora:
--   * el parser entrega como identidad el código de proveedor de SECOP (emisor SECOP, tipo
--     codigo_proveedor), estable y no personal; el normalizador usa el emisor que indique el parser;
--   * sin documento ni código no hay identidad, pero el nombre publicado se conserva como texto en
--     contracts.contractor_name_source (DAT-05: nunca se crea identidad por nombre);
--   * public_contracts devuelve ese nombre e indica si el contratista está identificado.

alter table public.contracts add column if not exists contractor_name_source text;
update public.contracts c set contractor_name_source = o.value_json -> 'contractor' ->> 'name'
  from public.contract_versions v join public.observations o on o.id = v.observation_id
 where v.id = c.current_version_id and c.contractor_name_source is null;

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
