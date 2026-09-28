-- Normalizador de SRC-01 (Senado, Datos Públicos): observaciones publicadas → entidades del
-- trámite (SRS-F07..F11, §6.3–6.4). Idempotente: se puede ejecutar tras cada carga; solo crea
-- lo que falta y nunca borra. Cada hecho apunta a la observación que lo sustenta.
--
-- Pasos (para no exceder el tiempo de una petición, la CLI los llama por separado):
--   catalog    comisiones, personas, partidos, afiliaciones, periodos de cargo y membresías
--   projects   proyectos e identificadores Senado/Cámara (vínculo solo con evidencia explícita)
--   votes      sesiones plenarias, votaciones, proyectos votados, votos nominales y voto vigente
--   attendance asistencia por sesión y persona
--   agenda     asuntos de agenda y su historial de revisiones

create index if not exists observations_source_predicate_idx
  on public.observations (source_id, predicate, status);

-- «PL 12/2025 Senado» → (tipo, número, año, corporación). Referencias incompletas → null.
create or replace function public.parse_formatted_project_ref(p_ref text)
returns table (initiative_type text, number text, filing_year smallint, corporation_code text)
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select case m[1] when 'PL' then 'proyecto_ley' else 'proyecto_acto_legislativo' end,
         m[2], m[3]::smallint, case m[4] when 'Senado' then 'senado' else 'camara' end
    from regexp_match(p_ref, '^(PL|PAL) (\d+)/(\d{4}) (Senado|Cámara)$') as m
   where m is not null
$$;

create or replace function public.normalize_senado_od(p_step text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_source  uuid;
  v_senado  uuid;
  v_result  jsonb := jsonb_build_object('step', p_step);
  v_n       integer;
  r         record;
  v_ids     uuid[];
  v_project uuid;
  v_person  uuid;
  v_ident   uuid;
  v_target  uuid;
  pref      record;
begin
  perform public.require_app_role(array['ingest_service']);
  select id into v_source from public.sources where code = 'SRC-01';
  select id into v_senado from public.corporations where code = 'senado';
  if v_source is null or v_senado is null then
    raise exception using errcode = 'P0002', message = 'SRC-01 o la corporación senado no existen';
  end if;

  if p_step = 'catalog' then
    -- Comisiones (último perfil observado).
    insert into public.commissions (corporation_id, code, name, commission_type)
    select distinct on (o.subject_ref) v_senado, 'od-' || split_part(o.subject_ref, ':', 3), o.value_json ->> 'name',
           case when o.value_json ->> 'name' ilike '%constitucional permanente%' then 'constitucional_permanente'
                when o.value_json ->> 'name' ilike '%legal%' then 'legal'
                when o.value_json ->> 'name' ilike '%especial%' then 'especial'
                when o.value_json ->> 'name' ilike '%accidental%' then 'accidental'
                else 'otra' end
      from public.observations o
     where o.source_id = v_source and o.predicate = 'commission_profile' and o.status = 'published'
     order by o.subject_ref, o.last_observed_at desc
    on conflict (corporation_id, code) do update set name = excluded.name
      where public.commissions.name is distinct from excluded.name;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('commissions', v_n);

    -- Personas: una por identificador de la fuente; el nombre del perfil vigente prevalece
    -- sobre el visto en votos/asistencias. Homónimos no se fusionan (SRS-F08).
    v_n := 0;
    for r in
      select o.subject_ref, split_part(o.subject_ref, ':', 3) as ext,
             (array_agg(o.value_json ->> 'name' order by (o.predicate = 'senator_profile') desc, o.last_observed_at desc))[1] as name,
             (array_agg(o.value_json ->> 'normalized_name' order by (o.predicate = 'senator_profile') desc, o.last_observed_at desc))[1] as nname,
             (array_agg(o.id order by (o.predicate = 'senator_profile') desc, o.last_observed_at desc))[1] as obs
        from public.observations o
       where o.source_id = v_source and o.predicate in ('senator_profile', 'senator_seen') and o.status = 'published'
         and not exists (select 1 from public.person_identifiers pi
                          where pi.source_id = v_source and pi.external_id = split_part(o.subject_ref, ':', 3))
       group by o.subject_ref
    loop
      insert into public.persons (canonical_name, normalized_name) values (r.name, r.nname) returning id into v_person;
      insert into public.person_identifiers (person_id, source_id, external_id, observation_id)
      values (v_person, v_source, r.ext, r.obs);
      v_n := v_n + 1;
    end loop;
    v_result := v_result || jsonb_build_object('persons', v_n);

    -- Partidos y afiliaciones (cada observación de perfil es evidencia de su afiliación).
    insert into public.parties (name, normalized_name)
    select distinct on (lower(btrim(o.value_json ->> 'party'))) btrim(o.value_json ->> 'party'), lower(btrim(o.value_json ->> 'party'))
      from public.observations o
     where o.source_id = v_source and o.predicate = 'senator_profile' and o.status = 'published'
       and coalesce(btrim(o.value_json ->> 'party'), '') <> ''
       and not exists (select 1 from public.parties p where p.normalized_name = lower(btrim(o.value_json ->> 'party')));
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('parties', v_n);

    insert into public.party_memberships (person_id, party_id, observation_id)
    select pi.person_id, p.id, o.id
      from public.observations o
      join public.person_identifiers pi on pi.source_id = v_source and pi.external_id = split_part(o.subject_ref, ':', 3)
      join public.parties p on p.normalized_name = lower(btrim(o.value_json ->> 'party'))
     where o.source_id = v_source and o.predicate = 'senator_profile' and o.status = 'published'
       and not exists (select 1 from public.party_memberships m where m.observation_id = o.id and m.person_id = pi.person_id);
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('party_memberships', v_n);

    -- Periodo de cargo: el catálogo de senadores es el vigente; sin fechas publicadas, el
    -- intervalo queda desconocido (no se inventa).
    insert into public.person_terms (person_id, corporation_id, position, observation_id)
    select pi.person_id, v_senado, 'senador', o.id
      from public.observations o
      join public.person_identifiers pi on pi.source_id = v_source and pi.external_id = split_part(o.subject_ref, ':', 3)
     where o.source_id = v_source and o.predicate = 'senator_profile' and o.status = 'published'
       and not exists (select 1 from public.person_terms t where t.observation_id = o.id and t.person_id = pi.person_id);
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('person_terms', v_n);

    insert into public.commission_memberships (commission_id, person_id, observation_id)
    select c.id, pi.person_id, o.id
      from public.observations o
      join public.person_identifiers pi on pi.source_id = v_source and pi.external_id = split_part(o.subject_ref, ':', 3)
      join public.commissions c on c.corporation_id = v_senado
                               and c.code = 'od-' || split_part(o.value_json ->> 'commission_ref', ':', 3)
     where o.source_id = v_source and o.predicate = 'senator_profile' and o.status = 'published'
       and not exists (select 1 from public.commission_memberships m where m.observation_id = o.id and m.person_id = pi.person_id);
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('commission_memberships', v_n);

  elsif p_step = 'projects' then
    v_n := 0;
    for r in
      select distinct on (o.subject_ref) o.id, o.value_json
        from public.observations o
       where o.source_id = v_source and o.predicate = 'voting_subject' and o.status = 'published'
         and jsonb_array_length(o.value_json -> 'project_refs') > 0
         and not exists (select 1 from public.project_identifier_observations pio where pio.observation_id = o.id)
       order by o.subject_ref, o.last_observed_at desc
    loop
      select coalesce(array_agg(distinct pi.project_id), '{}') into v_ids
        from jsonb_array_elements_text(r.value_json -> 'project_refs') e(ref)
        cross join lateral public.parse_formatted_project_ref(e.ref) p
        join public.corporations c on c.code = p.corporation_code
        join public.project_identifiers pi on pi.corporation_id = c.id and pi.initiative_type = p.initiative_type
             and pi.filing_year = p.filing_year and pi.number = p.number
             and pi.numbering_scope = 'corporation_filing_year';

      if (r.value_json ->> 'explicit_link')::boolean and cardinality(v_ids) > 1 then
        -- Dos expedientes distintos citados como el mismo: no se fusiona; lo decide una persona.
        insert into public.review_cases (case_type, dedupe_key, summary, subject_type, candidates, evidence_ids)
        values ('identity', 'link-conflict:' || array_to_string(v_ids, ','),
                'Numeración Senado–Cámara vincula expedientes ya separados: ' || (r.value_json ->> 'subject_text'),
                'project', to_jsonb(v_ids), '{}')
        on conflict (dedupe_key) where state in ('open', 'in_review') do nothing;
        continue;
      end if;

      -- Con vínculo explícito todos los números van al mismo expediente (existente o nuevo);
      -- sin vínculo, cada número es su propio expediente.
      v_target := null;
      if (r.value_json ->> 'explicit_link')::boolean then
        v_target := v_ids[1];
      end if;

      for pref in
        select x.* from jsonb_array_elements_text(r.value_json -> 'project_refs') e(ref)
        cross join lateral public.parse_formatted_project_ref(e.ref) x
      loop
        select pi.id into v_ident
          from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
         where c.code = pref.corporation_code and pi.initiative_type = pref.initiative_type
           and pi.filing_year = pref.filing_year and pi.number = pref.number and pi.numbering_scope = 'corporation_filing_year';
        if v_ident is null then
          if (r.value_json ->> 'explicit_link')::boolean then
            if v_target is null then
              insert into public.projects (initiative_type) values (pref.initiative_type) returning id into v_target;
            end if;
            v_project := v_target;
          else
            insert into public.projects (initiative_type) values (pref.initiative_type) returning id into v_project;
          end if;
          insert into public.project_identifiers (project_id, corporation_id, initiative_type, number, number_raw,
                                                  filing_year, source_id)
          select v_project, c.id, pref.initiative_type, pref.number, pref.number, pref.filing_year, v_source
            from public.corporations c where c.code = pref.corporation_code
          returning id into v_ident;
          v_n := v_n + 1;
        end if;
        insert into public.project_identifier_observations (identifier_id, observation_id)
        values (v_ident, r.id) on conflict do nothing;
      end loop;
    end loop;
    v_result := v_result || jsonb_build_object('project_identifiers', v_n);

  elsif p_step = 'votes' then
    insert into public.sessions (corporation_id, session_type, source_id, external_key, local_date, date_precision)
    select v_senado, 'plenaria', v_source, split_part(x.plenary_ref, ':', 3), max(x.d),
           case when max(x.d) is null then 'unknown' else 'day' end
      from (select o.value_json ->> 'plenary_ref' as plenary_ref, o.effective_date as d
              from public.observations o
             where o.source_id = v_source and o.predicate in ('voting_subject', 'attendance') and o.status = 'published') x
     where not exists (select 1 from public.sessions s where s.source_id = v_source and s.external_key = split_part(x.plenary_ref, ':', 3))
     group by x.plenary_ref;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('sessions', v_n);

    insert into public.votings (voting_key, session_id, external_id, subject_type, subject_text, voting_method,
                                local_date, date_precision, observation_id)
    select distinct on (o.subject_ref) o.subject_ref, s.id, split_part(o.subject_ref, ':', 3) || ':' || split_part(o.subject_ref, ':', 4),
           case when o.value_json ->> 'subject_type' = 'project' then 'project' else 'other' end,
           o.value_json ->> 'subject_text', 'nominal', o.effective_date, o.date_precision, o.id
      from public.observations o
      left join public.sessions s on s.source_id = v_source and s.external_key = split_part(o.value_json ->> 'plenary_ref', ':', 3)
     where o.source_id = v_source and o.predicate = 'voting_subject' and o.status = 'published'
     order by o.subject_ref, o.last_observed_at desc
    on conflict (voting_key) do nothing;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('votings', v_n);

    insert into public.voting_projects (voting_id, project_id)
    select distinct v.id, pi.project_id
      from public.votings v
      join public.observations o on o.source_id = v_source and o.predicate = 'voting_subject' and o.subject_ref = v.voting_key
      cross join lateral jsonb_array_elements_text(o.value_json -> 'project_refs') e(ref)
      cross join lateral public.parse_formatted_project_ref(e.ref) p
      join public.corporations c on c.code = p.corporation_code
      join public.project_identifiers pi on pi.corporation_id = c.id and pi.initiative_type = p.initiative_type
           and pi.filing_year = p.filing_year and pi.number = p.number and pi.numbering_scope = 'corporation_filing_year'
    on conflict do nothing;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('voting_projects', v_n);

    insert into public.vote_observations (voting_id, person_id, vote_raw, vote_normalized, observation_id, observed_at)
    select v.id, pi.person_id, o.value_json ->> 'vote_raw', o.value_json ->> 'vote', o.id, o.first_observed_at
      from public.observations o
      join public.votings v on v.voting_key = o.value_json ->> 'voting_ref'
      join public.person_identifiers pi on pi.source_id = v_source
                                       and pi.external_id = split_part(o.value_json ->> 'person_ref', ':', 3)
     where o.source_id = v_source and o.predicate = 'nominal_vote' and o.status = 'published'
    on conflict (voting_id, person_id, observation_id) do nothing;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('vote_observations', v_n);

    -- Voto vigente: la observación más reciente; conflicto si hay votos distintos observados.
    insert into public.current_votes (voting_id, person_id, selected_observation_id, conflict, rule_version)
    select vo.voting_id, vo.person_id, (array_agg(vo.id order by vo.observed_at desc, vo.id))[1],
           count(distinct vo.vote_normalized) > 1, 'latest-observed-v1'
      from public.vote_observations vo
      join public.votings v on v.id = vo.voting_id and v.voting_key like 'senado-od:%'
     group by vo.voting_id, vo.person_id
    on conflict (voting_id, person_id) do update
       set selected_observation_id = excluded.selected_observation_id, conflict = excluded.conflict,
           rule_version = excluded.rule_version, computed_at = now()
     where public.current_votes.selected_observation_id is distinct from excluded.selected_observation_id
        or public.current_votes.conflict is distinct from excluded.conflict;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('current_votes', v_n);

  elsif p_step = 'attendance' then
    insert into public.attendance_observations (session_id, person_id, status_raw, status_normalized, observation_id)
    select s.id, pi.person_id, o.value_json ->> 'status_raw', o.value_json ->> 'status', o.id
      from public.observations o
      join public.sessions s on s.source_id = v_source and s.external_key = split_part(o.value_json ->> 'plenary_ref', ':', 3)
      join public.person_identifiers pi on pi.source_id = v_source
                                       and pi.external_id = split_part(o.value_json ->> 'person_ref', ':', 3)
     where o.source_id = v_source and o.predicate = 'attendance' and o.status = 'published'
    on conflict (session_id, person_id, observation_id) do nothing;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('attendance', v_n);

  elsif p_step = 'agenda' then
    insert into public.agenda_items (source_record_id, item_key, corporation_id, commission_id)
    select distinct on (o.subject_ref) sn.record_id, o.subject_ref, v_senado,
           (select c.id from public.commissions c
             where c.corporation_id = v_senado
               and o.value_json ->> 'title' ~* '^COMISI[OÓ]N\s'
               and c.name ilike 'Comisión ' || initcap(split_part(substring(o.value_json ->> 'title' from '^COMISI[OÓ]N\s+([^.\s]+)'), ' ', 1)) || '%'
             limit 1)
      from public.observations o
      join public.source_snapshots sn on sn.id = o.first_snapshot_id
     where o.source_id = v_source and o.predicate = 'scheduled' and o.status = 'published'
       and not exists (select 1 from public.agenda_items ai where ai.item_key = o.subject_ref)
     order by o.subject_ref, o.first_observed_at;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('agenda_items', v_n);

    -- Una revisión por observación distinta del asunto, en orden de observación.
    insert into public.agenda_revisions (agenda_item_id, revision_no, status, local_date, scheduled_at, title,
                                         observation_id, observed_at)
    select ai.id,
           coalesce((select max(ar.revision_no) from public.agenda_revisions ar where ar.agenda_item_id = ai.id), 0)
             + row_number() over (partition by ai.id order by o.first_observed_at, o.id),
           'scheduled', o.effective_date, o.effective_at, o.value_json ->> 'title', o.id, o.first_observed_at
      from public.observations o
      join public.agenda_items ai on ai.item_key = o.subject_ref
     where o.source_id = v_source and o.predicate = 'scheduled' and o.status = 'published'
       and not exists (select 1 from public.agenda_revisions ar where ar.observation_id = o.id);
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('agenda_revisions', v_n);

  else
    raise exception using errcode = '22023', message = format('paso desconocido: %s', p_step);
  end if;

  return v_result;
end
$$;

revoke execute on function public.normalize_senado_od(text), public.parse_formatted_project_ref(text) from public, anon;
grant execute on function public.normalize_senado_od(text) to authenticated;
