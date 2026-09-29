-- Investigaciones y grandes casos: núcleo de datos (investigaciones §7, §8; DEC-19).
-- Todo es aditivo (MIG-01): se extienden `sources` y los roles, y se crean tablas nuevas. Las
-- tablas tienen RLS y sin privilegios directos: el acceso es por RPC con verificación de rol.
-- Lo publicado se lee siempre desde revisiones o afirmaciones en estado `published` (ARC-04).

-- ---------------------------------------------------------------------------------------------
-- Roles editoriales (ADM-01)
-- ---------------------------------------------------------------------------------------------
alter table public.app_roles drop constraint if exists app_roles_role_check;
alter table public.app_roles add constraint app_roles_role_check
  check (role in ('ingest_service', 'query_service', 'reviewer', 'operator', 'admin', 'editor', 'auditor'));
alter table public.pending_role_grants drop constraint if exists pending_role_grants_role_check;
alter table public.pending_role_grants add constraint pending_role_grants_role_check
  check (role in ('ingest_service', 'query_service', 'reviewer', 'operator', 'admin', 'editor', 'auditor'));

-- ---------------------------------------------------------------------------------------------
-- Banderas y configuración (§18)
-- ---------------------------------------------------------------------------------------------
create table public.feature_flags (
  key         text primary key check (key ~ '^[A-Z][A-Z0-9_]+$'),
  enabled     boolean not null default false,
  description text not null,
  updated_at  timestamptz not null default now(),
  updated_by  uuid references auth.users (id)
);

insert into public.feature_flags (key, enabled, description) values
  ('FEATURE_CASES', false, 'Grandes casos, investigaciones y entidades en Telegram. Desactivada hasta cumplir aceptación P0'),
  ('FEATURE_SUBSCRIPTIONS', false, 'Seguimientos y digest. Desactivada hasta validar outbox, consentimiento y correcciones'),
  ('ENABLE_CPNU_AUTOMATION', false, 'Adaptador automático de CPNU: automatización no certificada'),
  ('ENABLE_COMMERCIAL_PROVIDERS', false, 'Proveedores comerciales de antecedentes: presupuesto 0 (COS-01)');

create table public.app_settings (
  key         text primary key,
  value       jsonb not null,
  description text not null,
  updated_at  timestamptz not null default now()
);

insert into public.app_settings (key, value, description) values
  ('environment', '"production"', 'production exige revisor distinto del autor para atribuciones sensibles (EVI-10)'),
  ('paid_monthly_budget', '0', 'Presupuesto para proveedores de pago (COS-01)'),
  ('digest', '{"timezone": "America/Bogota", "hour": 18, "quiet_from": 21, "quiet_to": 8, "max_items": 10}',
   'Resumen diario de seguimientos (SUB-01, SUB-06)'),
  ('freshness_days', '{"siri": 45, "secop": 3, "relatoria_pgn": 30, "editorial": 7}',
   'Umbral para afirmar vigencia por tipo de fuente (CAS-06); pasado el umbral: «último estado conocido»'),
  ('pilot_municipalities', '{"codes": ["18001", "76109", "81001"], "status": "propuesta", "criterio": "disponibilidad documental SECOP II + SIRI, 2026-09-29"}',
   'Piloto territorial propuesto; la selección final es editorial (§22)');

-- ---------------------------------------------------------------------------------------------
-- Fuentes: estados independientes, capacidades, modo sombra y huella de esquema (SRC-01..06)
-- ---------------------------------------------------------------------------------------------
alter table public.sources
  add column approval_state text not null default 'candidate'
    check (approval_state in ('candidate', 'pilot', 'approved', 'suspended')),
  add column health_state text not null default 'unknown'
    check (health_state in ('healthy', 'degraded', 'unavailable', 'unknown')),
  add column coverage_state text not null default 'unknown'
    check (coverage_state in ('complete_for_scope', 'partial', 'unknown')),
  add column capabilities jsonb not null default '{}',
  add column shadow_mode boolean not null default true,
  add column institution text,
  add column access_method text,
  add column dataset_updated_at timestamptz;

-- Las fuentes legislativas ya en producción no están en modo sombra ni cambian de comportamiento.
update public.sources set shadow_mode = false, approval_state = 'approved'
 where state = 'active' and code in ('SRC-01', 'SRC-03', 'SRC-06');

create table public.source_schema_versions (
  id               uuid primary key default gen_random_uuid(),
  source_id        uuid not null references public.sources (id),
  fingerprint      text not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  fields           jsonb not null,
  mapping_version  text not null,
  result           text not null check (result in ('ok', 'degraded', 'incompatible')),
  missing_critical text[] not null default '{}',
  missing_optional text[] not null default '{}',
  validated_at     timestamptz not null default now(),
  unique (source_id, fingerprint, mapping_version)
);

-- ---------------------------------------------------------------------------------------------
-- Territorios y actores (DAT-01..05)
-- ---------------------------------------------------------------------------------------------
create table public.territories (
  id              uuid primary key default gen_random_uuid(),
  code            text not null unique check (code ~ '^[0-9]{2}([0-9]{3})?$'),
  level           text not null check (level in ('departamento', 'municipio')),
  parent_id       uuid references public.territories (id),
  name            text not null,
  normalized_name text not null,
  kind            text,
  valid_from      date,
  valid_to        date,
  observation_id  uuid references public.observations (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index territories_name_idx on public.territories (normalized_name);
create index territories_parent_idx on public.territories (parent_id);

create table public.actors (
  id              uuid primary key default gen_random_uuid(),
  actor_type      text not null
    check (actor_type in ('persona', 'entidad_publica', 'partido', 'organizacion_privada', 'otra')),
  display_name    text not null,
  normalized_name text not null,
  person_id       uuid references public.persons (id),
  party_id        uuid references public.parties (id),
  territory_id    uuid references public.territories (id),
  review_state    text not null default 'unreviewed'
    check (review_state in ('unreviewed', 'verified', 'rejected', 'merged')),
  merged_into_id  uuid references public.actors (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint actors_merge check ((review_state = 'merged') = (merged_into_id is not null))
);
create index actors_name_idx on public.actors (normalized_name);
create index actors_territory_idx on public.actors (territory_id);
create index actors_person_idx on public.actors (person_id);
create index actors_party_idx on public.actors (party_id);

-- Identificadores protegidos: nunca se guarda el número completo de una persona (SEG-02). El
-- HMAC (clave fuera de la base) permite reconocer el mismo documento; el enmascarado se muestra.
create table public.actor_identifiers (
  id             uuid primary key default gen_random_uuid(),
  actor_id       uuid not null references public.actors (id),
  issuer         text not null,
  id_type        text not null,
  value_hmac     text check (value_hmac ~ '^[0-9a-f]{64}$'),
  value_public   text,  -- NIT de entidades o personas jurídicas: dato público, sin dígitos perdidos
  value_masked   text not null,
  source_id      uuid references public.sources (id),
  observation_id uuid references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint actor_identifiers_value check (value_hmac is not null or value_public is not null)
);
create unique index actor_identifiers_hmac_uq on public.actor_identifiers (issuer, id_type, value_hmac)
  where value_hmac is not null;
create unique index actor_identifiers_public_uq on public.actor_identifiers (issuer, id_type, value_public)
  where value_public is not null;
create index actor_identifiers_actor_idx on public.actor_identifiers (actor_id);

-- ---------------------------------------------------------------------------------------------
-- Afirmaciones con evidencia y revisión (EVI-01, EVI-07, EVI-10)
-- ---------------------------------------------------------------------------------------------
create table public.claims (
  id                uuid primary key default gen_random_uuid(),
  claim_type        text not null check (claim_type in ('participation', 'position', 'affiliation',
                      'proceeding_status', 'case_link', 'contract_link', 'event', 'other')),
  statement         text not null check (length(statement) between 5 and 2000),
  subject_type      text,
  subject_id        uuid,
  object_type       text,
  object_id         uuid,
  value             jsonb not null default '{}',
  sensitive         boolean not null default true,
  origin            text not null check (origin in ('system_ingest', 'editorial')),
  state             text not null default 'draft'
    check (state in ('draft', 'pending_review', 'published', 'superseded', 'retracted')),
  version           integer not null default 1,
  author_id         uuid references auth.users (id),
  author_label      text,
  reviewer_id       uuid references auth.users (id),
  submitted_at      timestamptz,
  reviewed_at       timestamptz,
  published_at      timestamptz,
  retracted_at      timestamptz,
  retraction_reason text,
  rejection_reason  text,
  superseded_by     uuid references public.claims (id),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint claims_published check (state <> 'published' or (published_at is not null and reviewed_at is not null)),
  constraint claims_retracted check (state <> 'retracted' or retraction_reason is not null)
);
create index claims_state_idx on public.claims (state);
create index claims_subject_idx on public.claims (subject_type, subject_id);
create index claims_object_idx on public.claims (object_type, object_id);

create table public.claim_evidence (
  claim_id    uuid not null references public.claims (id),
  evidence_id uuid not null references public.evidence (id),
  function    text not null default 'supports' check (function in ('supports', 'contradicts')),
  created_at  timestamptz not null default now(),
  primary key (claim_id, evidence_id, function)
);
create index claim_evidence_evidence_idx on public.claim_evidence (evidence_id);

create table public.editorial_reviews (
  id           uuid primary key default gen_random_uuid(),
  object_type  text not null check (object_type in ('claim', 'case_revision')),
  object_id    uuid not null,
  version      integer not null,
  decision     text not null check (decision in ('submitted', 'approved', 'rejected', 'retracted', 'hidden')),
  reason       text,
  actor_id     uuid references auth.users (id),
  actor_label  text,
  evidence_ids uuid[] not null default '{}',
  created_at   timestamptz not null default now()
);
create index editorial_reviews_object_idx on public.editorial_reviews (object_type, object_id);
create trigger editorial_reviews_append_only before update or delete on public.editorial_reviews
  for each row execute function public.forbid_mutation();

-- ---------------------------------------------------------------------------------------------
-- Cargos y afiliaciones temporales (DAT-02, REL-03)
-- ---------------------------------------------------------------------------------------------
create table public.positions (
  id               uuid primary key default gen_random_uuid(),
  person_actor_id  uuid not null references public.actors (id),
  entity_actor_id  uuid references public.actors (id),
  title_original   text not null,
  territory_id     uuid references public.territories (id),
  starts_on        date,
  ends_on          date,
  date_precision   text not null default 'unknown' check (date_precision in ('day', 'month', 'year', 'unknown')),
  claim_id         uuid not null references public.claims (id),
  created_at       timestamptz not null default now(),
  constraint positions_range check (ends_on is null or starts_on is null or ends_on >= starts_on)
);
create index positions_person_idx on public.positions (person_actor_id);
create index positions_entity_idx on public.positions (entity_actor_id);

create table public.affiliations (
  id                    uuid primary key default gen_random_uuid(),
  person_actor_id       uuid not null references public.actors (id),
  organization_actor_id uuid not null references public.actors (id),
  affiliation_type      text not null check (affiliation_type in ('partido', 'laboral', 'otra')),
  starts_on             date,
  ends_on               date,
  date_precision        text not null default 'unknown' check (date_precision in ('day', 'month', 'year', 'unknown')),
  claim_id              uuid not null references public.claims (id),
  created_at            timestamptz not null default now()
);
create index affiliations_person_idx on public.affiliations (person_actor_id);
create index affiliations_org_idx on public.affiliations (organization_actor_id);

-- ---------------------------------------------------------------------------------------------
-- Expedientes, actuaciones y participaciones (CAS-03, CAS-04, REL-01..05)
-- ---------------------------------------------------------------------------------------------
create table public.proceedings (
  id                  uuid primary key default gen_random_uuid(),
  authority           text not null,
  authority_actor_id  uuid references public.actors (id),
  jurisdiction        text not null check (jurisdiction in ('penal', 'disciplinaria', 'fiscal', 'administrativa',
                                                             'constitucional', 'electoral', 'otra')),
  system              text not null,  -- sistema o registro que asigna el identificador (p. ej. SIRI)
  source_id           uuid references public.sources (id),
  source_identifier   text not null,
  radicado_original   text,
  radicado_normalized text,
  official_url        text,
  status_original     text,
  status_normalized   text not null default 'desconocido'
    check (status_normalized in ('indagacion', 'investigacion', 'juicio', 'decision_primera_instancia',
                                 'decision_segunda_instancia', 'sancion_registrada', 'archivado', 'absuelto',
                                 'condenado', 'terminado_otro', 'desconocido')),
  finality            text not null default 'desconocida'
    check (finality in ('en_firme', 'no_en_firme', 'recurrida', 'desconocida')),
  last_verified_at    timestamptz,
  restrictions        text,
  territory_id        uuid references public.territories (id),
  observation_id      uuid references public.observations (id),
  claim_id            uuid references public.claims (id),  -- afirmación que sostiene el estado mostrado
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (system, source_identifier)
);
create index proceedings_territory_idx on public.proceedings (territory_id);
create index proceedings_status_idx on public.proceedings (status_normalized);

create table public.proceeding_events (
  id             uuid primary key default gen_random_uuid(),
  proceeding_id  uuid not null references public.proceedings (id),
  event_type     text not null,
  description    text not null,
  occurred_on    date,
  date_precision text not null default 'unknown' check (date_precision in ('day', 'month', 'year', 'unknown')),
  published_on   date,
  observation_id uuid references public.observations (id),
  claim_id       uuid not null references public.claims (id),
  created_at     timestamptz not null default now()
);
create index proceeding_events_proceeding_idx on public.proceeding_events (proceeding_id, occurred_on);

create table public.participations (
  id              uuid primary key default gen_random_uuid(),
  actor_id        uuid not null references public.actors (id),
  proceeding_id   uuid not null references public.proceedings (id),
  role_original   text not null,
  role_normalized text not null
    check (role_normalized in ('investigado', 'imputado', 'acusado', 'condenado', 'absuelto', 'sancionado',
                               'denunciante', 'testigo', 'victima', 'entidad_afectada', 'contratante',
                               'contratista', 'mencionado', 'otro')),
  role_dictionary text not null default 'roles-v1',
  starts_on       date,
  ends_on         date,
  claim_id        uuid not null references public.claims (id),
  created_at      timestamptz not null default now()
);
create index participations_actor_idx on public.participations (actor_id, role_normalized);
create index participations_proceeding_idx on public.participations (proceeding_id);

-- ---------------------------------------------------------------------------------------------
-- Contratación (DAT-06, §11.1)
-- ---------------------------------------------------------------------------------------------
create table public.contracts (
  id                  uuid primary key default gen_random_uuid(),
  source_id           uuid not null references public.sources (id),
  native_id           text not null,
  entity_actor_id     uuid references public.actors (id),
  contractor_actor_id uuid references public.actors (id),
  territory_id        uuid references public.territories (id),
  currency            text not null default 'COP' check (currency ~ '^[A-Z]{3}$'),
  object              text,
  official_url        text,
  current_version_id  uuid,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (source_id, native_id)
);
create index contracts_entity_idx on public.contracts (entity_actor_id);
create index contracts_contractor_idx on public.contracts (contractor_actor_id);
create index contracts_territory_idx on public.contracts (territory_id);

create table public.contract_versions (
  id              uuid primary key default gen_random_uuid(),
  contract_id     uuid not null references public.contracts (id),
  content_hash    text not null check (content_hash ~ '^[0-9a-f]{64}$'),
  status_original text,
  signed_on       date,
  starts_on       date,
  ends_on         date,
  value_initial   numeric(20, 2),
  value_additions numeric(20, 2),
  value_paid      numeric(20, 2),
  value_current   numeric(20, 2),
  modality        text,
  contract_type   text,
  observation_id  uuid not null references public.observations (id),
  created_at      timestamptz not null default now(),
  unique (contract_id, content_hash)
);
create index contract_versions_contract_idx on public.contract_versions (contract_id, created_at desc);
create trigger contract_versions_append_only before update or delete on public.contract_versions
  for each row execute function public.forbid_mutation();
alter table public.contracts add constraint contracts_current_version_fk
  foreign key (current_version_id) references public.contract_versions (id);

-- Índice documental oficial (Relatoría PGN): se referencia por URL, sin descargar el original.
create table public.official_documents (
  id             uuid primary key default gen_random_uuid(),
  source_id      uuid not null references public.sources (id),
  native_id      text not null,
  doc_type       text,
  number         text,
  dependency     text,
  topic          text,
  subtopic       text,
  url            text,
  doc_date       date,
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  unique (source_id, native_id)
);
create index official_documents_date_idx on public.official_documents (doc_date desc);

-- ---------------------------------------------------------------------------------------------
-- Grandes casos (CAS-01..08) y revisiones editoriales
-- ---------------------------------------------------------------------------------------------
create table public.cases (
  id                    uuid primary key default gen_random_uuid(),
  slug                  text not null unique check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  published_revision_id uuid,
  latest_revision_no    integer not null default 0,
  created_by            uuid references auth.users (id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table public.case_revisions (
  id                uuid primary key default gen_random_uuid(),
  case_id           uuid not null references public.cases (id),
  revision_no       integer not null check (revision_no >= 1),
  title             text not null check (length(title) between 5 and 200),
  summary           text not null check (length(summary) between 20 and 4000),
  aliases           text[] not null default '{}',
  scope             text not null,
  topics            text[] not null default '{}',
  territory_ids     uuid[] not null default '{}',
  featured          boolean not null default false,
  featured_reason   text,
  coverage_note     text not null,
  evidence_ids      uuid[] not null default '{}',
  state             text not null default 'draft'
    check (state in ('draft', 'pending_review', 'published', 'superseded', 'retracted')),
  author_id         uuid references auth.users (id),
  reviewer_id       uuid references auth.users (id),
  submitted_at      timestamptz,
  reviewed_at       timestamptz,
  published_at      timestamptz,
  retracted_at      timestamptz,
  retraction_reason text,
  rejection_reason  text,
  created_at        timestamptz not null default now(),
  unique (case_id, revision_no),
  constraint case_revisions_featured check (not featured or featured_reason is not null),
  constraint case_revisions_retracted check (state <> 'retracted' or retraction_reason is not null)
);
create index case_revisions_state_idx on public.case_revisions (state);
alter table public.cases add constraint cases_published_revision_fk
  foreign key (published_revision_id) references public.case_revisions (id);

-- Una revisión publicada es inmutable salvo su transición a superseded/retracted (EVI-07).
create or replace function public.guard_published_revision() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if old.state in ('published', 'superseded', 'retracted') then
    if new.state not in ('superseded', 'retracted') or new.title is distinct from old.title
       or new.summary is distinct from old.summary or new.scope is distinct from old.scope
       or new.evidence_ids is distinct from old.evidence_ids or new.coverage_note is distinct from old.coverage_note then
      raise exception using errcode = 'BC002', message = 'una revisión publicada es inmutable';
    end if;
  end if;
  return new;
end
$$;
create trigger case_revisions_guard before update on public.case_revisions
  for each row execute function public.guard_published_revision();

create table public.case_proceedings (
  case_id       uuid not null references public.cases (id),
  proceeding_id uuid not null references public.proceedings (id),
  justification text not null,
  claim_id      uuid not null references public.claims (id),
  created_at    timestamptz not null default now(),
  primary key (case_id, proceeding_id)
);
create index case_proceedings_proceeding_idx on public.case_proceedings (proceeding_id);

create table public.case_contracts (
  case_id     uuid not null references public.cases (id),
  contract_id uuid not null references public.contracts (id),
  reason      text not null,
  claim_id    uuid not null references public.claims (id),
  created_at  timestamptz not null default now(),
  primary key (case_id, contract_id)
);

create table public.identity_candidates (
  id                uuid primary key default gen_random_uuid(),
  actor_a_id        uuid not null references public.actors (id),
  actor_b_id        uuid not null references public.actors (id),
  signals           jsonb not null default '{}',
  conflicts         jsonb not null default '{}',
  state             text not null default 'open' check (state in ('open', 'merged', 'separated', 'dismissed')),
  resolved_by       uuid references auth.users (id),
  resolution_reason text,
  created_at        timestamptz not null default now(),
  resolved_at       timestamptz,
  unique (actor_a_id, actor_b_id)
);

-- ---------------------------------------------------------------------------------------------
-- Seguimientos, outbox y entregas (SUB-01..07)
-- ---------------------------------------------------------------------------------------------
create table public.subscriptions (
  id                   uuid primary key default gen_random_uuid(),
  telegram_identity_id uuid not null references public.telegram_identities (id),
  object_type          text not null check (object_type in ('case', 'proceeding', 'contract', 'actor')),
  object_id            uuid not null,
  channel              text not null default 'telegram' check (channel in ('telegram')),
  frequency            text not null default 'daily_digest' check (frequency in ('daily_digest')),
  consent_at           timestamptz not null default now(),
  consent_text_version text not null,
  active               boolean not null default true,
  cancelled_at         timestamptz,
  last_digest_at       timestamptz,
  created_at           timestamptz not null default now(),
  unique (telegram_identity_id, object_type, object_id, channel)
);
create index subscriptions_object_idx on public.subscriptions (object_type, object_id) where active;

create table public.outbox_events (
  id           uuid primary key default gen_random_uuid(),
  event_type   text not null check (event_type in ('case_published', 'case_retracted', 'claim_published',
                                                    'claim_retracted', 'correction')),
  object_type  text not null,
  object_id    uuid not null,
  case_ids     uuid[] not null default '{}',
  revision_ref text not null,
  summary      text not null,
  dedupe_key   text not null unique,
  created_at   timestamptz not null default now()
);
create index outbox_events_created_idx on public.outbox_events (created_at);
create trigger outbox_events_append_only before update or delete on public.outbox_events
  for each row execute function public.forbid_mutation();

create table public.notification_deliveries (
  id                   uuid primary key default gen_random_uuid(),
  telegram_identity_id uuid not null references public.telegram_identities (id),
  outbox_event_id      uuid not null references public.outbox_events (id),
  subscription_id      uuid not null references public.subscriptions (id),
  state                text not null default 'pending'
    check (state in ('pending', 'sending', 'sent', 'failed', 'suppressed', 'unknown_delivery')),
  attempts             integer not null default 0,
  digest_key           text,
  provider_message_id  bigint,
  error_code           text,
  suppressed_reason    text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  sent_at              timestamptz,
  unique (telegram_identity_id, outbox_event_id)
);
create index notification_deliveries_pending_idx on public.notification_deliveries (state, created_at)
  where state in ('pending', 'sending', 'unknown_delivery');

create table public.admin_commands (
  id              uuid primary key default gen_random_uuid(),
  actor_id        uuid not null references auth.users (id),
  command_type    text not null,
  idempotency_key text not null,
  payload_hash    text not null,
  state           text not null default 'accepted' check (state in ('accepted', 'succeeded', 'failed')),
  result          jsonb,
  created_at      timestamptz not null default now(),
  unique (actor_id, command_type, idempotency_key)
);

-- ---------------------------------------------------------------------------------------------
-- Acceso: RLS en todo y sin privilegios directos (las RPC SECURITY DEFINER verifican el rol)
-- ---------------------------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['feature_flags', 'app_settings', 'source_schema_versions', 'territories', 'actors',
    'actor_identifiers', 'claims', 'claim_evidence', 'editorial_reviews', 'positions', 'affiliations', 'proceedings',
    'proceeding_events', 'participations', 'contracts', 'contract_versions', 'official_documents', 'cases',
    'case_revisions', 'case_proceedings', 'case_contracts', 'identity_candidates', 'subscriptions', 'outbox_events',
    'notification_deliveries', 'admin_commands']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from public, anon, authenticated', t);
  end loop;
end
$$;

create trigger territories_touch before update on public.territories for each row execute function public.set_updated_at();
create trigger actors_touch before update on public.actors for each row execute function public.set_updated_at();
create trigger claims_touch before update on public.claims for each row execute function public.set_updated_at();
create trigger proceedings_touch before update on public.proceedings for each row execute function public.set_updated_at();
create trigger contracts_touch before update on public.contracts for each row execute function public.set_updated_at();
create trigger cases_touch before update on public.cases for each row execute function public.set_updated_at();
create trigger notification_deliveries_touch before update on public.notification_deliveries
  for each row execute function public.set_updated_at();
