-- Actores, proyectos canónicos, identificadores por corporación, sesiones, agenda y eventos.
-- Requisitos: SRS-F07, F08, F10, F11, §6.3. Pruebas: T-05, T-06, T-07, T-10, T-11.
-- Toda relación factual apunta a la observación que la sustenta (observation_id).

create table public.persons (
  id              uuid primary key default gen_random_uuid(),
  canonical_name  text not null,
  normalized_name text not null,
  merged_into_id  uuid references public.persons (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  row_version     integer not null default 1,
  constraint persons_not_self_merged check (merged_into_id <> id)
);
comment on column public.persons.normalized_name is
  'Nombre sin tildes, en minúsculas y con espacios normalizados. No es único: homónimos (SRS-F08).';

create index persons_normalized_name_idx on public.persons (normalized_name);
create trigger persons_touch before update on public.persons
  for each row execute function public.touch_versioned_row();

create table public.person_identifiers (
  id             uuid primary key default gen_random_uuid(),
  person_id      uuid not null references public.persons (id),
  source_id      uuid not null references public.sources (id),
  external_id    text not null,
  observation_id uuid references public.observations (id),
  created_at     timestamptz not null default now(),
  unique (source_id, external_id)
);

create index person_identifiers_person_idx on public.person_identifiers (person_id);

create table public.parties (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  normalized_name text not null,
  aliases         text[] not null default '{}',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index parties_normalized_name_idx on public.parties (normalized_name);
create trigger parties_touch before update on public.parties
  for each row execute function public.set_updated_at();

create table public.organizations (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  normalized_name   text not null,
  organization_type text not null default 'otra'
    check (organization_type in ('gobierno', 'entidad_publica', 'partido', 'bancada', 'otra')),
  created_at        timestamptz not null default now()
);

create index organizations_normalized_name_idx on public.organizations (normalized_name);

create table public.person_terms (
  id             uuid primary key default gen_random_uuid(),
  person_id      uuid not null references public.persons (id),
  corporation_id uuid not null references public.corporations (id),
  period_id      uuid references public.legislative_periods (id),
  position       text not null check (position in ('senador', 'representante', 'otro')),
  constituency   text,
  valid_from     date,
  valid_to       date,
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint person_terms_range check (valid_to >= valid_from)
);

create index person_terms_person_idx on public.person_terms (person_id);

-- Un cambio de partido no modifica retrospectivamente la afiliación histórica (PRD §11).
create table public.party_memberships (
  id             uuid primary key default gen_random_uuid(),
  person_id      uuid not null references public.persons (id),
  party_id       uuid not null references public.parties (id),
  valid_from     date,
  valid_to       date,
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint party_memberships_range check (valid_to >= valid_from)
);

create index party_memberships_person_idx on public.party_memberships (person_id, valid_from);

create table public.commissions (
  id              uuid primary key default gen_random_uuid(),
  corporation_id  uuid not null references public.corporations (id),
  code            text not null,
  name            text not null,
  commission_type text not null default 'otra'
    check (commission_type in ('constitucional_permanente', 'legal', 'especial', 'accidental', 'otra')),
  created_at      timestamptz not null default now(),
  unique (corporation_id, code)
);

create table public.commission_memberships (
  id             uuid primary key default gen_random_uuid(),
  commission_id  uuid not null references public.commissions (id),
  person_id      uuid not null references public.persons (id),
  role           text not null default 'miembro'
    check (role in ('miembro', 'presidente', 'vicepresidente', 'secretario', 'otro')),
  valid_from     date,
  valid_to       date,
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint commission_memberships_range check (valid_to >= valid_from)
);

create index commission_memberships_person_idx on public.commission_memberships (person_id);
create index commission_memberships_commission_idx on public.commission_memberships (commission_id);

-- Proyecto canónico: identidad interna independiente del título y de los números externos.
-- Una fusión correctiva se registra con merged_into_id; nunca se borran expedientes.
create table public.projects (
  id                    uuid primary key default gen_random_uuid(),
  initiative_type       text not null
    check (initiative_type in ('proyecto_ley', 'proyecto_acto_legislativo')),
  canonical_title       text,
  origin_corporation_id uuid references public.corporations (id),
  merged_into_id        uuid references public.projects (id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  row_version           integer not null default 1,
  constraint projects_not_self_merged check (merged_into_id <> id)
);

create trigger projects_touch before update on public.projects
  for each row execute function public.touch_versioned_row();

-- SRS-F07: el número aislado no es clave. Clave contextual: corporación, tipo, ámbito de
-- numeración, año de radicación y número normalizado (sin ceros a la izquierda).
create table public.project_identifiers (
  id              uuid primary key default gen_random_uuid(),
  project_id      uuid not null references public.projects (id),
  corporation_id  uuid not null references public.corporations (id),
  initiative_type text not null
    check (initiative_type in ('proyecto_ley', 'proyecto_acto_legislativo')),
  number          text not null check (number ~ '^(0|[1-9][0-9]*)$'),
  number_raw      text not null,
  filing_year     smallint not null check (filing_year between 1991 and 2100),
  numbering_scope text not null default 'corporation_filing_year',
  source_id       uuid references public.sources (id),
  created_at      timestamptz not null default now(),
  unique (corporation_id, initiative_type, numbering_scope, filing_year, number)
);

create index project_identifiers_project_idx on public.project_identifiers (project_id);
create index project_identifiers_number_idx on public.project_identifiers (number, filing_year);

create table public.project_identifier_observations (
  identifier_id  uuid not null references public.project_identifiers (id),
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  primary key (identifier_id, observation_id)
);

create table public.project_participants (
  id              uuid primary key default gen_random_uuid(),
  project_id      uuid not null references public.projects (id),
  person_id       uuid references public.persons (id),
  organization_id uuid references public.organizations (id),
  role            text not null
    check (role in ('autor', 'coautor', 'ponente', 'ponente_coordinador', 'otro')),
  corporation_id  uuid references public.corporations (id),
  stage           text,
  valid_from      date,
  valid_to        date,
  observation_id  uuid not null references public.observations (id),
  created_at      timestamptz not null default now(),
  constraint project_participants_one_actor check (num_nonnulls(person_id, organization_id) = 1),
  constraint project_participants_range check (valid_to >= valid_from)
);

create index project_participants_project_idx on public.project_participants (project_id);
create index project_participants_person_idx on public.project_participants (person_id);

-- Estado observado: se conserva la etiqueta original; la normalizada puede ser 'unknown'.
create table public.project_status_observations (
  id                uuid primary key default gen_random_uuid(),
  project_id        uuid not null references public.projects (id),
  corporation_id    uuid references public.corporations (id),
  stage             text,
  status_raw        text not null,
  status_normalized text not null default 'unknown',
  effective_date    date,
  date_precision    text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  observation_id    uuid not null references public.observations (id),
  created_at        timestamptz not null default now(),
  unique (project_id, observation_id),
  constraint project_status_precision check ((date_precision = 'unknown') = (effective_date is null))
);

create index project_status_observations_project_idx
  on public.project_status_observations (project_id, effective_date desc);

-- Proyección reconstruible del estado actual (SRS-F10): guarda regla, observaciones
-- consideradas y marca de conflicto. No es fuente de verdad.
create table public.project_status_projection (
  project_id              uuid primary key references public.projects (id),
  selected_status_id      uuid references public.project_status_observations (id),
  conflict                boolean not null default false,
  considered_status_ids   uuid[] not null default '{}',
  rule_version            text not null,
  computed_at             timestamptz not null default now()
);

create table public.sessions (
  id             uuid primary key default gen_random_uuid(),
  corporation_id uuid not null references public.corporations (id),
  commission_id  uuid references public.commissions (id),
  session_type   text not null
    check (session_type in ('plenaria', 'comision', 'comisiones_conjuntas', 'otra')),
  source_id      uuid references public.sources (id),
  external_key   text,
  local_date     date,
  date_precision text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  started_at     timestamptz,
  ended_at       timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint sessions_commission check (session_type <> 'comision' or commission_id is not null),
  constraint sessions_precision check ((date_precision = 'unknown') = (local_date is null)),
  constraint sessions_times check (ended_at >= started_at)
);

create unique index sessions_external_key_idx
  on public.sessions (source_id, external_key) where external_key is not null;
create index sessions_date_idx on public.sessions (corporation_id, local_date);
create trigger sessions_touch before update on public.sessions
  for each row execute function public.set_updated_at();

-- Agenda: programación revisable (SRS-F11). La realización no es un estado de agenda:
-- se registra como evento con evidencia propia (event_type 'session_held').
create table public.agenda_items (
  id               uuid primary key default gen_random_uuid(),
  source_record_id uuid not null references public.source_records (id),
  item_key         text not null,
  corporation_id   uuid not null references public.corporations (id),
  commission_id    uuid references public.commissions (id),
  session_id       uuid references public.sessions (id),
  created_at       timestamptz not null default now(),
  unique (source_record_id, item_key)
);

create index agenda_items_session_idx on public.agenda_items (session_id);

create table public.agenda_revisions (
  id             uuid primary key default gen_random_uuid(),
  agenda_item_id uuid not null references public.agenda_items (id),
  revision_no    integer not null check (revision_no >= 1),
  status         text not null check (status in ('scheduled', 'postponed', 'cancelled', 'unknown')),
  local_date     date,
  scheduled_at   timestamptz,
  title          text,
  observation_id uuid not null references public.observations (id),
  observed_at    timestamptz not null,
  created_at     timestamptz not null default now(),
  unique (agenda_item_id, revision_no),
  -- La fecha local debe corresponder al instante en Bogotá (no se admite desalineación).
  constraint agenda_revisions_local_date check (
    scheduled_at is null
    or local_date = (scheduled_at at time zone 'America/Bogota')::date
  )
);

create index agenda_revisions_date_idx on public.agenda_revisions (local_date);
create trigger agenda_revisions_append_only before update or delete on public.agenda_revisions
  for each row execute function public.forbid_mutation();

create table public.agenda_projects (
  agenda_revision_id uuid not null references public.agenda_revisions (id),
  project_id         uuid not null references public.projects (id),
  relation_basis     text not null default 'explicit'
    check (relation_basis in ('explicit', 'resolved_reference', 'manual')),
  primary key (agenda_revision_id, project_id)
);

create index agenda_projects_project_idx on public.agenda_projects (project_id);

-- Última revisión de cada asunto de agenda.
create view public.agenda_current
with (security_invoker = true) as
select distinct on (r.agenda_item_id)
  r.agenda_item_id,
  r.id as agenda_revision_id,
  r.revision_no,
  r.status,
  r.local_date,
  r.scheduled_at,
  r.title,
  i.corporation_id,
  i.commission_id,
  i.session_id,
  r.observation_id,
  r.observed_at
from public.agenda_revisions r
join public.agenda_items i on i.id = r.agenda_item_id
order by r.agenda_item_id, r.revision_no desc;

-- Eventos fechados con evidencia. occurred_at es opcional: no se inventa la hora.
create table public.events (
  id             uuid primary key default gen_random_uuid(),
  event_key      text not null unique,
  event_type     text not null,
  occurred_on    date,
  occurred_at    timestamptz,
  date_precision text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  status         text not null default 'confirmed' check (status in ('confirmed', 'reported', 'retracted')),
  corporation_id uuid references public.corporations (id),
  commission_id  uuid references public.commissions (id),
  summary        text,
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  constraint events_precision check ((date_precision = 'unknown') = (occurred_on is null)),
  constraint events_instant_has_date check (occurred_at is null or occurred_on is not null)
);

create index events_date_idx on public.events (occurred_on);

create table public.event_projects (
  event_id      uuid not null references public.events (id),
  project_id    uuid not null references public.projects (id),
  relation_type text not null default 'subject',
  primary key (event_id, project_id, relation_type)
);

create index event_projects_project_idx on public.event_projects (project_id);

create table public.event_persons (
  event_id      uuid not null references public.events (id),
  person_id     uuid not null references public.persons (id),
  relation_type text not null,
  primary key (event_id, person_id, relation_type)
);

create index event_persons_person_idx on public.event_persons (person_id);

create table public.event_sessions (
  event_id   uuid not null references public.events (id),
  session_id uuid not null references public.sessions (id),
  primary key (event_id, session_id)
);

create index event_sessions_session_idx on public.event_sessions (session_id);
