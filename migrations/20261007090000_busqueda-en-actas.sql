-- Búsqueda dentro de las actas de sesión (texto de la Gaceta del Congreso, SRC-03).
-- El texto de los fragmentos de actas se copia desde el índice vectorial (Qdrant, que no tiene índice de
-- texto) a Postgres con búsqueda de texto completo en español sin tildes: nombres exactos entre comillas,
-- variantes («votación», «votaciones», «votacion» del OCR) y fragmentos resaltados con su página.

create text search configuration public.es_unaccent (copy = pg_catalog.spanish);
alter text search configuration public.es_unaccent
  alter mapping for hword, hword_part, word with public.unaccent, spanish_stem;

create table public.acta_passages (
  id             uuid primary key,  -- el mismo identificador del fragmento en Qdrant
  document_key   text not null,
  pdf_page_start integer not null,
  pdf_page_end   integer not null,
  text           text not null,
  tsv            tsvector generated always as (to_tsvector('public.es_unaccent'::regconfig, text)) stored,
  created_at     timestamptz not null default now()
);
create index acta_passages_tsv_idx on public.acta_passages using gin (tsv);
create index acta_passages_doc_idx on public.acta_passages (document_key, pdf_page_start);
alter table public.acta_passages enable row level security;
revoke all on public.acta_passages from public, anon, authenticated;

create or replace function public.ingest_acta_passages(p_rows jsonb) returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  n integer;
begin
  perform public.require_app_role(array['ingest_service']);
  insert into public.acta_passages (id, document_key, pdf_page_start, pdf_page_end, text)
  select (r ->> 'id')::uuid, r ->> 'document_key', (r ->> 'pdf_page_start')::int, (r ->> 'pdf_page_end')::int, r ->> 'text'
    from jsonb_array_elements(p_rows) r
  on conflict (id) do update set text = excluded.text, pdf_page_start = excluded.pdf_page_start,
                                 pdf_page_end = excluded.pdf_page_end;
  get diagnostics n = row_count;
  return n;
end
$$;

-- Gacetas con actas cuyos fragmentos aún no están copiados (para la sincronización desde Qdrant).
create or replace function public.ingest_actas_without_passages(p_limit integer default 100) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select coalesce(jsonb_agg(document_key), '[]') from (
    select distinct a.document_key from public.session_actas a
     where not exists (select 1 from public.acta_passages p where p.document_key = a.document_key)
     limit p_limit) x
$$;

-- Búsqueda: en una acta concreta (gaceta + rango de páginas) o en todas. Cada fragmento lleva la sesión
-- del acta en que cae (la de inicio de página mayor que no supera la del fragmento). Los marcadores ⟦ ⟧
-- delimitan las coincidencias; la vista los convierte en negrita después de escapar el texto.
create or replace function public.bot_acta_search(p_query text, p_document_key text default null,
                                                  p_page_from integer default null, p_page_to integer default null,
                                                  p_limit integer default 8) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  q tsquery := websearch_to_tsquery('public.es_unaccent', coalesce(p_query, ''));
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  if q::text = '' then
    return jsonb_build_object('known_total', 0, 'items', '[]'::jsonb, 'query_terms', false);
  end if;
  return (
    with hits as (
      select p.*, ts_rank_cd(p.tsv, q) as rank,
             row_number() over (partition by p.document_key order by ts_rank_cd(p.tsv, q) desc, p.pdf_page_start) as per_doc
        from public.acta_passages p
       where p.tsv @@ q
         and (p_document_key is null or p.document_key = p_document_key)
         and (p_page_from is null or p.pdf_page_end >= p_page_from)
         and (p_page_to is null or p.pdf_page_start < p_page_to)
    ), top as (
      select * from hits where p_document_key is not null or per_doc <= 2
       order by rank desc, pdf_page_start limit least(p_limit, 20)
    )
    select jsonb_build_object(
      'known_total', (select count(*) from hits),
      'documents', (select count(distinct document_key) from hits),
      'items', coalesce((select jsonb_agg(jsonb_build_object(
          'document_key', t.document_key, 'pdf_page', t.pdf_page_start,
          'snippet', ts_headline('public.es_unaccent', t.text, q,
                                 'StartSel=⟦, StopSel=⟧, MaxWords=45, MinWords=18, MaxFragments=2, FragmentDelimiter=" … "'),
          'session', (select jsonb_build_object('session_date', a.session_date, 'corporation', a.corporation, 'body', a.body,
                               'body_key', a.body_key, 'acta_number', a.acta_number, 'acta_year', a.acta_year,
                               'url', a.gaceta_url, 'gaceta', split_part(a.document_key, ':', 4) || '/' ||
                               split_part(a.document_key, ':', 3))
                        from public.session_actas a
                       where a.document_key = t.document_key and (a.pdf_page is null or a.pdf_page <= t.pdf_page_start)
                       order by a.pdf_page desc nulls last limit 1))
          order by t.rank desc, t.pdf_page_start) from top t), '[]')));
end
$$;

revoke execute on function public.ingest_acta_passages(jsonb) from public, anon;
grant execute on function public.ingest_acta_passages(jsonb) to authenticated;
revoke execute on function public.ingest_actas_without_passages(integer) from public, anon, authenticated;
grant execute on function public.ingest_actas_without_passages(integer) to authenticated;
revoke execute on function public.bot_acta_search(text, text, integer, integer, integer) from public, anon;
grant execute on function public.bot_acta_search(text, text, integer, integer, integer) to authenticated;
