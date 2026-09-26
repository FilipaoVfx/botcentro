-- Votaciones, votos nominales, totales agregados, asistencia e intervenciones.
-- Requisitos: SRS-F09, §6.4. Pruebas: T-08, T-09, T-17.
--
-- Reglas (SRS-F09, §6.4):
--   * Resultados agregados y nominales se guardan por separado.
--   * Una fila ausente no se convierte en 'absent'; solo la fuente puede decir 'absent'.
--   * No se completan filas nominales a partir de un total.

create table public.votings (
  id               uuid primary key default gen_random_uuid(),
  voting_key       text not null unique,
  session_id       uuid references public.sessions (id),
  source_record_id uuid references public.source_records (id),
  external_id      text,
  subject_type     text not null default 'unknown'
    check (subject_type in ('project', 'article', 'proposition', 'report', 'other', 'unknown')),
  subject_text     text,
  voting_method    text not null default 'unknown'
    check (voting_method in ('nominal', 'ordinary', 'secret', 'unknown')),
  voted_at         timestamptz,
  local_date       date,
  date_precision   text not null default 'unknown'
    check (date_precision in ('day', 'month', 'year', 'unknown')),
  observation_id   uuid not null references public.observations (id),
  created_at       timestamptz not null default now(),
  constraint votings_precision check ((date_precision = 'unknown') = (local_date is null)),
  constraint votings_local_date check (
    voted_at is null or local_date = (voted_at at time zone 'America/Bogota')::date
  )
);

create index votings_session_idx on public.votings (session_id);
create index votings_date_idx on public.votings (local_date);

create table public.voting_projects (
  voting_id  uuid not null references public.votings (id),
  project_id uuid not null references public.projects (id),
  primary key (voting_id, project_id)
);

create index voting_projects_project_idx on public.voting_projects (project_id);

-- Historia de votos nominales: las correcciones observadas son filas nuevas.
create table public.vote_observations (
  id              uuid primary key default gen_random_uuid(),
  voting_id       uuid not null references public.votings (id),
  person_id       uuid not null references public.persons (id),
  vote_raw        text not null,
  vote_normalized text not null
    check (vote_normalized in ('yes', 'no', 'abstain', 'impeded', 'absent', 'not_recorded', 'other')),
  observation_id  uuid not null references public.observations (id),
  observed_at     timestamptz not null,
  created_at      timestamptz not null default now(),
  unique (voting_id, person_id, observation_id)
);

create index vote_observations_person_idx on public.vote_observations (person_id);
create trigger vote_observations_append_only before update or delete on public.vote_observations
  for each row execute function public.forbid_mutation();

-- Proyección por par votación/persona. Las consultas exactas excluyen o señalan conflictos.
create table public.current_votes (
  voting_id               uuid not null references public.votings (id),
  person_id               uuid not null references public.persons (id),
  selected_observation_id uuid not null references public.vote_observations (id),
  conflict                boolean not null default false,
  rule_version            text not null,
  computed_at             timestamptz not null default now(),
  primary key (voting_id, person_id)
);

create index current_votes_person_idx on public.current_votes (person_id);

-- Totales reportados por la fuente; separados de cualquier suma nominal.
create table public.voting_totals (
  id             uuid primary key default gen_random_uuid(),
  voting_id      uuid not null references public.votings (id),
  category       text not null
    check (category in ('yes', 'no', 'abstain', 'impeded', 'absent', 'present', 'other')),
  total          integer not null check (total >= 0),
  observation_id uuid not null references public.observations (id),
  created_at     timestamptz not null default now(),
  unique (voting_id, category, observation_id)
);

create index voting_totals_voting_idx on public.voting_totals (voting_id);

-- La asistencia no se infiere de una votación (SRS §6.4).
create table public.attendance_observations (
  id                uuid primary key default gen_random_uuid(),
  session_id        uuid not null references public.sessions (id),
  person_id         uuid not null references public.persons (id),
  status_raw        text not null,
  status_normalized text not null
    check (status_normalized in ('present', 'absent', 'excused', 'other')),
  observation_id    uuid not null references public.observations (id),
  created_at        timestamptz not null default now(),
  unique (session_id, person_id, observation_id)
);

create index attendance_observations_person_idx on public.attendance_observations (person_id);

-- text_revision_id apunta a la revisión documental con el texto; FK en la migración documental.
create table public.interventions (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid not null references public.sessions (id),
  person_id        uuid references public.persons (id),
  text_revision_id uuid,
  completeness     text not null default 'unknown' check (completeness in ('complete', 'partial', 'unknown')),
  start_time       timestamptz,
  end_time         timestamptz,
  observation_id   uuid not null references public.observations (id),
  created_at       timestamptz not null default now(),
  constraint interventions_times check (end_time >= start_time)
);

create index interventions_session_idx on public.interventions (session_id);
create index interventions_person_idx on public.interventions (person_id);
