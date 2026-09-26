-- Índices en columnas de claves foráneas sin índice (advisor de InsForge: missing-fk-index).
-- Aceleran uniones de procedencia (p. ej. hechos derivados de una observación al aplicar o
-- revertir una corrección, SRS-F25) y evitan barridos completos al validar referencias.
-- Lista explícita generada desde el catálogo para que la migración sea revisable.

create index agenda_items_commission_id_fk_idx on public.agenda_items (commission_id);
create index agenda_items_corporation_id_fk_idx on public.agenda_items (corporation_id);
create index agenda_revisions_observation_id_fk_idx on public.agenda_revisions (observation_id);
create index app_roles_granted_by_fk_idx on public.app_roles (granted_by);
create index attendance_observations_observation_id_fk_idx on public.attendance_observations (observation_id);
create index audit_log_actor_id_fk_idx on public.audit_log (actor_id);
create index chat_contexts_telegram_identity_id_fk_idx on public.chat_contexts (telegram_identity_id);
create index commission_memberships_observation_id_fk_idx on public.commission_memberships (observation_id);
create index coverage_scopes_corporation_id_fk_idx on public.coverage_scopes (corporation_id);
create index current_votes_selected_observation_id_fk_idx on public.current_votes (selected_observation_id);
create index delivery_attempts_telegram_identity_id_fk_idx on public.delivery_attempts (telegram_identity_id);
create index document_comparisons_right_version_id_fk_idx on public.document_comparisons (right_version_id);
create index document_origins_snapshot_id_fk_idx on public.document_origins (snapshot_id);
create index document_revisions_blob_hash_fk_idx on public.document_revisions (blob_hash);
create index document_revisions_supersedes_id_fk_idx on public.document_revisions (supersedes_id);
create index documents_publisher_id_fk_idx on public.documents (publisher_id);
create index events_commission_id_fk_idx on public.events (commission_id);
create index events_corporation_id_fk_idx on public.events (corporation_id);
create index events_observation_id_fk_idx on public.events (observation_id);
create index evidence_chunk_id_fk_idx on public.evidence (chunk_id);
create index evidence_extraction_id_fk_idx on public.evidence (extraction_id);
create index ingestion_runs_coverage_scope_id_fk_idx on public.ingestion_runs (coverage_scope_id);
create index ingestion_runs_triggered_by_fk_idx on public.ingestion_runs (triggered_by);
create index interventions_observation_id_fk_idx on public.interventions (observation_id);
create index interventions_text_revision_id_fk_idx on public.interventions (text_revision_id);
create index jobs_run_id_fk_idx on public.jobs (run_id);
create index jobs_source_id_fk_idx on public.jobs (source_id);
create index party_memberships_observation_id_fk_idx on public.party_memberships (observation_id);
create index party_memberships_party_id_fk_idx on public.party_memberships (party_id);
create index person_identifiers_observation_id_fk_idx on public.person_identifiers (observation_id);
create index person_terms_corporation_id_fk_idx on public.person_terms (corporation_id);
create index person_terms_observation_id_fk_idx on public.person_terms (observation_id);
create index person_terms_period_id_fk_idx on public.person_terms (period_id);
create index persons_merged_into_id_fk_idx on public.persons (merged_into_id);
create index project_document_links_observation_id_fk_idx on public.project_document_links (observation_id);
create index project_document_links_segment_id_revision_id_fk_idx on public.project_document_links (segment_id, revision_id);
create index project_identifier_observations_observation_id_fk_idx on public.project_identifier_observations (observation_id);
create index project_identifiers_source_id_fk_idx on public.project_identifiers (source_id);
create index project_participants_corporation_id_fk_idx on public.project_participants (corporation_id);
create index project_participants_observation_id_fk_idx on public.project_participants (observation_id);
create index project_participants_organization_id_fk_idx on public.project_participants (organization_id);
create index project_status_observations_corporation_id_fk_idx on public.project_status_observations (corporation_id);
create index project_status_observations_observation_id_fk_idx on public.project_status_observations (observation_id);
create index project_status_projection_selected_status_id_fk_idx on public.project_status_projection (selected_status_id);
create index project_versions_corporation_id_fk_idx on public.project_versions (corporation_id);
create index project_versions_observation_id_fk_idx on public.project_versions (observation_id);
create index project_versions_revision_id_fk_idx on public.project_versions (revision_id);
create index project_versions_segment_id_revision_id_fk_idx on public.project_versions (segment_id, revision_id);
create index projects_merged_into_id_fk_idx on public.projects (merged_into_id);
create index projects_origin_corporation_id_fk_idx on public.projects (origin_corporation_id);
create index review_cases_opened_by_fk_idx on public.review_cases (opened_by);
create index review_cases_related_run_id_fk_idx on public.review_cases (related_run_id);
create index review_cases_resolution_id_fk_idx on public.review_cases (resolution_id);
create index review_resolutions_actor_id_fk_idx on public.review_resolutions (actor_id);
create index review_resolutions_reverts_resolution_id_fk_idx on public.review_resolutions (reverts_resolution_id);
create index sessions_commission_id_fk_idx on public.sessions (commission_id);
create index source_checks_run_id_fk_idx on public.source_checks (run_id);
create index source_checks_snapshot_id_fk_idx on public.source_checks (snapshot_id);
create index sources_policy_id_id_fk_idx on public.sources (policy_id, id);
create index telegram_identities_authorized_by_fk_idx on public.telegram_identities (authorized_by);
create index telegram_updates_job_id_fk_idx on public.telegram_updates (job_id);
create index telegram_updates_query_run_id_fk_idx on public.telegram_updates (query_run_id);
create index telegram_updates_telegram_identity_id_fk_idx on public.telegram_updates (telegram_identity_id);
create index usage_ledger_job_id_fk_idx on public.usage_ledger (job_id);
create index usage_ledger_query_id_fk_idx on public.usage_ledger (query_id);
create index usage_ledger_reserved_by_fk_idx on public.usage_ledger (reserved_by);
create index usage_ledger_source_id_fk_idx on public.usage_ledger (source_id);
create index vote_observations_observation_id_fk_idx on public.vote_observations (observation_id);
create index voting_totals_observation_id_fk_idx on public.voting_totals (observation_id);
create index votings_observation_id_fk_idx on public.votings (observation_id);
create index votings_source_record_id_fk_idx on public.votings (source_record_id);

-- is_staff_or_service solo delega en has_app_role: no necesita privilegios del propietario.
create or replace function public.is_staff_or_service() returns boolean
language sql
stable
security invoker
set search_path = pg_catalog, public, pg_temp
as $$
  select public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
$$;
