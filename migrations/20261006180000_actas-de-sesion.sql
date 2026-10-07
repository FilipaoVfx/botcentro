-- Actas de las sesiones en la Gaceta del Congreso (SRC-03), con la sesión que registra cada una (encabezado
-- «ACTA NÚMERO N DE AAAA (mes día)» + cuerpo). Se enlazan al video oficial (SRC-26), a la vista del día y a las
-- votaciones por corporación, cuerpo y fecha. El texto sigue en el índice de Qdrant; aquí solo la referencia.

create table public.session_actas (
  id           uuid primary key default gen_random_uuid(),
  document_key text not null,
  gaceta_url   text not null check (gaceta_url like 'https://svrpubindc.imprenta.gov.co/%'),
  acta_number  text not null,
  acta_year    integer not null,
  session_date date,
  corporation  text check (corporation in ('senado', 'camara', 'congreso')),
  body         text,
  body_key     text,
  pdf_page     integer,
  published_on date,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (document_key, acta_number, acta_year)
);
create index session_actas_day_idx on public.session_actas (session_date, corporation);
alter table public.session_actas enable row level security;
revoke all on public.session_actas from public, anon, authenticated;

create or replace function public.ingest_session_actas(p_rows jsonb) returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  n integer;
begin
  perform public.require_app_role(array['ingest_service']);
  insert into public.session_actas (document_key, gaceta_url, acta_number, acta_year, session_date, corporation, body,
                                    body_key, pdf_page, published_on)
  select r ->> 'document_key', r ->> 'gaceta_url', r ->> 'number', (r ->> 'year')::int,
         nullif(r ->> 'session_date', '')::date, r ->> 'corporation', r ->> 'body', r ->> 'body_key',
         nullif(r ->> 'pdf_page', '')::int, nullif(r ->> 'published_on', '')::date
    from jsonb_array_elements(p_rows) r
  on conflict (document_key, acta_number, acta_year) do update
     set session_date = excluded.session_date, corporation = excluded.corporation, body = excluded.body,
         body_key = excluded.body_key, pdf_page = excluded.pdf_page, updated_at = now();
  get diagnostics n = row_count;
  return n;
end
$$;

-- Actas de sesiones entre dos fechas (misma regla de corporación que bot_session_videos).
create or replace function public.bot_session_actas(p_corporation text, p_from date, p_to date) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'session_date', a.session_date, 'corporation', a.corporation, 'body', a.body, 'body_key', a.body_key,
            'acta_number', a.acta_number, 'acta_year', a.acta_year, 'document_key', a.document_key,
            'gaceta', split_part(a.document_key, ':', 4) || '/' || split_part(a.document_key, ':', 3),
            'url', a.gaceta_url, 'pdf_page', a.pdf_page, 'published_on', a.published_on)
            order by a.session_date desc, (a.body_key = 'plenaria') desc nulls last, a.body), '[]')
            from public.session_actas a
           where a.session_date between p_from and p_to
             and (p_corporation is null or a.corporation = p_corporation or a.corporation = 'congreso'));
end
$$;

revoke execute on function public.ingest_session_actas(jsonb) from public, anon;
grant execute on function public.ingest_session_actas(jsonb) to authenticated;
revoke execute on function public.bot_session_actas(text, date, date) from public, anon;
grant execute on function public.bot_session_actas(text, date, date) to authenticated;
