-- Registro de fuentes, perfiles de uso, cobertura, ejecuciones, capturas, observaciones y evidencia.
-- Requisitos: SRS-F01..F06, F10, §6.2. Pruebas: T-01, T-02, T-12, T-31.

create table public.corporations (
  id   uuid primary key default gen_random_uuid(),
  code text not null unique check (code ~ '^[a-z_]+$'),
  name text not null
);

-- Legislatura y cuatrienio son conceptos distintos (SRS §6.3).
create table public.legislative_periods (
  id          uuid primary key default gen_random_uuid(),
  period_type text not null check (period_type in ('cuatrienio', 'legislatura')),
  label       text not null,
  start_date  date not null,
  end_date    date not null,
  constraint legislative_periods_range check (end_date >= start_date),
  unique (period_type, label)
);

-- SRS-F01. base_url puede faltar mientras la fuente es candidata; la activación lo exige.
create table public.sources (
  id                      uuid primary key default gen_random_uuid(),
  code                    text not null unique check (code ~ '^SRC-[0-9]{2,}$'),
  name                    text not null,
  authority               text not null
    check (authority in ('primary', 'secondary', 'public_discourse', 'normative')),
  phase                   text not null
    check (phase in ('mvp', 'mvp_conditional', 'phase_2', 'phase_3')),
  base_url                text check (base_url ~ '^https?://'),
  allowed_domains         text[] not null default '{}',
  adapter                 text,
  adapter_version         text,
  state                   text not null default 'candidate'
    check (state in ('candidate', 'validated', 'active', 'degraded', 'suspended', 'retired')),
  state_reason            text,
  supported_objects       text[] not null default '{}',
  poll_interval           interval check (poll_interval >= interval '1 minute'),
  request_timeout_seconds integer not null default 30 check (request_timeout_seconds between 1 and 600),
  max_concurrency         smallint not null default 2 check (max_concurrency between 1 and 16),
  owner                   text,
  config_ref              text,
  policy_id               uuid,
  last_checked_at         timestamptz,
  last_success_at         timestamptz,
  notes                   text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  row_version             integer not null default 1
);
comment on column public.sources.config_ref is
  'Referencia a configuración versionada. Los secretos viven fuera de la base (SRS-N02).';

create trigger sources_touch before update on public.sources
  for each row execute function public.touch_versioned_row();

-- SRS-F02. Cada operación es allowed/denied/unknown; unknown nunca equivale a aprobación.
-- Versionadas y append-only: un cambio de política es una versión nueva.
create table public.source_policies (
  id                   uuid primary key default gen_random_uuid(),
  source_id            uuid not null references public.sources (id),
  version              integer not null check (version >= 1),
  capture_metadata     text not null default 'unknown' check (capture_metadata in ('allowed', 'denied', 'unknown')),
  download_files       text not null default 'unknown' check (download_files in ('allowed', 'denied', 'unknown')),
  retain_content       text not null default 'unknown' check (retain_content in ('allowed', 'denied', 'unknown')),
  generate_derivatives text not null default 'unknown' check (generate_derivatives in ('allowed', 'denied', 'unknown')),
  index_content        text not null default 'unknown' check (index_content in ('allowed', 'denied', 'unknown')),
  redistribute         text not null default 'unknown' check (redistribute in ('allowed', 'denied', 'unknown')),
  retention_policy     text,
  evidence_url         text,
  reviewer             text,
  reviewed_at          timestamptz,
  approval_reason      text,
  created_at           timestamptz not null default now(),
  unique (source_id, version),
  unique (id, source_id),
  constraint source_policies_review_pair check ((reviewer is null) = (reviewed_at is null)),
  constraint source_policies_download_needs_capture
    check (download_files <> 'allowed' or capture_metadata = 'allowed'),
  constraint source_policies_retain_needs_download
    check (retain_content <> 'allowed' or download_files = 'allowed'),
  constraint source_policies_derivatives_need_download
    check (generate_derivatives <> 'allowed' or download_files = 'allowed'),
  constraint source_policies_index_needs_derivatives
    check (index_content <> 'allowed' or generate_derivatives = 'allowed')
);

create trigger source_policies_append_only before update or delete on public.source_policies
  for each row execute function public.forbid_mutation();

alter table public.sources
  add constraint sources_policy_belongs_to_source
  foreign key (policy_id, id) references public.source_policies (id, source_id);

-- Cobertura declarada (PRD §3.7). expected_count puede ser desconocido; si se conoce, se
-- declara de dónde sale el denominador (inventario enumerado de la fuente, T-31).
create table public.coverage_scopes (
  id                   uuid primary key default gen_random_uuid(),
  source_id            uuid not null references public.sources (id),
  object_type          text not null,
  corporation_id       uuid references public.corporations (id),
  from_date            date,
  to_date              date,
  expected_count       integer check (expected_count >= 0),
  expected_count_basis text,
  observed_count       integer not null default 0 check (observed_count >= 0),
  status               text not null default 'planned'
    check (status in ('planned', 'in_progress', 'partial', 'covered', 'not_covered', 'unknown')),
  limitations          text,
  last_measured_at     timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint coverage_scopes_range check (to_date >= from_date),
  constraint coverage_scopes_denominator_basis
    check (expected_count is null or expected_count_basis is not null)
);

create index coverage_scopes_source_idx on public.coverage_scopes (source_id);
create trigger coverage_scopes_touch before update on public.coverage_scopes
  for each row execute function public.set_updated_at();

-- SRS-F03/F04. cursor_after solo se fija al confirmar un lote persistido (ingest_commit_cursor).
create table public.ingestion_runs (
  id                uuid primary key default gen_random_uuid(),
  source_id         uuid not null references public.sources (id),
  coverage_scope_id uuid references public.coverage_scopes (id),
  mode              text not null check (mode in ('incremental', 'backfill', 'reconciliation', 'validation')),
  connector_version text not null,
  status            text not null default 'running'
    check (status in ('running', 'succeeded', 'partial', 'failed', 'cancelled')),
  cursor_before     jsonb,
  cursor_after      jsonb,
  items_discovered  integer not null default 0 check (items_discovered >= 0),
  items_fetched     integer not null default 0 check (items_fetched >= 0),
  items_unchanged   integer not null default 0 check (items_unchanged >= 0),
  items_normalized  integer not null default 0 check (items_normalized >= 0),
  items_quarantined integer not null default 0 check (items_quarantined >= 0),
  items_failed      integer not null default 0 check (items_failed >= 0),
  error_code        text,
  triggered_by      uuid references auth.users (id),
  trace_id          uuid not null default gen_random_uuid(),
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  constraint ingestion_runs_times check (finished_at >= started_at),
  constraint ingestion_runs_finished check ((status = 'running') = (finished_at is null))
);

create index ingestion_runs_source_idx on public.ingestion_runs (source_id, started_at desc);

-- Recurso lógico de una fuente (ficha, página de listado, PDF...). Clave estable por fuente.
create table public.source_records (
  id               uuid primary key default gen_random_uuid(),
  source_id        uuid not null references public.sources (id),
  record_type      text not null,
  logical_key      text not null,
  external_id      text,
  canonical_url    text,
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  withdrawn_at     timestamptz,
  withdrawal_basis text,
  unique (source_id, logical_key),
  constraint source_records_withdrawal check ((withdrawn_at is null) = (withdrawal_basis is null))
);

create index source_records_external_idx on public.source_records (source_id, external_id);

-- SRS-F06. Capturas inmutables. object_key es null si la política no permite conservar el original.
create table public.source_snapshots (
  id              uuid primary key default gen_random_uuid(),
  record_id       uuid not null references public.source_records (id),
  run_id          uuid references public.ingestion_runs (id),
  requested_url   text not null,
  final_url       text not null,
  http_status     smallint check (http_status between 100 and 599),
  content_hash    text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  byte_size       bigint not null check (byte_size >= 0),
  mime_type       text,
  object_key      text,
  etag            text,
  last_modified   text,
  adapter_version text not null,
  fetched_at      timestamptz not null,
  created_at      timestamptz not null default now(),
  unique (record_id, content_hash)
);

create index source_snapshots_run_idx on public.source_snapshots (run_id);
create trigger source_snapshots_append_only before update or delete on public.source_snapshots
  for each row execute function public.forbid_mutation();

-- Distingue una comprobación exitosa sin cambios de una captura nueva (SRS §6.2).
-- 'reverted': el contenido volvió a bytes ya capturados antes (A→B→A); cuenta como cambio.
-- seq ordena las comprobaciones de un registro aunque compartan instante.
create table public.source_checks (
  id          uuid primary key default gen_random_uuid(),
  seq         bigint generated always as identity,
  record_id   uuid not null references public.source_records (id),
  run_id      uuid references public.ingestion_runs (id),
  checked_at  timestamptz not null default now(),
  result      text not null
    check (result in ('new_snapshot', 'reverted', 'unchanged', 'not_modified', 'not_found', 'error',
                      'withdrawal_signal')),
  snapshot_id uuid references public.source_snapshots (id),
  http_status smallint check (http_status between 100 and 599),
  error_code  text,
  detail      text,
  constraint source_checks_snapshot_required
    check (result not in ('new_snapshot', 'reverted', 'unchanged', 'not_modified') or snapshot_id is not null)
);

create index source_checks_record_idx on public.source_checks (record_id, seq desc);
create trigger source_checks_append_only before update or delete on public.source_checks
  for each row execute function public.forbid_mutation();

-- Observación: afirmación capturada de una fuente. observation_key (hash canónico de fuente,
-- sujeto, predicado, valor y fecha efectiva) hace idempotente la reingesta (T-02): volver a ver
-- el mismo hecho solo actualiza last_observed_at y acumula evidencia.
-- Fechas con precisión menor al día se guardan como el primer día del mes/año.
create table public.observations (
  id                uuid primary key default gen_random_uuid(),
  observation_key   text not null unique check (observation_key ~ '^[0-9a-f]{64}$'),
  source_id         uuid not null references public.sources (id),
  first_snapshot_id uuid not null references public.source_snapshots (id),
  authority         text not null
    check (authority in ('primary', 'secondary', 'public_discourse', 'normative')),
  subject_type      text not null,
  subject_id        uuid,
  subject_ref       text,
  predicate         text not null,
  value_json        jsonb not null,
  value_raw         text,
  effective_date    date,
  date_precision    text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  effective_at      timestamptz,
  published_on      date,
  first_observed_at timestamptz not null,
  last_observed_at  timestamptz not null,
  status            text not null default 'candidate'
    check (status in ('candidate', 'published', 'quarantined', 'superseded', 'retracted')),
  status_reason     text,
  parser_version    text not null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint observations_subject check (subject_id is not null or subject_ref is not null),
  constraint observations_seen_order check (last_observed_at >= first_observed_at),
  constraint observations_precision check ((date_precision = 'unknown') = (effective_date is null)),
  constraint observations_instant_has_date check (effective_at is null or effective_date is not null),
  constraint observations_value_size check (pg_column_size(value_json) <= 8192)
);

create index observations_subject_idx on public.observations (subject_type, subject_id, predicate);
create index observations_subject_ref_idx on public.observations (source_id, subject_type, subject_ref);
create index observations_first_snapshot_idx on public.observations (first_snapshot_id);
create index observations_pending_idx on public.observations (status) where status <> 'published';
create trigger observations_touch before update on public.observations
  for each row execute function public.set_updated_at();

-- Evidencia localizable (SRS §9.3). Localizador en columnas tipadas; las FK hacia documentos
-- se agregan en la migración documental.
create table public.evidence (
  id                   uuid primary key default gen_random_uuid(),
  evidence_key         text not null unique check (evidence_key ~ '^[0-9a-f]{64}$'),
  kind                 text not null check (kind in ('source_record', 'document_passage')),
  snapshot_id          uuid references public.source_snapshots (id),
  document_revision_id uuid,
  extraction_id        uuid,
  chunk_id             uuid,
  record_pointer       text,
  pdf_page_start       integer check (pdf_page_start >= 1),
  pdf_page_end         integer,
  printed_label        text,
  section_label        text,
  char_start           integer check (char_start >= 0),
  char_end             integer,
  excerpt_hash         text check (excerpt_hash ~ '^[0-9a-f]{64}$'),
  created_at           timestamptz not null default now(),
  constraint evidence_has_origin check (snapshot_id is not null or document_revision_id is not null),
  constraint evidence_record_needs_snapshot check (kind <> 'source_record' or snapshot_id is not null),
  constraint evidence_passage_locator check (
    kind <> 'document_passage'
    or (document_revision_id is not null and extraction_id is not null and pdf_page_start is not null)
  ),
  constraint evidence_page_range check (pdf_page_end >= pdf_page_start),
  constraint evidence_char_range check (char_end > char_start)
);

create index evidence_snapshot_idx on public.evidence (snapshot_id);
create index evidence_revision_idx on public.evidence (document_revision_id);
create trigger evidence_append_only before update or delete on public.evidence
  for each row execute function public.forbid_mutation();

create table public.observation_evidence (
  observation_id uuid not null references public.observations (id),
  evidence_id    uuid not null references public.evidence (id),
  created_at     timestamptz not null default now(),
  primary key (observation_id, evidence_id)
);

create index observation_evidence_evidence_idx on public.observation_evidence (evidence_id);
create trigger observation_evidence_append_only before update or delete on public.observation_evidence
  for each row execute function public.forbid_mutation();

-- Una observación publicada requiere evidencia (SRS §6.2). Diferido al commit para permitir
-- crear observación y evidencia en la misma transacción.
create or replace function public.check_published_observation_has_evidence() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if new.status = 'published' and not exists (
    select 1 from public.observation_evidence oe where oe.observation_id = new.id
  ) then
    raise exception using
      errcode = 'BC003',
      message = format('la observación %s no puede publicarse sin evidencia', new.id);
  end if;
  return null;
end
$$;

create constraint trigger observations_published_need_evidence
  after insert or update of status on public.observations
  deferrable initially deferred
  for each row execute function public.check_published_observation_has_evidence();

-- SRS §3.1: ningún conector activo sin perfil validado. T-01: la activación se rechaza con
-- diagnóstico concreto. El dominio Python produce el mismo diagnóstico antes; este trigger es
-- la defensa en profundidad.
create or replace function public.enforce_source_activation() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  pol      public.source_policies%rowtype;
  problems text[] := '{}';
begin
  if new.state not in ('validated', 'active', 'degraded') then
    return new;
  end if;
  if tg_op = 'UPDATE'
     and old.state = new.state
     and old.policy_id is not distinct from new.policy_id
     and old.base_url is not distinct from new.base_url
     and old.allowed_domains = new.allowed_domains
     and old.adapter is not distinct from new.adapter
     and old.adapter_version is not distinct from new.adapter_version
     and old.owner is not distinct from new.owner then
    return new;
  end if;

  if new.policy_id is null then
    problems := problems || 'sin perfil de uso asociado'::text;
  else
    select * into pol from public.source_policies where id = new.policy_id;
    if pol.reviewed_at is null then
      problems := problems || 'perfil de uso sin revisión registrada'::text;
    end if;
    if pol.capture_metadata <> 'allowed' then
      problems := problems || format('captura de metadata no aprobada (%s)', pol.capture_metadata);
    end if;
  end if;

  if not exists (select 1 from public.coverage_scopes c where c.source_id = new.id) then
    problems := problems || 'sin alcance de cobertura declarado'::text;
  end if;

  if new.state in ('active', 'degraded') then
    if new.base_url is null then
      problems := problems || 'sin URL de entrada'::text;
    end if;
    if cardinality(new.allowed_domains) = 0 then
      problems := problems || 'sin dominios permitidos'::text;
    end if;
    if new.adapter is null or new.adapter_version is null then
      problems := problems || 'sin adaptador versionado'::text;
    end if;
    if new.owner is null then
      problems := problems || 'sin responsable'::text;
    end if;
  end if;

  if cardinality(problems) > 0 then
    raise exception using
      errcode = 'BC002',
      message = format('activación de %s rechazada', new.code),
      detail = array_to_string(problems, '; ');
  end if;
  return new;
end
$$;

create trigger sources_enforce_activation before insert or update on public.sources
  for each row execute function public.enforce_source_activation();

-- Cobertura sin denominador inventado (T-31): el porcentaje solo existe si hay conteo esperado.
create view public.coverage_status
with (security_invoker = true) as
select
  c.id,
  s.code as source_code,
  c.object_type,
  co.code as corporation,
  c.from_date,
  c.to_date,
  c.status,
  c.observed_count,
  c.expected_count,
  c.expected_count_basis,
  case
    when c.expected_count is null or c.expected_count = 0 then null
    else round(100.0 * c.observed_count / c.expected_count, 1)
  end as observed_pct,
  c.limitations,
  c.last_measured_at
from public.coverage_scopes c
join public.sources s on s.id = c.source_id
left join public.corporations co on co.id = c.corporation_id;
