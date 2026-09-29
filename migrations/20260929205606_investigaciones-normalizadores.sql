-- Investigaciones: normalización de observaciones a entidades (DAT-01..06, REL-01/02, EVI-10, §11.1).
-- Idempotente y por lotes (límite de 10 s por RPC). Reglas:
--   * Identidad por documento (NIT público o HMAC de documento de persona), nunca por nombre (T-19).
--   * Una sanción de SIRI se registra como afirmación `pending_review`: no se publica sin revisión.
--   * Un contrato conserva cada versión distinta; la vigente se actualiza sin borrar historia.

create or replace function public.place_key(p text) returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$ select btrim(regexp_replace(regexp_replace(public.fold_text(p), '[^a-z0-9 ]', ' ', 'g'), '\s+', ' ', 'g')) $$;

create or replace function public.territory_match(p_department text, p_municipality text) returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select t.id from public.territories t join public.territories d on d.id = t.parent_id
   where t.level = 'municipio' and public.place_key(t.name) = public.place_key(p_municipality)
     and (p_department is null or public.place_key(d.name) = public.place_key(p_department)
          or public.place_key(d.name) like public.place_key(p_department) || '%'
          or public.place_key(p_department) like public.place_key(d.name) || '%')
   order by t.code limit 1
$$;

create or replace function public.ingest_register_schema(
  p_source_code text, p_fingerprint text, p_fields jsonb, p_mapping_version text, p_result text,
  p_missing_critical text[], p_missing_optional text[], p_dataset_updated_at timestamptz
) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_source uuid;
begin
  perform public.require_app_role(array['ingest_service']);
  select id into v_source from public.sources where code = p_source_code;
  if v_source is null then raise exception using errcode = 'P0002', message = 'fuente inexistente'; end if;
  insert into public.source_schema_versions (source_id, fingerprint, fields, mapping_version, result, missing_critical,
                                             missing_optional)
  values (v_source, p_fingerprint, p_fields, p_mapping_version, p_result, coalesce(p_missing_critical, '{}'),
          coalesce(p_missing_optional, '{}'))
  on conflict (source_id, fingerprint, mapping_version) do update set validated_at = now(), result = excluded.result;
  update public.sources
     set dataset_updated_at = coalesce(p_dataset_updated_at, dataset_updated_at),
         health_state = case p_result when 'incompatible' then 'unavailable' when 'degraded' then 'degraded'
                                      else health_state end
   where id = v_source;
end
$$;

create or replace function public.ingest_source_status(p_source_code text, p_health text, p_coverage text) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service']);
  update public.sources set health_state = coalesce(p_health, health_state), coverage_state = coalesce(p_coverage, coverage_state)
   where code = p_source_code;
end
$$;

-- Actor por identificador verificable; si no existe se crea. Nunca se reutiliza por nombre.
create or replace function public.actor_by_identifier(p_type text, p_name text, p_issuer text, p_id_type text,
                                                      p_hmac text, p_public text, p_masked text, p_territory uuid,
                                                      p_source uuid, p_observation uuid) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_actor uuid;
  v_type  text := coalesce(nullif(p_id_type, ''), 'doc');
begin
  if p_hmac is null and p_public is null then
    return null;  -- sin documento no hay identidad: no se crea un actor por nombre
  end if;
  select ai.actor_id into v_actor from public.actor_identifiers ai
   where ai.issuer = p_issuer and ai.id_type = v_type
     and ((p_hmac is not null and ai.value_hmac = p_hmac) or (p_public is not null and ai.value_public = p_public))
   limit 1;
  if v_actor is not null then
    return v_actor;
  end if;
  insert into public.actors (actor_type, display_name, normalized_name, territory_id)
  values (p_type, coalesce(nullif(btrim(p_name), ''), 'Sin nombre en la fuente'), public.fold_text(p_name), p_territory)
  returning id into v_actor;
  insert into public.actor_identifiers (actor_id, issuer, id_type, value_hmac, value_public, value_masked, source_id,
                                        observation_id)
  values (v_actor, p_issuer, v_type, p_hmac, p_public, coalesce(p_masked, '***'), p_source, p_observation);
  return v_actor;
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
         and not exists (select 1 from public.territories t where t.observation_id = o.id)
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
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'territories', v_n);

  elsif p_step = 'contracts' then
    for r in
      select o.id, o.source_id, o.value_json v, o.last_observed_at from public.observations o
        join public.sources s on s.id = o.source_id and s.code in ('SRC-15', 'SRC-17')
       where o.predicate = 'contract_snapshot' and o.status = 'published'
         and not exists (select 1 from public.contract_versions cv where cv.observation_id = o.id)
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
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'contract_observations', v_n);

  elsif p_step = 'siri' then
    select id into v_source from public.sources where code = 'SRC-18';
    for r in
      select o.id, o.value_json v, o.last_observed_at from public.observations o
       where o.source_id = v_source and o.predicate = 'siri_record' and o.status = 'published'
         and not exists (select 1 from public.proceedings p where p.observation_id = o.id)
       order by o.last_observed_at
       limit p_limit
    loop
      v_terr := public.territory_match(r.v -> 'place' ->> 'departamento', r.v -> 'place' ->> 'municipio');
      v_party := public.actor_by_identifier('persona', r.v -> 'person' ->> 'name', 'RNEC', r.v -> 'person' ->> 'id_type',
                                            r.v -> 'person' ->> 'id_hmac', null, r.v -> 'person' ->> 'id_masked', null,
                                            v_source, r.id);
      if v_party is null then
        continue;  -- sin documento no se atribuye a nadie
      end if;
      insert into public.claims (claim_type, statement, subject_type, subject_id, value, sensitive, origin, state,
                                 author_label, submitted_at)
      values ('participation',
              format('SIRI registra %s para %s (%s) por %s; efectos jurídicos desde %s. Registro %s.',
                     lower(coalesce(r.v ->> 'sanction', 'una sanción')), r.v -> 'person' ->> 'name',
                     coalesce(r.v ->> 'position', 'cargo no informado'), coalesce(r.v ->> 'authority', 'autoridad no informada'),
                     coalesce(r.v ->> 'effects_on', 'fecha no informada'), r.v ->> 'native_id'),
              'actor', v_party, jsonb_build_object('source', 'SRC-18', 'numero_siri', r.v ->> 'native_id',
                                                   'record_type', r.v ->> 'record_type'),
              true, 'system_ingest', 'pending_review', 'ingesta SIRI (automática)', now())
      returning id into v_claim;
      insert into public.claim_evidence (claim_id, evidence_id)
      select v_claim, oe.evidence_id from public.observation_evidence oe where oe.observation_id = r.id
      on conflict do nothing;
      insert into public.proceedings (system, source_identifier, authority, jurisdiction, source_id, radicado_original,
                                      status_original, status_normalized, finality, territory_id, observation_id, claim_id,
                                      last_verified_at)
      values ('SIRI', r.v ->> 'native_id', coalesce(r.v ->> 'authority', 'Procuraduría General de la Nación'),
              case upper(coalesce(r.v ->> 'record_type', ''))
                when 'DISCIPLINARIO' then 'disciplinaria' when 'PENAL' then 'penal' when 'FISCAL' then 'fiscal'
                when 'CONTRACTUAL' then 'administrativa' else 'otra' end,
              v_source, r.v ->> 'proceeding_number', r.v ->> 'sanction', 'sancion_registrada', 'desconocida', v_terr, r.id,
              v_claim, r.last_observed_at)
      on conflict (system, source_identifier) do update set last_verified_at = excluded.last_verified_at,
                                                            observation_id = excluded.observation_id
      returning id into v_proc;
      update public.claims set object_type = 'proceeding', object_id = v_proc where id = v_claim;
      insert into public.participations (actor_id, proceeding_id, role_original, role_normalized, claim_id)
      values (v_party, v_proc, coalesce(r.v ->> 'sanction', 'sancionado'), 'sancionado', v_claim);
      if nullif(r.v ->> 'effects_on', '') is not null then
        insert into public.proceeding_events (proceeding_id, event_type, description, occurred_on, date_precision,
                                              observation_id, claim_id)
        values (v_proc, 'efectos_juridicos', format('Sanción con efectos jurídicos: %s', coalesce(r.v ->> 'sanction', '')),
                (r.v ->> 'effects_on')::date, 'day', r.id, v_claim);
      end if;
      insert into public.editorial_reviews (object_type, object_id, version, decision, actor_label)
      values ('claim', v_claim, 1, 'submitted', 'ingesta SIRI (automática)');
      v_n := v_n + 1;
    end loop;
    return jsonb_build_object('step', p_step, 'siri_records', v_n);

  elsif p_step = 'documents' then
    select id into v_source from public.sources where code = 'SRC-19';
    insert into public.official_documents (source_id, native_id, doc_type, number, dependency, topic, subtopic, url, doc_date,
                                           observation_id)
    select v_source, o.value_json ->> 'native_id', o.value_json ->> 'doc_type', o.value_json ->> 'number',
           o.value_json ->> 'dependency', o.value_json ->> 'topic', o.value_json ->> 'subtopic', o.value_json ->> 'url',
           nullif(o.value_json ->> 'doc_date', '')::date, o.id
      from public.observations o
     where o.source_id = v_source and o.predicate = 'official_document' and o.status = 'published'
       and not exists (select 1 from public.official_documents d where d.observation_id = o.id)
     limit p_limit
    on conflict (source_id, native_id) do update set observation_id = excluded.observation_id;
    get diagnostics v_n = row_count;
    return jsonb_build_object('step', p_step, 'documents', v_n);
  end if;
  raise exception using errcode = '22023', message = format('paso desconocido: %s', p_step);
end
$$;

do $$
declare f text;
begin
  foreach f in array array['place_key(text)', 'territory_match(text, text)',
    'ingest_register_schema(text, text, jsonb, text, text, text[], text[], timestamptz)',
    'ingest_source_status(text, text, text)',
    'actor_by_identifier(text, text, text, text, text, text, text, uuid, uuid, uuid)',
    'normalize_investigations(text, integer)']
  loop
    execute format('revoke execute on function public.%s from public, anon', f);
  end loop;
  foreach f in array array['ingest_register_schema(text, text, jsonb, text, text, text[], text[], timestamptz)',
    'ingest_source_status(text, text, text)', 'normalize_investigations(text, integer)']
  loop
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end
$$;
