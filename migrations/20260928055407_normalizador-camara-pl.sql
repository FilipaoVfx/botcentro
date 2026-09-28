-- Normalizador de SRC-06 (Cámara, proyectos de ley): observaciones → proyectos, identificadores
-- Senado/Cámara, estado reportado y autoría (SRS-F07, F10; PRD-03, PRD-06). Idempotente y por
-- lotes (límite de 10 s por RPC en InsForge): la CLI repite cada paso hasta que no queda nada.

-- Vincula las referencias de un expediente. Con vínculo explícito (la fuente cita ambos números
-- en el mismo registro) comparten proyecto; si ya pertenecen a proyectos distintos no se fusiona
-- y se abre un caso de identidad. Devuelve el proyecto (o null si hubo conflicto).
create or replace function public.link_project_refs(
  p_refs        text[],
  p_explicit    boolean,
  p_observation uuid,
  p_source      uuid,
  p_summary     text
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_existing uuid[];
  v_target   uuid;
  v_project  uuid;
  v_ident    uuid;
  pref       record;
begin
  select coalesce(array_agg(distinct pi.project_id), '{}') into v_existing
    from unnest(p_refs) r(ref)
    cross join lateral public.parse_formatted_project_ref(r.ref) x
    join public.corporations c on c.code = x.corporation_code
    join public.project_identifiers pi on pi.corporation_id = c.id and pi.initiative_type = x.initiative_type
         and pi.filing_year = x.filing_year and pi.number = x.number and pi.numbering_scope = 'corporation_filing_year';

  if p_explicit and cardinality(v_existing) > 1 then
    insert into public.review_cases (case_type, dedupe_key, summary, subject_type, candidates)
    values ('identity', 'link-conflict:' || array_to_string(v_existing, ','), p_summary, 'project', to_jsonb(v_existing))
    on conflict (dedupe_key) where state in ('open', 'in_review') do nothing;
    return null;
  end if;
  v_target := case when p_explicit then v_existing[1] end;

  for pref in select x.* from unnest(p_refs) r(ref) cross join lateral public.parse_formatted_project_ref(r.ref) x loop
    select pi.id, pi.project_id into v_ident, v_project
      from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
     where c.code = pref.corporation_code and pi.initiative_type = pref.initiative_type
       and pi.filing_year = pref.filing_year and pi.number = pref.number and pi.numbering_scope = 'corporation_filing_year';
    if v_ident is null then
      if p_explicit then
        if v_target is null then
          insert into public.projects (initiative_type) values (pref.initiative_type) returning id into v_target;
        end if;
        v_project := v_target;
      else
        insert into public.projects (initiative_type) values (pref.initiative_type) returning id into v_project;
      end if;
      insert into public.project_identifiers (project_id, corporation_id, initiative_type, number, number_raw,
                                              filing_year, source_id)
      select v_project, c.id, pref.initiative_type, pref.number, pref.number, pref.filing_year, p_source
        from public.corporations c where c.code = pref.corporation_code
      returning id into v_ident;
    end if;
    insert into public.project_identifier_observations (identifier_id, observation_id)
    values (v_ident, p_observation) on conflict do nothing;
    v_target := coalesce(v_target, v_project);
    v_ident := null;
    v_project := null;
  end loop;
  return v_target;
end
$$;

-- Proyecto al que pertenece una ficha de Cámara (por su identificador ya vinculado).
create or replace function public.camara_project_of(p_source uuid, p_subject_ref text)
returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select pi.project_id
    from public.observations po
    join public.project_identifier_observations pio on pio.observation_id = po.id
    join public.project_identifiers pi on pi.id = pio.identifier_id
   where po.source_id = p_source and po.predicate = 'project_profile' and po.subject_ref = p_subject_ref
   order by po.last_observed_at desc
   limit 1
$$;

-- Vocabulario normalizado del estado reportado por la Cámara (se conserva siempre la etiqueta).
create or replace function public.camara_status_normalized(p_raw text) returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select case lower(btrim(p_raw))
    when 'archivado' then 'archived'
    when 'ley' then 'enacted'
    when 'retirado' then 'withdrawn'
    when 'sanción presidencial' then 'sent_to_sanction'
    when 'trámite en comisión' then 'in_committee'
    when 'debate de comisión' then 'in_committee'
    when 'trámite en plenaria' then 'in_plenary'
    when 'debate de plenaria' then 'in_plenary'
    when 'trámite en senado' then 'in_other_chamber'
    when 'trámite conciliación' then 'in_conciliation'
    when 'revisión corte constitucional' then 'constitutional_review'
    when 'trámite objeciones presidenciales' then 'objected'
    when 'acumulado' then 'accumulated'
    when 'pendiente ponencia primer debate' then 'pending_first_debate_report'
    when 'pendiente ponencia segundo debate' then 'pending_second_debate_report'
    when 'pendiente por designar ponentes' then 'pending_rapporteurs'
    else 'other'
  end
$$;

create or replace function public.normalize_camara_pl(p_step text, p_limit integer default 500)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_source uuid;
  v_camara uuid;
  v_n      integer := 0;
  v_skip   integer := 0;
  v_result jsonb := jsonb_build_object('step', p_step);
  r        record;
  v_proj   uuid;
  v_person uuid;
begin
  perform public.require_app_role(array['ingest_service']);
  select id into v_source from public.sources where code = 'SRC-06';
  select id into v_camara from public.corporations where code = 'camara';

  if p_step = 'projects' then
    for r in
      select o.id, o.subject_ref, o.value_json v
        from public.observations o
       where o.source_id = v_source and o.predicate = 'project_profile' and o.status = 'published'
         and o.value_json ->> 'camara_ref' is not null
         and not exists (select 1 from public.project_identifier_observations pio where pio.observation_id = o.id)
       limit p_limit
    loop
      v_proj := public.link_project_refs(
        array_remove(array[r.v ->> 'camara_ref', r.v ->> 'senado_ref'], null),
        (r.v ->> 'explicit_link')::boolean, r.id, v_source,
        'Numeración Senado–Cámara de la ficha de Cámara vincula expedientes ya separados: ' || coalesce(r.v ->> 'camara_raw', ''));
      if v_proj is null then
        v_skip := v_skip + 1;
        -- Deja constancia para no reprocesar la ficha en conflicto en cada lote.
        insert into public.project_identifier_observations (identifier_id, observation_id)
        select pi.id, r.id from public.project_identifiers pi
          join public.corporations c on c.id = pi.corporation_id
          cross join lateral public.parse_formatted_project_ref(r.v ->> 'camara_ref') x
         where c.code = x.corporation_code and pi.initiative_type = x.initiative_type
           and pi.filing_year = x.filing_year and pi.number = x.number
        on conflict do nothing;
        continue;
      end if;
      update public.projects
         set canonical_title = coalesce(canonical_title, r.v ->> 'title'),
             origin_corporation_id = coalesce(origin_corporation_id,
               (select id from public.corporations where code = case r.v ->> 'origin' when 'Senado' then 'senado'
                                                                   when 'Cámara' then 'camara' end))
       where id = v_proj and (canonical_title is null or origin_corporation_id is null);
      v_n := v_n + 1;
    end loop;
    v_result := v_result || jsonb_build_object('projects_linked', v_n, 'conflicts', v_skip);

  elsif p_step = 'status' then
    insert into public.project_status_observations (project_id, corporation_id, status_raw, status_normalized,
                                                    observation_id)
    select p.project_id, v_camara, o.value_json ->> 'status_raw',
           public.camara_status_normalized(o.value_json ->> 'status_raw'), o.id
      from public.observations o
      cross join lateral (select public.camara_project_of(v_source, o.subject_ref) as project_id) p
     where o.source_id = v_source and o.predicate = 'status_reported' and o.status = 'published'
       and p.project_id is not null
       and not exists (select 1 from public.project_status_observations s where s.observation_id = o.id)
     limit p_limit
    on conflict (project_id, observation_id) do nothing;
    get diagnostics v_n = row_count;

    -- Proyección: último estado observado (la fuente no fecha el estado; se documenta la regla).
    insert into public.project_status_projection (project_id, selected_status_id, conflict, considered_status_ids,
                                                  rule_version, computed_at)
    select s.project_id, (array_agg(s.id order by o.last_observed_at desc, s.id))[1], false,
           array_agg(s.id), 'camara-latest-observed-v1', now()
      from public.project_status_observations s
      join public.observations o on o.id = s.observation_id
     where o.source_id = v_source
     group by s.project_id
    on conflict (project_id) do update
       set selected_status_id = excluded.selected_status_id, considered_status_ids = excluded.considered_status_ids,
           rule_version = excluded.rule_version, computed_at = now()
     where public.project_status_projection.selected_status_id is distinct from excluded.selected_status_id;
    v_result := v_result || jsonb_build_object('status', v_n);

  elsif p_step = 'authors' then
    for r in
      select o.subject_ref, split_part(o.subject_ref, ':', 3) ext,
             (array_agg(o.value_json ->> 'name' order by o.last_observed_at desc))[1] as name,
             (array_agg(o.value_json ->> 'normalized_name' order by o.last_observed_at desc))[1] as nname,
             (array_agg(o.id order by o.last_observed_at desc))[1] as obs
        from public.observations o
       where o.source_id = v_source and o.predicate = 'representative_seen' and o.status = 'published'
         and not exists (select 1 from public.person_identifiers pi
                          where pi.source_id = v_source and pi.external_id = split_part(o.subject_ref, ':', 3))
       group by o.subject_ref
       limit p_limit
    loop
      insert into public.persons (canonical_name, normalized_name) values (r.name, r.nname) returning id into v_person;
      insert into public.person_identifiers (person_id, source_id, external_id, observation_id)
      values (v_person, v_source, r.ext, r.obs);
      v_n := v_n + 1;
    end loop;
    v_result := v_result || jsonb_build_object('representatives', v_n);

    insert into public.project_participants (project_id, person_id, role, corporation_id, observation_id)
    select p.project_id, pi.person_id, 'autor', v_camara, o.id
      from public.observations o
      join public.person_identifiers pi on pi.source_id = v_source
                                       and pi.external_id = split_part(o.value_json ->> 'person_ref', ':', 3)
      cross join lateral (select public.camara_project_of(v_source, o.value_json ->> 'project_ref') as project_id) p
     where o.source_id = v_source and o.predicate = 'authorship' and o.status = 'published'
       and p.project_id is not null
       and not exists (select 1 from public.project_participants pp where pp.observation_id = o.id)
     limit p_limit * 10;
    get diagnostics v_n = row_count;
    v_result := v_result || jsonb_build_object('authorships', v_n);

  else
    raise exception using errcode = '22023', message = format('paso desconocido: %s', p_step);
  end if;
  return v_result;
end
$$;

create index if not exists project_participants_observation_idx on public.project_participants (observation_id);
create index if not exists project_status_observations_observation_idx on public.project_status_observations (observation_id);

revoke execute on function public.normalize_camara_pl(text, integer), public.link_project_refs(text[], boolean, uuid, uuid, text),
  public.camara_project_of(uuid, text), public.camara_status_normalized(text) from public, anon;
grant execute on function public.normalize_camara_pl(text, integer) to authenticated;
