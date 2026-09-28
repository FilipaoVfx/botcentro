-- Interfaz conversacional I4 (centrorequirement.md CU-09, CU-11; DEC-18).
-- Actos de votación con totales CALCULADOS a partir del registro nominal (la fuente no publica un
-- resultado oficial agregado): la interfaz los rotula así y nunca los mezcla con totales reportados.
-- Una persona sin fila en un acto es «sin dato», no ausencia ni abstención.

create or replace function public.bot_votings_page(p_filters jsonb, p_limit integer default 200)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_since   date := (p_filters ->> 'since')::date;
  v_until   date := (p_filters ->> 'until')::date;
  v_project uuid := (p_filters ->> 'project_id')::uuid;
  v_persons uuid[] := (select coalesce(array_agg(x::uuid), '{}')
                         from jsonb_array_elements_text(coalesce(p_filters -> 'person_ids', '[]')) x);
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return (
    with acts as (
      select vt.id, vt.local_date, vt.subject_text,
             count(vo.id) filter (where vo.vote_normalized = 'yes') as yes,
             count(vo.id) filter (where vo.vote_normalized = 'no') as no
        from public.votings vt
        left join public.current_votes cv on cv.voting_id = vt.id
        left join public.vote_observations vo on vo.id = cv.selected_observation_id
       where (v_since is null or vt.local_date >= v_since)
         and (v_until is null or vt.local_date <= v_until)
         and (v_project is null or exists (select 1 from public.voting_projects vp
                                             where vp.voting_id = vt.id and vp.project_id = v_project))
         and (cardinality(v_persons) = 0 or exists (select 1 from public.current_votes c2
                                                     where c2.voting_id = vt.id and c2.person_id = any (v_persons)))
       group by vt.id
    )
    select jsonb_build_object(
      'total', (select count(*) from acts),
      'items', coalesce((select jsonb_agg(jsonb_build_object(
          'voting_id', a.id, 'date', a.local_date, 'subject', a.subject_text, 'yes', a.yes, 'no', a.no,
          'projects', (select jsonb_agg(public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year))
                         from public.voting_projects vp
                         join public.project_identifiers pi on pi.project_id = vp.project_id
                         join public.corporations c on c.id = pi.corporation_id and c.code = 'senado'
                        where vp.voting_id = a.id),
          'person_vote', case when cardinality(v_persons) > 0 then
              (select vo.vote_normalized from public.current_votes cv
                 join public.vote_observations vo on vo.id = cv.selected_observation_id
                where cv.voting_id = a.id and cv.person_id = any (v_persons) limit 1) end)
          order by a.local_date desc nulls last, a.id)
        from (select * from acts order by local_date desc nulls last, id limit least(p_limit, 200)) a), '[]'::jsonb)));
end
$$;

create or replace function public.bot_voting_detail(p_voting_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return (
    select jsonb_build_object(
      'voting_id', vt.id, 'date', vt.local_date, 'subject', vt.subject_text,
      'session', (select jsonb_build_object('type', s.session_type, 'corporation', c.code,
                                            'commission', cm.name)
                    from public.sessions s join public.corporations c on c.id = s.corporation_id
                    left join public.commissions cm on cm.id = s.commission_id where s.id = vt.session_id),
      'projects', (select coalesce(jsonb_agg(distinct jsonb_build_object('project_id', vp.project_id,
                     'label', (select string_agg(public.project_ref_label(c.code, pi.initiative_type, pi.number,
                                                                          pi.filing_year), ' · ' order by c.code)
                                 from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
                                where pi.project_id = vp.project_id))), '[]')
                     from public.voting_projects vp where vp.voting_id = vt.id),
      'yes', (select count(*) from public.current_votes cv join public.vote_observations vo on vo.id = cv.selected_observation_id
               where cv.voting_id = vt.id and vo.vote_normalized = 'yes'),
      'no', (select count(*) from public.current_votes cv join public.vote_observations vo on vo.id = cv.selected_observation_id
              where cv.voting_id = vt.id and vo.vote_normalized = 'no'),
      'no_voters', (select coalesce(jsonb_agg(pe.canonical_name order by pe.canonical_name), '[]')
                      from public.current_votes cv
                      join public.vote_observations vo on vo.id = cv.selected_observation_id and vo.vote_normalized = 'no'
                      join public.persons pe on pe.id = cv.person_id
                     where cv.voting_id = vt.id),
      'source_url', (select sn.final_url from public.observations o
                       join public.source_snapshots sn on sn.id = o.first_snapshot_id where o.id = vt.observation_id),
      'fetched_at', (select sn.fetched_at from public.observations o
                       join public.source_snapshots sn on sn.id = o.first_snapshot_id where o.id = vt.observation_id))
      from public.votings vt where vt.id = p_voting_id);
end
$$;

revoke execute on function public.bot_votings_page(jsonb, integer), public.bot_voting_detail(uuid) from public, anon;
grant execute on function public.bot_votings_page(jsonb, integer), public.bot_voting_detail(uuid) to authenticated;
