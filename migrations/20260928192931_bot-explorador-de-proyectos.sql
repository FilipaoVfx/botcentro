-- Interfaz conversacional I2 (centrorequirement.md CU-01/02/03; DEC-18).
-- «Actividad reciente» = último hecho legislativo fechado en las fuentes: radicación en Cámara o
-- Senado (ficha de la Cámara) o votación en plenaria del Senado. Nunca la fecha de descarga.

create or replace function public.fold_text(p text) returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select lower(translate(coalesce(p, ''), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNaeiouun'))
$$;

-- Hasta 200 proyectos ordenados (actividad desc, id) para materializar la lista mostrada en la
-- interfaz; los ordinales y la paginación se resuelven contra esos IDs (UI-F08, UI-F09).
-- Filtros: corporation ('senado'|'camara'), initiative_type, since (fecha), query (texto),
-- status (estado normalizado de la Cámara).
create or replace function public.bot_projects_page(p_filters jsonb, p_limit integer default 200)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_since  date := (p_filters ->> 'since')::date;
  v_corp   text := p_filters ->> 'corporation';
  v_type   text := p_filters ->> 'initiative_type';
  v_status text := p_filters ->> 'status';
  v_tokens text[] := (select coalesce(array_agg(t), '{}') from regexp_split_to_table(
                        public.fold_text(p_filters ->> 'query'), '\s+') t where length(t) >= 3);
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return (
    with profile as (
      select distinct on (pi.project_id) pi.project_id, o.value_json v
        from public.project_identifiers pi
        join public.project_identifier_observations pio on pio.identifier_id = pi.id
        join public.observations o on o.id = pio.observation_id and o.predicate = 'project_profile'
       order by pi.project_id, o.last_observed_at desc
    ), voting as (
      select vp.project_id, max(v.local_date) as last_vote
        from public.voting_projects vp join public.votings v on v.id = vp.voting_id
       group by vp.project_id
    ), base as (
      select p.id, p.initiative_type, p.canonical_title,
             pr.v ->> 'short_name' as short_name, pr.v ->> 'object' as object,
             nullif(pr.v ->> 'filed_camara', '')::date as filed_camara,
             nullif(pr.v ->> 'filed_senado', '')::date as filed_senado,
             vo.last_vote,
             (select s.status_normalized from public.project_status_projection sp
                join public.project_status_observations s on s.id = sp.selected_status_id
               where sp.project_id = p.id) as status_normalized,
             (select s.status_raw from public.project_status_projection sp
                join public.project_status_observations s on s.id = sp.selected_status_id
               where sp.project_id = p.id) as status_raw,
             (select string_agg(public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year), ' · '
                                order by c.code)
                from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
               where pi.project_id = p.id) as label,
             (select array_agg(distinct c.code) from public.project_identifiers pi
                join public.corporations c on c.id = pi.corporation_id where pi.project_id = p.id) as corporations
        from public.projects p
        left join profile pr on pr.project_id = p.id
        left join voting vo on vo.project_id = p.id
       where p.merged_into_id is null
    ), activity as (
      select b.*, greatest(b.filed_camara, b.filed_senado, b.last_vote) as activity_date,
             case greatest(b.filed_camara, b.filed_senado, b.last_vote)
               when b.last_vote then 'Votación en plenaria del Senado'
               when b.filed_senado then 'Radicado en el Senado'
               when b.filed_camara then 'Radicado en la Cámara'
             end as activity_kind
        from base b
    ), filtered as (
      select a.* from activity a
       where (v_corp is null or v_corp = any (a.corporations))
         and (v_type is null or a.initiative_type = v_type)
         and (v_status is null or a.status_normalized = v_status)
         and (v_since is null or a.activity_date >= v_since)
         and (cardinality(v_tokens) = 0 or public.fold_text(concat_ws(' ', a.canonical_title, a.short_name, a.object))
                                            like all (select '%' || t || '%' from unnest(v_tokens) t))
    )
    select jsonb_build_object(
      'total', (select count(*) from filtered),
      'items', coalesce((select jsonb_agg(jsonb_build_object(
                  'project_id', f.id, 'label', f.label, 'title', coalesce(f.short_name, f.canonical_title),
                  'full_title', f.canonical_title, 'activity_date', f.activity_date, 'activity_kind', f.activity_kind,
                  'status', f.status_raw) order by f.activity_date desc nulls last, f.id)
                 from (select * from filtered order by activity_date desc nulls last, id limit least(p_limit, 200)) f),
                '[]'::jsonb))
  );
end
$$;

-- Autores de un proyecto con su partido cuando la fuente lo trae (senadores); sin partido para
-- representantes hasta capturar sus perfiles (limitación declarada en SRC-06).
create or replace function public.bot_project_participants(p_project_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return coalesce((
    select jsonb_agg(x order by x ->> 'name')
      from (select distinct on (pe.id) jsonb_build_object(
                     'person_id', pe.id, 'name', pe.canonical_name, 'role', pp.role,
                     'corporation', (select c.code from public.corporations c where c.id = pp.corporation_id),
                     'party', (select pa.name from public.party_memberships pm join public.parties pa on pa.id = pm.party_id
                                where pm.person_id = pe.id order by pm.valid_from desc nulls last limit 1),
                     'source', (select s.code from public.observations o join public.sources s on s.id = o.source_id
                                 where o.id = pp.observation_id)) as x
              from public.project_participants pp join public.persons pe on pe.id = pp.person_id
             where pp.project_id = p_project_id
             order by pe.id) y), '[]'::jsonb);
end
$$;

revoke execute on function public.bot_projects_page(jsonb, integer), public.bot_project_participants(uuid)
  from public, anon;
grant execute on function public.bot_projects_page(jsonb, integer), public.bot_project_participants(uuid)
  to authenticated;
