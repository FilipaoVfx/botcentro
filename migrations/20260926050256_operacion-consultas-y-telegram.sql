-- Operación: principales, identidades de Telegram, cola de trabajos, revisión, auditoría,
-- consultas, entrega y presupuesto.
-- Requisitos: SRS-F16, F22..F26, N05, N17, §6.2, §6.6, §7.3, §11. Pruebas: T-03, T-21..T-24, T-29.

-- Principal en cuyo nombre se consulta: usuario de Telegram, usuario de InsForge Auth (API) o
-- ejecución de evaluación. Los usuarios de Telegram no tienen sesión de InsForge.
create table public.principals (
  id            uuid primary key default gen_random_uuid(),
  kind          text not null check (kind in ('telegram_user', 'auth_user', 'evaluation')),
  auth_user_id  uuid unique references auth.users (id),
  display_label text,
  disabled_at   timestamptz,
  created_at    timestamptz not null default now(),
  constraint principals_auth_user check ((kind = 'auth_user') = (auth_user_id is not null))
);

-- Identidad seudonimizada (SRS §11): user_hash = HMAC(clave, bot_id:user_id). chat_id se conserva
-- porque es imprescindible para entregar respuestas en el chat privado; acceso restringido.
create table public.telegram_identities (
  id            uuid primary key default gen_random_uuid(),
  principal_id  uuid not null unique references public.principals (id),
  bot_id        bigint not null,
  user_hash     text not null check (user_hash ~ '^[0-9a-f]{64}$'),
  chat_id       bigint not null,
  authorized    boolean not null default false,
  authorized_at timestamptz,
  authorized_by uuid references auth.users (id),
  revoked_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (bot_id, user_hash),
  constraint telegram_identities_authorization
    check (not authorized or (authorized_at is not null and revoked_at is null))
);

create trigger telegram_identities_touch before update on public.telegram_identities
  for each row execute function public.set_updated_at();

-- Contexto conversacional de corta duración (aclaraciones, SRS-F16). Retención 24 h (SRS-N05).
create table public.chat_contexts (
  id                   uuid primary key default gen_random_uuid(),
  telegram_identity_id uuid not null references public.telegram_identities (id) on delete cascade,
  context_token        text not null unique,
  kind                 text not null check (kind in ('clarification', 'pagination', 'comparison')),
  payload              jsonb not null default '{}',
  expires_at           timestamptz not null default now() + interval '24 hours',
  created_at           timestamptz not null default now(),
  constraint chat_contexts_payload_size check (pg_column_size(payload) <= 4096)
);

create index chat_contexts_expiry_idx on public.chat_contexts (expires_at);

-- Cola durable (SRS §3.2, §7.3). Entrega al menos una vez; la clave idempotente garantiza el
-- efecto lógico único. Al vivir en la misma base, encolar en la transacción que publica los
-- datos equivale al outbox transaccional exigido por SRS §7.3.
create table public.jobs (
  id                 uuid primary key default gen_random_uuid(),
  kind               text not null check (kind ~ '^[a-z_]+\.[a-z_.]+$'),
  processing_version text not null default '1',
  idempotency_key    text not null,
  state              text not null default 'pending'
    check (state in ('pending', 'leased', 'retry_wait', 'succeeded', 'dead_letter', 'cancelled')),
  priority           smallint not null default 100,
  payload            jsonb not null default '{}',
  attempts           integer not null default 0 check (attempts >= 0),
  max_attempts       integer not null default 5 check (max_attempts >= 1),
  run_at             timestamptz not null default now(),
  lease_owner        text,
  lease_until        timestamptz,
  last_error_code    text,
  last_error         text,
  source_id          uuid references public.sources (id),
  run_id             uuid references public.ingestion_runs (id),
  trace_id           uuid not null default gen_random_uuid(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  finished_at        timestamptz,
  unique (kind, processing_version, idempotency_key),
  constraint jobs_lease_consistency
    check ((state = 'leased') = (lease_owner is not null and lease_until is not null)),
  constraint jobs_finished check ((state in ('succeeded', 'dead_letter', 'cancelled')) = (finished_at is not null)),
  constraint jobs_payload_size check (pg_column_size(payload) <= 8192)
);

create index jobs_ready_idx on public.jobs (priority, run_at) where state in ('pending', 'retry_wait');
create index jobs_lease_idx on public.jobs (lease_until) where state = 'leased';
create index jobs_dead_letter_idx on public.jobs (updated_at) where state = 'dead_letter';
create trigger jobs_touch before update on public.jobs
  for each row execute function public.set_updated_at();

-- Cola de revisión (PRD-12, SRS-F24). dedupe_key evita abrir el mismo caso dos veces mientras
-- siga abierto.
create table public.review_cases (
  id             uuid primary key default gen_random_uuid(),
  case_type      text not null
    check (case_type in ('identity', 'schema', 'quality', 'conflict', 'coverage', 'enrichment', 'other')),
  state          text not null default 'open' check (state in ('open', 'in_review', 'resolved', 'dismissed')),
  dedupe_key     text not null,
  subject_type   text,
  subject_id     uuid,
  summary        text not null,
  candidates     jsonb not null default '[]',
  evidence_ids   uuid[] not null default '{}',
  related_run_id uuid references public.ingestion_runs (id),
  opened_at      timestamptz not null default now(),
  opened_by      uuid references auth.users (id),
  resolution_id  uuid,
  updated_at     timestamptz not null default now(),
  row_version    integer not null default 1,
  constraint review_cases_candidates_size check (pg_column_size(candidates) <= 16384)
);

create unique index review_cases_open_dedupe_idx
  on public.review_cases (dedupe_key) where state in ('open', 'in_review');
create index review_cases_state_idx on public.review_cases (state, opened_at);
create trigger review_cases_touch before update on public.review_cases
  for each row execute function public.touch_versioned_row();

-- SRS-F25: decisión con actor, instante, motivo, evidencia, valor anterior y nuevo. Revertir es
-- una decisión nueva que apunta a la anterior.
create table public.review_resolutions (
  id                    uuid primary key default gen_random_uuid(),
  case_id               uuid not null references public.review_cases (id),
  decision              text not null
    check (decision in ('accept_candidate', 'reject_candidates', 'keep_conflict', 'manual_value', 'dismiss', 'revert')),
  reason                text not null check (length(btrim(reason)) >= 5),
  actor_id              uuid not null references auth.users (id),
  before_value          jsonb,
  after_value           jsonb,
  evidence_ids          uuid[] not null default '{}',
  reverts_resolution_id uuid references public.review_resolutions (id),
  trace_id              uuid not null default gen_random_uuid(),
  decided_at            timestamptz not null default now(),
  constraint review_resolutions_revert check ((decision = 'revert') = (reverts_resolution_id is not null))
);

create index review_resolutions_case_idx on public.review_resolutions (case_id);
create trigger review_resolutions_append_only before update or delete on public.review_resolutions
  for each row execute function public.forbid_mutation();

alter table public.review_cases
  add constraint review_cases_resolution_fk
  foreign key (resolution_id) references public.review_resolutions (id);

-- Auditoría administrativa append-only (retención 365 días, SRS-N05).
create table public.audit_log (
  id          bigint generated always as identity primary key,
  actor_id    uuid references auth.users (id),
  actor_label text,
  action      text not null,
  target_type text not null,
  target_id   text,
  before_value jsonb,
  after_value  jsonb,
  reason      text,
  trace_id    uuid,
  occurred_at timestamptz not null default now()
);

create index audit_log_target_idx on public.audit_log (target_type, target_id);
create index audit_log_time_idx on public.audit_log (occurred_at);
create trigger audit_log_append_only before update or delete on public.audit_log
  for each row execute function public.forbid_mutation();

-- Trazabilidad de consultas (SRS §6.6). question_text se conserva hasta expires_at (30 días
-- por defecto, SRS-N05) y luego se purga; la traza sin texto sigue sirviendo para métricas.
create table public.query_runs (
  id              uuid primary key default gen_random_uuid(),
  principal_id    uuid not null references public.principals (id),
  channel         text not null check (channel in ('telegram', 'api', 'evaluation')),
  question_text   text,
  intent          text check (intent in ('structured', 'document', 'hybrid', 'comparison', 'agenda', 'unsupported')),
  status          text not null default 'queued'
    check (status in ('queued', 'running', 'answered', 'needs_clarification', 'insufficient_evidence',
                      'conflicting_evidence', 'failed')),
  context_token   text,
  model_version   text,
  prompt_version  text,
  corpus_revision text,
  latency_ms      integer check (latency_ms >= 0),
  idempotency_key text,
  request_hash    text,
  trace_id        uuid not null default gen_random_uuid(),
  created_at      timestamptz not null default now(),
  finished_at     timestamptz,
  expires_at      timestamptz not null default now() + interval '30 days',
  unique (principal_id, idempotency_key),
  constraint query_runs_question_length check (char_length(question_text) <= 4096)
);

create index query_runs_principal_idx on public.query_runs (principal_id, created_at desc);
create index query_runs_expiry_idx on public.query_runs (expires_at);

create table public.query_evidence (
  query_id    uuid not null references public.query_runs (id) on delete cascade,
  evidence_id uuid not null references public.evidence (id),
  rank        integer not null check (rank >= 1),
  claim_ids   text[] not null default '{}',
  primary key (query_id, evidence_id)
);

create index query_evidence_evidence_idx on public.query_evidence (evidence_id);

create table public.answer_records (
  id              uuid primary key default gen_random_uuid(),
  query_id        uuid not null unique references public.query_runs (id) on delete cascade,
  answer_text     text,
  support_status  text not null check (support_status in ('supported', 'partially_supported', 'abstained')),
  warnings        text[] not null default '{}',
  corpus_revision text,
  created_at      timestamptz not null default now(),
  expires_at      timestamptz not null default now() + interval '30 days'
);

create index answer_records_expiry_idx on public.answer_records (expires_at);

-- Deduplicación por (bot_id, update_id) (SRS-F22, T-21).
create table public.telegram_updates (
  bot_id               bigint not null,
  update_id            bigint not null,
  received_at          timestamptz not null default now(),
  update_kind          text not null check (update_kind in ('message', 'callback_query', 'other')),
  telegram_identity_id uuid references public.telegram_identities (id),
  status               text not null
    check (status in ('accepted', 'rejected_unauthorized', 'rejected_unsupported', 'rejected_rate_limited')),
  query_run_id         uuid references public.query_runs (id) on delete set null,
  job_id               uuid references public.jobs (id),
  primary key (bot_id, update_id)
);

create index telegram_updates_received_idx on public.telegram_updates (received_at);

-- Bandeja de salida con segmentos ordenados (SRS §11). Un error de envío reintenta la entrega
-- del segmento, nunca la consulta (SRS-F23). 'uncertain' registra timeouts ambiguos.
create table public.delivery_attempts (
  id                   uuid primary key default gen_random_uuid(),
  answer_id            uuid not null references public.answer_records (id) on delete cascade,
  telegram_identity_id uuid not null references public.telegram_identities (id),
  sequence             integer not null check (sequence >= 1),
  body                 text not null,
  state                text not null default 'pending' check (state in ('pending', 'sent', 'failed', 'uncertain')),
  attempts             integer not null default 0 check (attempts >= 0),
  provider_message_id  bigint,
  error_code           text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  sent_at              timestamptz,
  unique (answer_id, sequence),
  constraint delivery_attempts_sent check ((state = 'sent') = (sent_at is not null))
);

create index delivery_attempts_pending_idx on public.delivery_attempts (created_at) where state in ('pending', 'uncertain');
create trigger delivery_attempts_touch before update on public.delivery_attempts
  for each row execute function public.set_updated_at();

-- Presupuestos (SRS-N17, PRD §13). provider null = presupuesto global.
create table public.budgets (
  id           uuid primary key default gen_random_uuid(),
  name         text not null unique,
  provider     text,
  period       text not null check (period in ('daily', 'monthly')),
  limit_amount numeric(14, 4) not null check (limit_amount >= 0),
  currency     text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  alert_ratio  numeric(4, 3) not null default 0.8 check (alert_ratio > 0 and alert_ratio <= 1),
  hard_stop    boolean not null default true,
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  row_version  integer not null default 1
);

create trigger budgets_touch before update on public.budgets
  for each row execute function public.touch_versioned_row();

-- Libro de consumo con reserva previa (T-29). settled_cost se concilia con el costo real.
create table public.usage_ledger (
  id             uuid primary key default gen_random_uuid(),
  provider       text not null,
  operation      text not null,
  units          numeric(18, 4) not null default 0 check (units >= 0),
  estimated_cost numeric(14, 6) not null check (estimated_cost >= 0),
  settled_cost   numeric(14, 6) check (settled_cost >= 0),
  state          text not null default 'reserved' check (state in ('reserved', 'settled', 'released')),
  job_id         uuid references public.jobs (id),
  query_id       uuid references public.query_runs (id) on delete set null,
  source_id      uuid references public.sources (id),
  reserved_by    uuid references auth.users (id),
  reserved_at    timestamptz not null default now(),
  settled_at     timestamptz,
  constraint usage_ledger_settled check ((state = 'settled') = (settled_cost is not null and settled_at is not null))
);

create index usage_ledger_window_idx on public.usage_ledger (reserved_at, provider) where state <> 'released';
