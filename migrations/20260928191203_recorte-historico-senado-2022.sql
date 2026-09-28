-- DEC-17: la base supera el cupo gratuito de InsForge (500 MB). Por decisión del responsable se
-- recorta el histórico de SRC-01 (Senado, datos abiertos) anterior al 2022-01-01. Se borran las
-- observaciones fechadas y lo que se derivó de ellas (votaciones, votos, asistencias, sesiones,
-- agenda, evidencia y proyectos que quedan sin ninguna referencia). Se conservan los catálogos sin
-- fecha (senadores, perfiles, comisiones) y cualquier observación que otra tabla aún use.
-- Reversible: la API del Senado conserva el histórico y `botcentro ingest SRC-01` lo recarga.
-- Aprobado por el responsable el 2026-09-28 («apruebo recorte»).

create or replace function public.maintenance_trim_source_before(p_source_code text, p_before date)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_source uuid;
  v_counts jsonb := '{}';
  n        integer;
begin
  select id into v_source from public.sources where code = p_source_code;
  if v_source is null then
    raise exception using errcode = 'P0002', message = format('fuente inexistente: %s', p_source_code);
  end if;

  create temp table trim_obs on commit drop as
    select o.id from public.observations o
     where o.source_id = v_source and o.effective_date < p_before;
  -- Nunca se borra una observación que sostiene datos fuera del alcance del recorte.
  delete from trim_obs t where
       exists (select 1 from public.person_identifiers x where x.observation_id = t.id)
    or exists (select 1 from public.person_terms x where x.observation_id = t.id)
    or exists (select 1 from public.party_memberships x where x.observation_id = t.id)
    or exists (select 1 from public.commission_memberships x where x.observation_id = t.id)
    or exists (select 1 from public.project_participants x where x.observation_id = t.id)
    or exists (select 1 from public.project_status_observations x where x.observation_id = t.id)
    or exists (select 1 from public.events x where x.observation_id = t.id)
    or exists (select 1 from public.interventions x where x.observation_id = t.id)
    or exists (select 1 from public.project_document_links x where x.observation_id = t.id)
    or exists (select 1 from public.project_versions x where x.observation_id = t.id);
  create unique index on trim_obs (id);
  analyze trim_obs;

  create temp table trim_votings on commit drop as
    select v.id from public.votings v where v.observation_id in (select id from trim_obs);
  create temp table trim_sessions on commit drop as
    select s.id from public.sessions s where s.source_id = v_source and s.local_date < p_before;
  create temp table trim_revisions on commit drop as
    select r.id, r.agenda_item_id from public.agenda_revisions r where r.observation_id in (select id from trim_obs);

  alter table public.vote_observations disable trigger vote_observations_append_only;
  alter table public.agenda_revisions disable trigger agenda_revisions_append_only;
  alter table public.observation_evidence disable trigger observation_evidence_append_only;
  alter table public.evidence disable trigger evidence_append_only;

  -- Votaciones
  delete from public.current_votes where voting_id in (select id from trim_votings);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('current_votes', n);
  delete from public.current_votes cv using public.vote_observations vo
   where cv.selected_observation_id = vo.id and vo.observation_id in (select id from trim_obs);
  delete from public.vote_observations
   where voting_id in (select id from trim_votings) or observation_id in (select id from trim_obs);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('vote_observations', n);
  delete from public.voting_totals
   where voting_id in (select id from trim_votings) or observation_id in (select id from trim_obs);
  delete from public.voting_projects where voting_id in (select id from trim_votings);
  delete from public.votings where id in (select id from trim_votings);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('votings', n);

  -- Asistencia y agenda
  delete from public.attendance_observations
   where observation_id in (select id from trim_obs) or session_id in (select id from trim_sessions);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('attendance', n);
  delete from public.agenda_projects where agenda_revision_id in (select id from trim_revisions);
  delete from public.agenda_revisions where id in (select id from trim_revisions);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('agenda_revisions', n);
  delete from public.agenda_items ai
   where ai.id in (select agenda_item_id from trim_revisions)
     and not exists (select 1 from public.agenda_revisions r where r.agenda_item_id = ai.id);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('agenda_items', n);

  -- Sesiones que ya nada referencia
  delete from public.sessions s
   where s.id in (select id from trim_sessions)
     and not exists (select 1 from public.agenda_items x where x.session_id = s.id)
     and not exists (select 1 from public.event_sessions x where x.session_id = s.id)
     and not exists (select 1 from public.votings x where x.session_id = s.id)
     and not exists (select 1 from public.attendance_observations x where x.session_id = s.id)
     and not exists (select 1 from public.interventions x where x.session_id = s.id);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('sessions', n);

  -- Identificadores y proyectos que solo existían por esas observaciones
  delete from public.project_identifier_observations where observation_id in (select id from trim_obs);
  create temp table trim_projects on commit drop as
    select distinct pi.project_id as id from public.project_identifiers pi
     where pi.source_id = v_source
       and not exists (select 1 from public.project_identifier_observations x where x.identifier_id = pi.id);
  delete from public.project_identifiers pi
   where pi.source_id = v_source
     and not exists (select 1 from public.project_identifier_observations x where x.identifier_id = pi.id);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('project_identifiers', n);
  delete from public.projects p
   where p.id in (select id from trim_projects)
     and not exists (select 1 from public.project_identifiers x where x.project_id = p.id)
     and not exists (select 1 from public.project_participants x where x.project_id = p.id)
     and not exists (select 1 from public.project_status_observations x where x.project_id = p.id)
     and not exists (select 1 from public.project_status_projection x where x.project_id = p.id)
     and not exists (select 1 from public.agenda_projects x where x.project_id = p.id)
     and not exists (select 1 from public.event_projects x where x.project_id = p.id)
     and not exists (select 1 from public.voting_projects x where x.project_id = p.id)
     and not exists (select 1 from public.project_document_links x where x.project_id = p.id)
     and not exists (select 1 from public.project_versions x where x.project_id = p.id)
     and not exists (select 1 from public.chunk_project_links x where x.project_id = p.id)
     and not exists (select 1 from public.projects x where x.merged_into_id = p.id);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('projects', n);

  -- Evidencia y observaciones
  create temp table trim_evidence on commit drop as
    select distinct oe.evidence_id as id from public.observation_evidence oe
     where oe.observation_id in (select id from trim_obs);
  delete from public.observation_evidence where observation_id in (select id from trim_obs);
  delete from public.evidence e
   where e.id in (select id from trim_evidence)
     and not exists (select 1 from public.observation_evidence x where x.evidence_id = e.id)
     and not exists (select 1 from public.query_evidence x where x.evidence_id = e.id);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('evidence', n);
  delete from public.observations where id in (select id from trim_obs);
  get diagnostics n = row_count; v_counts := v_counts || jsonb_build_object('observations', n);

  alter table public.vote_observations enable trigger vote_observations_append_only;
  alter table public.agenda_revisions enable trigger agenda_revisions_append_only;
  alter table public.observation_evidence enable trigger observation_evidence_append_only;
  alter table public.evidence enable trigger evidence_append_only;

  update public.coverage_scopes set from_date = greatest(from_date, p_before)
   where source_id = v_source and from_date < p_before;

  insert into public.audit_log (actor_label, action, target_type, target_id, before_value, after_value, reason)
  values ('migración por encargo del responsable', 'source.trim_history', 'source', v_source::text,
          jsonb_build_object('before', p_before), v_counts,
          format('DEC-17: recorte de %s anterior a %s para volver al cupo gratuito de InsForge', p_source_code, p_before));
  return v_counts;
end
$$;

revoke all on function public.maintenance_trim_source_before(text, date) from public, anon, authenticated;

-- Solo actúa donde existe la fuente (en una base nueva, p. ej. la de pruebas, no hace nada).
select public.maintenance_trim_source_before('SRC-01', date '2022-01-01')
 where exists (select 1 from public.sources where code = 'SRC-01');
