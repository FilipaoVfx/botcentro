-- Interfaz conversacional I3 (centrorequirement.md CU-04, CU-05, CU-06; DEC-18).
-- La portada de un día separa lo ocurrido (hechos con fecha), lo programado (agenda) y lo
-- publicado (gacetas, que se leen del índice vectorial en Python). La cronología de un proyecto
-- solo incluye hechos legislativos fechados; descargar o indexar no son eventos del trámite.

create or replace function public.bot_day_overview(p_corporation text, p_date date)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_corp uuid := (select id from public.corporations where code = p_corporation);
begin
  perform public.require_app_role(array['query_service', 'admin']);
  if v_corp is null then
    raise exception using errcode = '22023', message = 'corporación desconocida';
  end if;
  return jsonb_build_object(
    'votings', (select coalesce(jsonb_agg(x order by x ->> 'subject'), '[]') from (
                  select jsonb_build_object(
                           'subject', vt.subject_text,
                           'projects', (select jsonb_agg(public.project_ref_label(c.code, pi.initiative_type, pi.number,
                                                                                  pi.filing_year))
                                          from public.voting_projects vp
                                          join public.project_identifiers pi on pi.project_id = vp.project_id
                                          join public.corporations c on c.id = pi.corporation_id and c.id = v_corp
                                         where vp.voting_id = vt.id),
                           'yes', count(vo.id) filter (where vo.vote_normalized = 'yes'),
                           'no', count(vo.id) filter (where vo.vote_normalized = 'no')) as x
                    from public.votings vt
                    left join public.sessions s on s.id = vt.session_id
                    left join public.current_votes cv on cv.voting_id = vt.id
                    left join public.vote_observations vo on vo.id = cv.selected_observation_id
                   where vt.local_date = p_date and coalesce(s.corporation_id, v_corp) = v_corp
                   group by vt.id) y),
    'filings', (select coalesce(jsonb_agg(x order by x ->> 'label'), '[]') from (
                  select distinct on (o.subject_ref) jsonb_build_object(
                           'label', coalesce(case p_corporation when 'camara' then o.value_json ->> 'camara_ref'
                                                                 else o.value_json ->> 'senado_ref' end,
                                             o.value_json ->> 'camara_raw'),
                           'title', coalesce(o.value_json ->> 'short_name', o.value_json ->> 'title')) as x
                    from public.observations o
                   where o.predicate = 'project_profile' and o.status = 'published'
                     and o.value_json ->> (case p_corporation when 'camara' then 'filed_camara' else 'filed_senado' end)
                         = p_date::text
                   order by o.subject_ref, o.last_observed_at desc) y),
    'agenda', (select coalesce(jsonb_agg(x order by x ->> 'title'), '[]') from (
                 select jsonb_build_object('title', r.title, 'commission', cm.name) as x
                   from (select distinct on (ar.agenda_item_id) ar.* from public.agenda_revisions ar
                          order by ar.agenda_item_id, ar.revision_no desc) r
                   join public.agenda_items ai on ai.id = r.agenda_item_id and ai.corporation_id = v_corp
                   left join public.commissions cm on cm.id = ai.commission_id
                  where r.local_date = p_date) y),
    'agenda_covered', exists (select 1 from public.agenda_items ai where ai.corporation_id = v_corp),
    'votings_covered', exists (select 1 from public.votings vt join public.sessions s on s.id = vt.session_id
                                where s.corporation_id = v_corp),
    'latest_agenda', (select max(r.local_date) from public.agenda_revisions r
                        join public.agenda_items ai on ai.id = r.agenda_item_id and ai.corporation_id = v_corp),
    'latest_voting', (select max(vt.local_date) from public.votings vt
                        join public.sessions s on s.id = vt.session_id and s.corporation_id = v_corp));
end
$$;

create or replace function public.bot_project_timeline(p_project_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return jsonb_build_object(
    'events', (
      select coalesce(jsonb_agg(e order by e ->> 'date' desc), '[]') from (
        -- Radicaciones (ficha de la Cámara)
        select jsonb_build_object('date', d.day, 'kind', 'radicacion', 'corporation', d.corp,
                                  'detail', 'Radicado en ' || case d.corp when 'camara' then 'la Cámara' else 'el Senado' end,
                                  'source', 'SRC-06') as e
          from (select distinct on (corp) corp, day from (
                  select 'camara' as corp, public.valid_legislative_date(o.value_json ->> 'filed_camara') as day, o.last_observed_at
                    from public.project_identifiers pi
                    join public.project_identifier_observations pio on pio.identifier_id = pi.id
                    join public.observations o on o.id = pio.observation_id and o.predicate = 'project_profile'
                   where pi.project_id = p_project_id
                  union all
                  select 'senado', public.valid_legislative_date(o.value_json ->> 'filed_senado'), o.last_observed_at
                    from public.project_identifiers pi
                    join public.project_identifier_observations pio on pio.identifier_id = pi.id
                    join public.observations o on o.id = pio.observation_id and o.predicate = 'project_profile'
                   where pi.project_id = p_project_id) f
                where day is not null order by corp, last_observed_at desc) d
        union all
        -- Votaciones nominales en plenaria del Senado (una fila por acto de votación)
        select jsonb_build_object('date', vt.local_date, 'kind', 'votacion', 'corporation', 'senado',
                                  'detail', format('Votación en plenaria: Sí %s · No %s',
                                                   count(vo.id) filter (where vo.vote_normalized = 'yes'),
                                                   count(vo.id) filter (where vo.vote_normalized = 'no')),
                                  'source', 'SRC-01')
          from public.voting_projects vp
          join public.votings vt on vt.id = vp.voting_id
          left join public.current_votes cv on cv.voting_id = vt.id
          left join public.vote_observations vo on vo.id = cv.selected_observation_id
         where vp.project_id = p_project_id and vt.local_date is not null
         group by vt.id
        union all
        -- Agenda: programación identificada, no hecho realizado
        select jsonb_build_object('date', r.local_date, 'kind', 'agenda', 'corporation', c.code,
                                  'detail', 'En agenda: ' || coalesce(r.title, 'sin título'), 'source', 'SRC-01')
          from public.agenda_projects ap
          join public.agenda_revisions r on r.id = ap.agenda_revision_id
          join public.agenda_items ai on ai.id = r.agenda_item_id
          join public.corporations c on c.id = ai.corporation_id
         where ap.project_id = p_project_id and r.local_date is not null
      ) x),
    'status', (select jsonb_build_object('raw', s.status_raw, 'observed_at', o.last_observed_at, 'source', src.code)
                 from public.project_status_projection sp
                 join public.project_status_observations s on s.id = sp.selected_status_id
                 join public.observations o on o.id = s.observation_id
                 join public.sources src on src.id = o.source_id
                where sp.project_id = p_project_id));
end
$$;

revoke execute on function public.bot_day_overview(text, date), public.bot_project_timeline(uuid) from public, anon;
grant execute on function public.bot_day_overview(text, date), public.bot_project_timeline(uuid) to authenticated;
