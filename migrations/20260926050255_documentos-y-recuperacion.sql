-- Documentos, revisiones inmutables, orígenes, gacetas, segmentos, extracción, chunks y embeddings.
-- Requisitos: SRS-F12..F15, F18, §6.5, §8. Pruebas: T-12..T-16, T-18.

-- Objeto físico direccionado por contenido: dos URLs con los mismos bytes comparten blob sin
-- perder procedencia separada (document_origins). object_key es null si no se conserva el original.
create table public.blobs (
  content_hash text primary key check (content_hash ~ '^[0-9a-f]{64}$'),
  byte_size    bigint not null check (byte_size >= 0),
  detected_mime text,
  object_key   text,
  stored_at    timestamptz,
  verified_at  timestamptz,
  created_at   timestamptz not null default now(),
  constraint blobs_stored_pair check ((object_key is null) = (stored_at is null))
);

create table public.documents (
  id            uuid primary key default gen_random_uuid(),
  document_key  text not null unique,
  document_type text not null
    check (document_type in ('texto_radicado', 'ponencia', 'texto_aprobado', 'gaceta', 'acta',
                             'orden_del_dia', 'informe', 'concepto', 'transcripcion', 'otro')),
  title         text,
  publisher_id  uuid references public.organizations (id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger documents_touch before update on public.documents
  for each row execute function public.set_updated_at();

-- SRS-F12: una URL que cambia bytes genera revisión nueva; la revisión es inmutable salvo
-- las marcas de retiro/reemplazo, que solo cambian por RPC auditada.
create table public.document_revisions (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid not null references public.documents (id),
  blob_hash        text not null references public.blobs (content_hash),
  mime_type        text not null,
  byte_size        bigint not null check (byte_size >= 0),
  published_on     date,
  date_precision   text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  supersedes_id    uuid references public.document_revisions (id),
  withdrawn_at     timestamptz,
  withdrawal_reason text,
  created_at       timestamptz not null default now(),
  unique (document_id, blob_hash),
  constraint document_revisions_precision check ((date_precision = 'unknown') = (published_on is null)),
  constraint document_revisions_withdrawal check ((withdrawn_at is null) = (withdrawal_reason is null)),
  constraint document_revisions_not_self check (supersedes_id <> id)
);

create index document_revisions_document_idx on public.document_revisions (document_id);

-- Solo withdrawn_at/withdrawal_reason/supersedes_id pueden cambiar; el contenido no.
create or replace function public.guard_document_revision_update() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if new.document_id is distinct from old.document_id
     or new.blob_hash is distinct from old.blob_hash
     or new.mime_type is distinct from old.mime_type
     or new.byte_size is distinct from old.byte_size
     or new.created_at is distinct from old.created_at then
    raise exception using errcode = 'BC001', message = 'el contenido de una revisión documental es inmutable';
  end if;
  return new;
end
$$;

create trigger document_revisions_guard before update on public.document_revisions
  for each row execute function public.guard_document_revision_update();
create trigger document_revisions_no_delete before delete on public.document_revisions
  for each row execute function public.forbid_mutation();

create table public.document_origins (
  revision_id      uuid not null references public.document_revisions (id),
  snapshot_id      uuid not null references public.source_snapshots (id),
  source_record_id uuid not null references public.source_records (id),
  source_url       text not null,
  created_at       timestamptz not null default now(),
  primary key (revision_id, snapshot_id)
);

create index document_origins_record_idx on public.document_origins (source_record_id);

create table public.gacetas (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid not null unique references public.documents (id),
  series           text not null default 'congreso',
  number           text not null,
  publication_year smallint not null check (publication_year between 1991 and 2100),
  unique (series, publication_year, number)
);

-- Segmentos de una revisión (gaceta multiexpediente, SRS-F14 y T-14).
create table public.document_segments (
  id            uuid primary key default gen_random_uuid(),
  revision_id   uuid not null references public.document_revisions (id),
  page_start    integer not null check (page_start >= 1),
  page_end      integer not null,
  section_label text,
  segment_kind  text not null default 'expediente'
    check (segment_kind in ('expediente', 'seccion', 'anexo', 'otro')),
  created_at    timestamptz not null default now(),
  unique (id, revision_id),
  constraint document_segments_range check (page_end >= page_start)
);

create index document_segments_revision_idx on public.document_segments (revision_id);

-- Relación proyecto–documento con evidencia. Si hay segmento, debe ser de la misma revisión.
create table public.project_document_links (
  id             uuid primary key default gen_random_uuid(),
  project_id     uuid not null references public.projects (id),
  revision_id    uuid not null references public.document_revisions (id),
  segment_id     uuid,
  relation_type  text not null
    check (relation_type in ('texto_radicado', 'ponencia', 'texto_aprobado', 'acta', 'publicacion',
                             'mencion', 'otro')),
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint project_document_links_segment
    foreign key (segment_id, revision_id) references public.document_segments (id, revision_id),
  constraint project_document_links_unique
    unique nulls not distinct (project_id, revision_id, segment_id, relation_type)
);

create index project_document_links_revision_idx on public.project_document_links (revision_id);

-- Versión legislativa explícita (no sinónimo de descarga ni de reextracción, T-15).
create table public.project_versions (
  id             uuid primary key default gen_random_uuid(),
  project_id     uuid not null references public.projects (id),
  revision_id    uuid not null references public.document_revisions (id),
  segment_id     uuid,
  corporation_id uuid references public.corporations (id),
  stage          text not null,
  version_label  text not null,
  version_date   date,
  date_precision text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint project_versions_segment
    foreign key (segment_id, revision_id) references public.document_segments (id, revision_id),
  constraint project_versions_precision check ((date_precision = 'unknown') = (version_date is null)),
  constraint project_versions_unique
    unique nulls not distinct (project_id, revision_id, segment_id, stage, version_label)
);

create index project_versions_project_idx on public.project_versions (project_id, version_date);

-- Una ejecución por revisión y receta (extractor, OCR, configuración).
create table public.extraction_runs (
  id                uuid primary key default gen_random_uuid(),
  revision_id       uuid not null references public.document_revisions (id),
  extractor_version text not null,
  ocr_version       text not null default 'none',
  config_hash       text not null,
  status            text not null default 'pending'
    check (status in ('pending', 'running', 'succeeded', 'partial', 'failed')),
  quality_status    text check (quality_status in ('accepted', 'review_required', 'failed')),
  page_count        integer check (page_count >= 0),
  pages_ocr         integer check (pages_ocr >= 0),
  error_code        text,
  started_at        timestamptz,
  finished_at       timestamptz,
  created_at        timestamptz not null default now(),
  unique (revision_id, extractor_version, ocr_version, config_hash),
  constraint extraction_runs_times check (finished_at >= started_at),
  constraint extraction_runs_quality_when_done
    check (status in ('pending', 'running') or quality_status is not null)
);

-- Texto por página con método, calidad y offsets dentro del texto completo de la extracción
-- (páginas unidas por '\f'). pdf_page empieza en 1.
create table public.document_pages (
  id             uuid primary key default gen_random_uuid(),
  extraction_id  uuid not null references public.extraction_runs (id),
  pdf_page       integer not null check (pdf_page >= 1),
  printed_label  text,
  text           text,
  char_start     integer not null check (char_start >= 0),
  char_end       integer not null,
  method         text not null check (method in ('native', 'ocr', 'none')),
  quality_status text not null check (quality_status in ('accepted', 'review_required', 'failed')),
  confidence     numeric(5, 4) check (confidence between 0 and 1),
  error_code     text,
  unique (extraction_id, pdf_page),
  constraint document_pages_char_range check (char_end >= char_start)
);

-- Chunks con localizador estable (SRS-F14). El id lo calcula el chunker (UUID determinista por
-- receta y fragmento). search_vector alimenta la búsqueda léxica en español.
create table public.chunks (
  id              uuid primary key,
  extraction_id   uuid not null references public.extraction_runs (id),
  segment_id      uuid references public.document_segments (id),
  ordinal         integer not null check (ordinal >= 0),
  text            text not null,
  text_hash       text not null check (text_hash ~ '^[0-9a-f]{64}$'),
  chunker_version text not null,
  token_count     integer not null check (token_count > 0),
  pdf_page_start  integer not null check (pdf_page_start >= 1),
  pdf_page_end    integer not null,
  char_start      integer not null check (char_start >= 0),
  char_end        integer not null,
  section_label   text,
  heading         text,
  quality_status  text not null default 'accepted'
    check (quality_status in ('accepted', 'review_required', 'failed')),
  search_vector   tsvector generated always as (to_tsvector('spanish'::regconfig, text)) stored,
  created_at      timestamptz not null default now(),
  unique (extraction_id, chunker_version, ordinal),
  constraint chunks_page_range check (pdf_page_end >= pdf_page_start),
  constraint chunks_char_range check (char_end > char_start)
);

create index chunks_search_idx on public.chunks using gin (search_vector);
create index chunks_segment_idx on public.chunks (segment_id);

-- Evita atribuir toda una gaceta a un proyecto (SRS-F14).
create table public.chunk_project_links (
  chunk_id       uuid not null references public.chunks (id),
  project_id     uuid not null references public.projects (id),
  relation_basis text not null check (relation_basis in ('segment_link', 'explicit_mention', 'manual')),
  created_at     timestamptz not null default now(),
  primary key (chunk_id, project_id)
);

create index chunk_project_links_project_idx on public.chunk_project_links (project_id);

-- Embeddings versionados por modelo (SRS §15). La columna no fija dimensión para admitir
-- modelos distintos; el índice ANN se crea por modelo cuando se elija (DEC-06), p. ej.:
--   create index chunk_embeddings_<modelo>_hnsw on public.chunk_embeddings
--     using hnsw ((embedding::vector(1536)) vector_cosine_ops) where model_id = '<modelo>';
create table public.chunk_embeddings (
  chunk_id        uuid not null references public.chunks (id),
  model_id        text not null,
  model_version   text not null,
  dimensions      integer not null check (dimensions > 0),
  index_namespace text not null,
  embedding       vector,
  state           text not null default 'pending' check (state in ('pending', 'indexed', 'stale', 'deleted')),
  indexed_at      timestamptz,
  created_at      timestamptz not null default now(),
  primary key (chunk_id, model_id, model_version),
  constraint chunk_embeddings_dims check (embedding is null or vector_dims(embedding) = dimensions),
  constraint chunk_embeddings_indexed check (state <> 'indexed' or (embedding is not null and indexed_at is not null))
);

create index chunk_embeddings_namespace_idx on public.chunk_embeddings (index_namespace, state);

-- Caché de comparaciones por versiones exactas y receta (SRS-F15).
create table public.document_comparisons (
  id               uuid primary key default gen_random_uuid(),
  left_version_id  uuid not null references public.project_versions (id),
  right_version_id uuid not null references public.project_versions (id),
  recipe_version   text not null,
  status           text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed')),
  result_object_key text,
  error_code       text,
  requested_at     timestamptz not null default now(),
  finished_at      timestamptz,
  unique (left_version_id, right_version_id, recipe_version),
  constraint document_comparisons_distinct check (left_version_id <> right_version_id)
);

-- Relaciones evento–documento e intervención–texto, y FKs pendientes de la evidencia.
create table public.event_documents (
  event_id      uuid not null references public.events (id),
  revision_id   uuid not null references public.document_revisions (id),
  relation_type text not null,
  primary key (event_id, revision_id, relation_type)
);

create index event_documents_revision_idx on public.event_documents (revision_id);

alter table public.interventions
  add constraint interventions_text_revision_fk
  foreign key (text_revision_id) references public.document_revisions (id);

alter table public.evidence
  add constraint evidence_document_revision_fk
  foreign key (document_revision_id) references public.document_revisions (id),
  add constraint evidence_extraction_fk
  foreign key (extraction_id) references public.extraction_runs (id),
  add constraint evidence_chunk_fk
  foreign key (chunk_id) references public.chunks (id);
