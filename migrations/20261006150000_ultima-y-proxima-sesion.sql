-- «Última sesión» y «Próxima sesión» de cada corporación, con la fuente y el significado de cada dato:
-- una plenaria con votaciones registradas o un video oficial de la sesión prueban que se celebró; la
-- agenda solo la anuncia (publicarla no confirma que ocurra). Si no hay agenda futura publicada se dice,
-- junto con la hora de la última consulta a la fuente y el patrón real de días de sesión del último año.

create or replace function public.bot_session_status(p_corporation text, p_today date) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_corp uuid;
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  select id into v_corp from public.corporations where code = p_corporation;
  if v_corp is null then
    raise exception using errcode = '22023', message = 'corporación desconocida';
  end if;
  return jsonb_build_object(
    'last_vote_session', (
      select jsonb_build_object('date', s.local_date, 'votings', (select count(*) from public.votings v where v.session_id = s.id))
        from public.sessions s
       where s.corporation_id = v_corp and s.session_type = 'plenaria' and s.local_date <= p_today
         and exists (select 1 from public.votings v where v.session_id = s.id)
       order by s.local_date desc limit 1),
    'last_video_session', (
      select jsonb_build_object('date', v.session_date, 'body', v.body, 'kind', v.kind, 'topic', v.topic, 'url', v.url)
        from public.session_videos v join public.sources src on src.id = v.source_id and not src.shadow_mode
       where v.kind in ('sesion', 'audiencia') and v.corporation = p_corporation and v.session_date <= p_today
       order by v.session_date desc, (v.body_key = 'plenaria') desc nulls last limit 1),
    'last_agenda', (
      select jsonb_build_object('date', a.local_date, 'items', count(*))
        from public.agenda_current a
       where a.corporation_id = v_corp and a.local_date <= p_today and a.status = 'scheduled'
       group by a.local_date order by a.local_date desc limit 1),
    'next_agenda', (
      select jsonb_build_object('date', d, 'titles', (
               select coalesce(jsonb_agg(a.title order by a.scheduled_at nulls last, a.title), '[]')
                 from (select title, scheduled_at from public.agenda_current
                        where corporation_id = v_corp and local_date = d and status = 'scheduled' limit 6) a))
        from (select min(local_date) d from public.agenda_current
               where corporation_id = v_corp and local_date > p_today and status = 'scheduled') x
       where d is not null),
    'agenda_covered', exists (select 1 from public.agenda_current a where a.corporation_id = v_corp),
    'agenda_checked_at', (
      select max(r.finished_at) from public.ingestion_runs r join public.sources s on s.id = r.source_id
       where s.code = case p_corporation when 'senado' then 'SRC-01' else 'SRC-06' end
         and r.status in ('succeeded', 'partial')),
    'weekday_pattern', (
      select jsonb_build_object('days', sum(n), 'by_weekday', jsonb_object_agg(dow, n))
        from (select extract(isodow from s.local_date)::int dow, count(distinct s.local_date) n
                from public.sessions s
               where s.corporation_id = v_corp and s.session_type = 'plenaria'
                 and s.local_date between p_today - 365 and p_today
                 and exists (select 1 from public.votings v where v.session_id = s.id)
               group by 1) w));
end
$$;

revoke execute on function public.bot_session_status(text, date) from public, anon;
grant execute on function public.bot_session_status(text, date) to authenticated;
