-- DEC-12: los vectores de documentos se publican en Qdrant Cloud (plan gratuito) y la base conserva
-- documentos, chunks citables y enlaces a proyectos. Esta migración:
--   1. expone los chunks con su vector y proyectos para sincronizarlos con Qdrant;
--   2. elimina índices sin uso o duplicados (medidos con pg_stat_user_indexes: 0 lecturas).

create or replace function public.ingest_export_chunks(p_after uuid default null, p_limit integer default 500)
returns table (chunk_id uuid, text text, heading text, section_label text, document_key text, document_type text,
               title text, published_on date, source_url text, source_code text, project_ids uuid[],
               embedding text)
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
begin
  perform public.require_app_role(array['ingest_service']);
  return query
  select c.id, c.text, c.heading, c.section_label, d.document_key, d.document_type, d.title, r.published_on,
         o.source_url, s.code,
         coalesce((select array_agg(l.project_id order by l.project_id) from public.chunk_project_links l
                    where l.chunk_id = c.id), '{}'),
         (select e.embedding::text from public.chunk_embeddings e
           where e.chunk_id = c.id and e.state = 'indexed' and e.index_namespace = 'e5-small-v1' limit 1)
    from public.chunks c
    join public.extraction_runs x on x.id = c.extraction_id
    join public.document_revisions r on r.id = x.revision_id
    join public.documents d on d.id = r.document_id
    left join lateral (select og.source_url, og.snapshot_id from public.document_origins og
                        where og.revision_id = r.id order by og.created_at limit 1) o on true
    left join public.source_snapshots sn on sn.id = o.snapshot_id
    left join public.source_records sr on sr.id = sn.record_id
    left join public.sources s on s.id = sr.source_id
   where c.quality_status = 'accepted' and (p_after is null or c.id > p_after)
   order by c.id
   limit least(p_limit, 2000);
end
$$;

revoke execute on function public.ingest_export_chunks(uuid, integer) from public, anon;
grant execute on function public.ingest_export_chunks(uuid, integer) to authenticated;

-- (source_id, subject_type, subject_ref): 31 MB y 0 lecturas; las búsquedas por referencia usan
-- observations_source_predicate_idx.
drop index if exists public.observations_subject_ref_idx;
-- Duplicado exacto de vote_observations_observation_id_idx.
drop index if exists public.vote_observations_observation_id_fk_idx;

-- Proyectos enlazados a un conjunto de chunks (carga útil de Qdrant al publicar documentos nuevos).
create or replace function public.ingest_chunk_projects(p_chunk_ids uuid[])
returns table (chunk_id uuid, project_ids uuid[])
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select c.id, coalesce(array_agg(l.project_id order by l.project_id) filter (where l.project_id is not null), '{}')
    from unnest(p_chunk_ids) c(id)
    left join public.chunk_project_links l on l.chunk_id = c.id
   where public.has_app_role(array['ingest_service'])
   group by c.id
$$;

revoke execute on function public.ingest_chunk_projects(uuid[]) from public, anon;
grant execute on function public.ingest_chunk_projects(uuid[]) to authenticated;
