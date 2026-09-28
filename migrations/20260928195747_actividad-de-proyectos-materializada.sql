-- I5 (UI-O05): la lista de proyectos tardaba ~2 s porque recalculaba la actividad de ~6 mil
-- proyectos en cada consulta. Se materializa y se refresca tras la actualización diaria.

create materialized view public.project_activity as
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
  select p.id as project_id, p.initiative_type, p.canonical_title,
         pr.v ->> 'short_name' as short_name, pr.v ->> 'object' as object,
         public.valid_legislative_date(pr.v ->> 'filed_camara') as filed_camara,
         public.valid_legislative_date(pr.v ->> 'filed_senado') as filed_senado,
         vo.last_vote,
         s.status_normalized, s.status_raw,
         (select string_agg(public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year), ' · '
                            order by c.code)
            from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
           where pi.project_id = p.id) as label,
         (select array_agg(distinct c.code) from public.project_identifiers pi
            join public.corporations c on c.id = pi.corporation_id where pi.project_id = p.id) as corporations
    from public.projects p
    left join profile pr on pr.project_id = p.id
    left join voting vo on vo.project_id = p.id
    left join public.project_status_projection sp on sp.project_id = p.id
    left join public.project_status_observations s on s.id = sp.selected_status_id
   where p.merged_into_id is null
)
select b.*, greatest(b.filed_camara, b.filed_senado, b.last_vote) as activity_date,
       case greatest(b.filed_camara, b.filed_senado, b.last_vote)
         when b.last_vote then 'Votación en plenaria del Senado'
         when b.filed_senado then 'Radicado en el Senado'
         when b.filed_camara then 'Radicado en la Cámara'
       end as activity_kind,
       public.fold_text(concat_ws(' ', b.canonical_title, b.short_name, b.object)) as search_text
  from base b;

create unique index project_activity_pk on public.project_activity (project_id);
create index project_activity_date_idx on public.project_activity (activity_date desc nulls last, project_id);
revoke all on public.project_activity from public, anon, authenticated;

create or replace function public.maintenance_refresh_project_activity()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['ingest_service']);
  refresh materialized view concurrently public.project_activity;
end
$$;

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
    with filtered as (
      select a.* from public.project_activity a
       where (v_corp is null or v_corp = any (a.corporations))
         and (v_type is null or a.initiative_type = v_type)
         and (v_status is null or a.status_normalized = v_status)
         and (v_since is null or a.activity_date >= v_since)
         and (cardinality(v_tokens) = 0 or a.search_text like all (select '%' || t || '%' from unnest(v_tokens) t))
    )
    select jsonb_build_object(
      'total', (select count(*) from filtered),
      'as_of', (select max(activity_date) from public.project_activity),
      'items', coalesce((select jsonb_agg(jsonb_build_object(
                  'project_id', f.project_id, 'label', f.label, 'title', coalesce(f.short_name, f.canonical_title),
                  'full_title', f.canonical_title, 'activity_date', f.activity_date, 'activity_kind', f.activity_kind,
                  'status', f.status_raw) order by f.activity_date desc nulls last, f.project_id)
                 from (select * from filtered order by activity_date desc nulls last, project_id
                        limit least(p_limit, 200)) f), '[]'::jsonb)));
end
$$;

revoke execute on function public.maintenance_refresh_project_activity() from public, anon;
grant execute on function public.maintenance_refresh_project_activity() to authenticated;
