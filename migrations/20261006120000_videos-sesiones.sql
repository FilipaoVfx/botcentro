-- Videos oficiales de las sesiones del Congreso (SRC-26, feeds RSS oficiales de YouTube).
-- Solo metadatos y enlace. Se enlazan a los datos que el usuario ya consulta (día de la corporación,
-- trámite de un proyecto, detalle de una votación) por corporación, cuerpo y fecha de la sesión, que
-- vienen del título; la fecha de publicación en YouTube se guarda aparte y nunca la reemplaza.

create table public.session_videos (
  id             uuid primary key default gen_random_uuid(),
  video_id       text not null unique check (video_id ~ '^[A-Za-z0-9_-]{11}$'),
  channel_id     text not null,
  channel_name   text not null,
  title          text not null,
  url            text not null check (url like 'https://www.youtube.com/watch?v=%'),
  published_at   timestamptz,
  session_date   date,
  corporation    text check (corporation in ('senado', 'camara', 'congreso')),
  body           text,
  body_key       text,
  kind           text not null check (kind in ('sesion', 'audiencia', 'programa', 'senal')),
  topic          text,
  description    text,
  source_id      uuid not null references public.sources (id),
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index session_videos_day_idx on public.session_videos (session_date, corporation) where kind in ('sesion', 'audiencia');
alter table public.session_videos enable row level security;
revoke all on public.session_videos from public, anon, authenticated;

create or replace function public.normalize_session_videos(p_step text, p_limit integer default 200) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_source uuid;
  v_n      integer := 0;
  r        record;
begin
  perform public.require_app_role(array['ingest_service']);
  select id into v_source from public.sources where code = 'SRC-26';
  for r in
    select o.id, o.value_json v from public.observations o
     where o.source_id = v_source and o.predicate = 'session_video' and o.status = 'published'
       and not exists (select 1 from public.normalized_observations n where n.observation_id = o.id)
     order by o.last_observed_at
     limit p_limit
  loop
    insert into public.session_videos (video_id, channel_id, channel_name, title, url, published_at, session_date,
                                       corporation, body, body_key, kind, topic, description, source_id, observation_id)
    values (r.v ->> 'video_id', r.v ->> 'channel_id', r.v ->> 'channel_name', r.v ->> 'title', r.v ->> 'url',
            nullif(r.v ->> 'published_at', '')::timestamptz, nullif(r.v ->> 'session_date', '')::date,
            r.v ->> 'corporation', r.v ->> 'body', r.v ->> 'body_key', r.v ->> 'kind', r.v ->> 'topic',
            r.v ->> 'description', v_source, r.id)
    on conflict (video_id) do update
       set title = excluded.title, session_date = excluded.session_date, corporation = excluded.corporation,
           body = excluded.body, body_key = excluded.body_key, kind = excluded.kind, topic = excluded.topic,
           description = excluded.description, observation_id = excluded.observation_id, updated_at = now();
    insert into public.normalized_observations (observation_id, step) values (r.id, 'session_videos');
    v_n := v_n + 1;
  end loop;
  return jsonb_build_object('step', p_step, 'session_videos', v_n);
end
$$;

-- Videos de sesiones y audiencias entre dos fechas. Con corporación: los suyos, los de comisiones
-- conjuntas y las audiencias del Congreso sin corporación identificable (rotuladas «Congreso»).
create or replace function public.bot_session_videos(p_corporation text, p_from date, p_to date) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'video_id', v.video_id, 'url', v.url, 'title', v.title, 'channel', v.channel_name,
            'session_date', v.session_date, 'published_at', v.published_at, 'corporation', v.corporation,
            'body', v.body, 'body_key', v.body_key, 'kind', v.kind, 'topic', v.topic)
            order by v.session_date desc, (v.body_key = 'plenaria') desc nulls last, v.body, v.published_at), '[]')
            from public.session_videos v join public.sources s on s.id = v.source_id and not s.shadow_mode
           where v.kind in ('sesion', 'audiencia') and v.session_date between p_from and p_to
             and (p_corporation is null or v.corporation = p_corporation or v.corporation = 'congreso'
                  or v.corporation is null));
end
$$;

revoke execute on function public.normalize_session_videos(text, integer) from public, anon;
grant execute on function public.normalize_session_videos(text, integer) to authenticated;
revoke execute on function public.bot_session_videos(text, date, date) from public, anon;
grant execute on function public.bot_session_videos(text, date, date) to authenticated;
