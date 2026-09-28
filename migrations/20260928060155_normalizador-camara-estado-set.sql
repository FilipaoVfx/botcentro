-- Normalizador de SRC-06: los pasos de estado y autoría resuelven el proyecto con un único join
-- (antes, una llamada por fila) y la proyección de estado se recalcula solo para los proyectos
-- tocados en el lote. Con ~6,6k fichas el paso anterior superaba el límite de 10 s del RPC.

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
  v_projects uuid[];
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
    with prof as (
      select distinct on (po.subject_ref) po.subject_ref, pi.project_id
        from public.observations po
        join public.project_identifier_observations pio on pio.observation_id = po.id
        join public.project_identifiers pi on pi.id = pio.identifier_id
       where po.source_id = v_source and po.predicate = 'project_profile'
       order by po.subject_ref, po.last_observed_at desc
    ), ins as (
      insert into public.project_status_observations (project_id, corporation_id, status_raw, status_normalized,
                                                      observation_id)
      select prof.project_id, v_camara, o.value_json ->> 'status_raw',
             public.camara_status_normalized(o.value_json ->> 'status_raw'), o.id
        from public.observations o
        join prof on prof.subject_ref = o.subject_ref
       where o.source_id = v_source and o.predicate = 'status_reported' and o.status = 'published'
         and not exists (select 1 from public.project_status_observations s where s.observation_id = o.id)
       limit p_limit
      on conflict (project_id, observation_id) do nothing
      returning project_id
    )
    select coalesce(array_agg(distinct project_id), '{}'), count(*) into v_projects, v_n from ins;

    -- Proyección: último estado observado (la fuente no fecha el estado; se documenta la regla).
    insert into public.project_status_projection (project_id, selected_status_id, conflict, considered_status_ids,
                                                  rule_version, computed_at)
    select s.project_id, (array_agg(s.id order by o.last_observed_at desc, s.id))[1], false,
           array_agg(s.id), 'camara-latest-observed-v1', now()
      from public.project_status_observations s
      join public.observations o on o.id = s.observation_id
     where o.source_id = v_source and s.project_id = any (v_projects)
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

    with prof as (
      select distinct on (po.subject_ref) po.subject_ref, pi.project_id
        from public.observations po
        join public.project_identifier_observations pio on pio.observation_id = po.id
        join public.project_identifiers pi on pi.id = pio.identifier_id
       where po.source_id = v_source and po.predicate = 'project_profile'
       order by po.subject_ref, po.last_observed_at desc
    )
    insert into public.project_participants (project_id, person_id, role, corporation_id, observation_id)
    select prof.project_id, pi.person_id, 'autor', v_camara, o.id
      from public.observations o
      join public.person_identifiers pi on pi.source_id = v_source
                                       and pi.external_id = split_part(o.value_json ->> 'person_ref', ':', 3)
      join prof on prof.subject_ref = o.value_json ->> 'project_ref'
     where o.source_id = v_source and o.predicate = 'authorship' and o.status = 'published'
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

