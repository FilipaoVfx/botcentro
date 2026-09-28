-- Registro atómico de documentos de texto derivados de capturas (fichas de proyecto) con su
-- revisión, origen, extracción, página, chunks, enlaces a proyecto y embeddings (SRS §8).
-- Idempotente por document_key: un documento ya registrado se omite. Una ficha que cambia de
-- contenido genera una revisión nueva del mismo documento (SRS-F12).

create or replace function public.ingest_register_text_documents(p_items jsonb)
returns table (created integer, revised integer, skipped integer, chunks integer, embeddings integer)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_item     jsonb;
  v_chunk    jsonb;
  v_doc      uuid;
  v_rev      uuid;
  v_ext      uuid;
  v_project  uuid;
  v_record   uuid;
  v_created  integer := 0;
  v_revised  integer := 0;
  v_skipped  integer := 0;
  v_chunks   integer := 0;
  v_embeds   integer := 0;
  v_is_new   boolean;
begin
  perform public.require_app_role(array['ingest_service']);
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) > 200 then
    raise exception using errcode = '22023', message = 'se espera un arreglo de hasta 200 documentos';
  end if;

  for v_item in select value from jsonb_array_elements(p_items) loop
    select d.id into v_doc from public.documents d where d.document_key = v_item ->> 'document_key';
    v_is_new := v_doc is null;
    if not v_is_new and exists (
      select 1 from public.document_revisions r where r.document_id = v_doc and r.blob_hash = v_item ->> 'content_hash'
    ) then
      v_skipped := v_skipped + 1;
      continue;
    end if;

    select sn.record_id into v_record from public.source_snapshots sn where sn.id = (v_item ->> 'snapshot_id')::uuid;
    if v_record is null then
      raise exception using errcode = 'P0002', message = 'captura de origen inexistente';
    end if;

    insert into public.blobs (content_hash, byte_size, detected_mime, object_key, stored_at, verified_at)
    values (v_item ->> 'content_hash', (v_item ->> 'byte_size')::bigint, 'text/plain', v_item ->> 'object_key',
            case when v_item ->> 'object_key' is not null then now() end,
            case when v_item ->> 'object_key' is not null then now() end)
    on conflict (content_hash) do nothing;

    if v_is_new then
      insert into public.documents (document_key, document_type, title)
      values (v_item ->> 'document_key', v_item ->> 'document_type', v_item ->> 'title')
      returning id into v_doc;
      v_created := v_created + 1;
    else
      update public.documents set title = v_item ->> 'title' where id = v_doc;
      v_revised := v_revised + 1;
    end if;

    insert into public.document_revisions (document_id, blob_hash, mime_type, byte_size, published_on, date_precision,
                                           supersedes_id)
    values (v_doc, v_item ->> 'content_hash', 'text/plain', (v_item ->> 'byte_size')::bigint,
            (v_item ->> 'published_on')::date,
            case when v_item ->> 'published_on' is null then 'unknown' else 'day' end,
            (select r.id from public.document_revisions r where r.document_id = v_doc
              order by r.created_at desc limit 1))
    returning id into v_rev;

    insert into public.document_origins (revision_id, snapshot_id, source_record_id, source_url)
    values (v_rev, (v_item ->> 'snapshot_id')::uuid, v_record, coalesce(v_item ->> 'source_url', ''));

    v_project := null;
    if v_item ->> 'project_subject_ref' is not null then
      v_project := public.camara_project_of((v_item ->> 'source_id')::uuid, v_item ->> 'project_subject_ref');
      if v_project is not null then
        insert into public.project_document_links (project_id, revision_id, relation_type, observation_id)
        values (v_project, v_rev, 'otro', (v_item ->> 'observation_id')::uuid)
        on conflict on constraint project_document_links_unique do nothing;
      end if;
    end if;

    insert into public.extraction_runs (id, revision_id, extractor_version, ocr_version, config_hash, status,
                                        quality_status, page_count, pages_ocr, started_at, finished_at)
    values ((v_item ->> 'extraction_id')::uuid, v_rev, v_item ->> 'extractor_version', 'none', v_item ->> 'config_hash', 'succeeded', 'accepted', 1, 0,
            now(), now())
    returning id into v_ext;

    insert into public.document_pages (extraction_id, pdf_page, text, char_start, char_end, method, quality_status)
    values (v_ext, 1, v_item ->> 'text', 0, char_length(v_item ->> 'text'), 'native', 'accepted');

    for v_chunk in select value from jsonb_array_elements(v_item -> 'chunks') loop
      insert into public.chunks (id, extraction_id, ordinal, text, text_hash, chunker_version, token_count,
                                 pdf_page_start, pdf_page_end, char_start, char_end, section_label, heading, quality_status)
      values ((v_chunk ->> 'id')::uuid, v_ext, (v_chunk ->> 'ordinal')::int, v_chunk ->> 'text', v_chunk ->> 'text_hash',
              v_chunk ->> 'chunker_version', (v_chunk ->> 'token_count')::int, 1, 1, (v_chunk ->> 'char_start')::int,
              (v_chunk ->> 'char_end')::int, v_chunk ->> 'section_label', v_chunk ->> 'heading', 'accepted');
      v_chunks := v_chunks + 1;
      if v_project is not null then
        insert into public.chunk_project_links (chunk_id, project_id, relation_basis)
        values ((v_chunk ->> 'id')::uuid, v_project, 'segment_link') on conflict do nothing;
      end if;
      if v_chunk ? 'embedding' then
        insert into public.chunk_embeddings (chunk_id, model_id, model_version, dimensions, index_namespace, embedding,
                                             state, indexed_at)
        values ((v_chunk ->> 'id')::uuid, v_item ->> 'model_id', v_item ->> 'model_version',
                (v_item ->> 'dimensions')::int, v_item ->> 'index_namespace', (v_chunk ->> 'embedding')::vector,
                'indexed', now());
        v_embeds := v_embeds + 1;
      end if;
    end loop;
  end loop;

  return query select v_created, v_revised, v_skipped, v_chunks, v_embeds;
end
$$;

-- Fichas de SRC-06 publicadas que aún no tienen documento (la más reciente por proyecto).
create or replace function public.ingest_pending_camara_fichas(p_limit integer default 200)
returns table (observation_id uuid, source_id uuid, snapshot_id uuid, subject_ref text, effective_date date,
               value_json jsonb)
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
begin
  perform public.require_app_role(array['ingest_service']);
  return query
  select o.id, o.source_id, o.first_snapshot_id, o.subject_ref, o.effective_date, o.value_json
    from (select distinct on (x.subject_ref) x.*
            from public.observations x
            join public.sources s on s.id = x.source_id and s.code = 'SRC-06'
           where x.predicate = 'project_profile' and x.status = 'published'
           order by x.subject_ref, x.last_observed_at desc) o
   where not exists (select 1 from public.documents d where d.document_key = 'camara-ficha:' || o.subject_ref)
   order by o.subject_ref
   limit least(p_limit, 1000);
end
$$;

revoke execute on function public.ingest_register_text_documents(jsonb), public.ingest_pending_camara_fichas(integer)
  from public, anon;
grant execute on function public.ingest_register_text_documents(jsonb), public.ingest_pending_camara_fichas(integer)
  to authenticated;
