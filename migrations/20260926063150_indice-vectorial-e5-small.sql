-- Base vectorial: registro de modelos de embedding e índice HNSW para multilingual-e5-small.
-- Decisión DEC-06 (provisional) en docs/decisiones.md; procedimiento en docs/embeddings.md.
--
-- chunk_embeddings.embedding no fija dimensión para admitir varios modelos (ADR 0001 §8). Cada
-- modelo activo tiene un índice HNSW parcial sobre la expresión embedding::vector(N) y una función
-- de búsqueda que usa esa misma expresión, para que el planificador pueda usar el índice.

create table public.embedding_models (
  model_id           text not null,
  model_version      text not null,
  dimensions         integer not null check (dimensions > 0),
  distance           text not null default 'cosine' check (distance in ('cosine')),
  query_prefix       text not null default '',
  passage_prefix     text not null default '',
  max_input_tokens   integer not null check (max_input_tokens > 0),
  chunker_version    text not null,
  runtime            text not null,
  status             text not null default 'candidate' check (status in ('candidate', 'active', 'retired')),
  evaluation_summary text,
  selected_at        timestamptz,
  created_at         timestamptz not null default now(),
  primary key (model_id, model_version),
  constraint embedding_models_selected check (status = 'candidate' or selected_at is not null)
);

-- Un solo modelo activo a la vez para la búsqueda semántica del bot.
create unique index embedding_models_single_active on public.embedding_models ((true)) where status = 'active';

alter table public.embedding_models enable row level security;
revoke all on public.embedding_models from anon, authenticated;
grant select on public.embedding_models to authenticated;
create policy select_by_role on public.embedding_models for select to authenticated
  using ((select public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])));

insert into public.embedding_models (model_id, model_version, dimensions, query_prefix, passage_prefix,
                                     max_input_tokens, chunker_version, runtime, status, evaluation_summary,
                                     selected_at)
values ('intfloat/multilingual-e5-small', 'onnx-614241f', 384, 'query: ', 'passage: ', 512,
        'chunker-v2-e5s', 'fastembed-onnx-cpu', 'active',
        'Prueba preliminar con 10 consultas sobre 7 descripciones reales de comisiones del Senado (SRC-01): '
        'acierto@1 9/10, MRR 0,95, 194 ms por pasaje largo en 2 CPU. e5-large 10/10 a 2152 ms; '
        'potion 8/10 a 2 ms. Selección provisional hasta el corpus de evaluación del SRS §16.1.',
        now());

-- Integridad: los vectores de un modelo registrado deben tener su dimensión declarada.
create or replace function public.check_embedding_dimensions() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_dims integer;
begin
  select m.dimensions into v_dims from public.embedding_models m
   where m.model_id = new.model_id and m.model_version = new.model_version;
  if v_dims is null then
    raise exception using errcode = '22023',
      message = format('modelo de embedding no registrado: %s %s', new.model_id, new.model_version);
  end if;
  if new.dimensions <> v_dims then
    raise exception using errcode = '22023',
      message = format('dimensión %s no coincide con la del modelo (%s)', new.dimensions, v_dims);
  end if;
  return new;
end
$$;

create trigger chunk_embeddings_dimensions before insert or update on public.chunk_embeddings
  for each row execute function public.check_embedding_dimensions();

-- Índice ANN parcial del modelo activo.
create index chunk_embeddings_e5_small_hnsw on public.chunk_embeddings
  using hnsw ((embedding::vector(384)) vector_cosine_ops)
  where model_id = 'intfloat/multilingual-e5-small' and state = 'indexed';

-- Búsqueda semántica con el índice de e5-small. SECURITY INVOKER: aplica RLS del llamante.
create or replace function public.search_chunks_e5_small(
  p_embedding  vector(384),
  p_project_id uuid default null,
  p_limit      integer default 30
)
returns table (
  chunk_id       uuid,
  distance       double precision,
  revision_id    uuid,
  extraction_id  uuid,
  pdf_page_start integer,
  pdf_page_end   integer,
  section_label  text
)
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select c.id, n.distance, e.revision_id, c.extraction_id, c.pdf_page_start, c.pdf_page_end, c.section_label
    from (
      select ce.chunk_id, (ce.embedding::vector(384) <=> p_embedding)::double precision as distance
        from public.chunk_embeddings ce
       where ce.model_id = 'intfloat/multilingual-e5-small' and ce.state = 'indexed'
       order by ce.embedding::vector(384) <=> p_embedding
       limit least(greatest(coalesce(p_limit, 30), 1), 100) * 4
    ) n
    join public.chunks c on c.id = n.chunk_id
    join public.extraction_runs e on e.id = c.extraction_id
    join public.document_revisions r on r.id = e.revision_id
   where c.quality_status = 'accepted'
     and r.withdrawn_at is null
     and (p_project_id is null or exists (
           select 1 from public.chunk_project_links l
            where l.chunk_id = c.id and l.project_id = p_project_id))
   order by n.distance, c.id
   limit least(greatest(coalesce(p_limit, 30), 1), 100)
$$;

revoke execute on function public.search_chunks_e5_small(vector, uuid, integer),
  public.check_embedding_dimensions() from public, anon;
grant execute on function public.search_chunks_e5_small(vector, uuid, integer) to authenticated;
