-- Búsqueda en actas: sin comillas ni operadores, primero la frase exacta («jurisdicción especial para la
-- paz», «Paloma Valencia»); si no aparece, todas las palabras en cualquier orden. La respuesta dice cuál.
create or replace function public.bot_acta_search(p_query text, p_document_key text default null,
                                                  p_page_from integer default null, p_page_to integer default null,
                                                  p_limit integer default 8) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_text text := btrim(coalesce(p_query, ''));
  q      tsquery;
  v_mode text := 'palabras';
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  if v_text !~ '["|]| or | -' and array_length(regexp_split_to_array(v_text, '\s+'), 1) > 1 then
    q := phraseto_tsquery('public.es_unaccent', v_text);
    if exists (select 1 from public.acta_passages p
                where p.tsv @@ q and (p_document_key is null or p.document_key = p_document_key)
                  and (p_page_from is null or p.pdf_page_end >= p_page_from)
                  and (p_page_to is null or p.pdf_page_start < p_page_to)) then
      v_mode := 'frase';
    else
      q := null;
    end if;
  end if;
  q := coalesce(q, websearch_to_tsquery('public.es_unaccent', v_text));
  if q::text = '' then
    return jsonb_build_object('known_total', 0, 'items', '[]'::jsonb, 'mode', 'vacia');
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
      'mode', v_mode,
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
