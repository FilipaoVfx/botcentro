-- Control de acceso (SRS §4, SRS-N01, N02; PRD-13). Pruebas: T-24.
--
-- InsForge otorga por defecto SELECT/INSERT/UPDATE/DELETE sobre tablas de public a anon y
-- authenticated. Aquí se revoca todo y se concede lo mínimo por rol de aplicación:
--   * anon: nada. No hay superficie pública en el MVP.
--   * authenticated: solo lo que su rol en app_roles permita, filtrado además por RLS.
--   * Tablas append-only y de operación: escritura exclusivamente vía RPC (SECURITY DEFINER).
-- La API key administrativa (project_admin, BYPASSRLS) queda para migraciones y soporte.

-- ---------------------------------------------------------------------------------------------
-- 1. RLS y revocación general
-- ---------------------------------------------------------------------------------------------
do $$
declare
  v_tables text[] := array[
    'app_roles',
    'corporations', 'legislative_periods', 'sources', 'source_policies', 'coverage_scopes',
    'ingestion_runs', 'source_records', 'source_snapshots', 'source_checks', 'observations',
    'evidence', 'observation_evidence',
    'persons', 'person_identifiers', 'parties', 'organizations', 'person_terms',
    'party_memberships', 'commissions', 'commission_memberships', 'projects',
    'project_identifiers', 'project_identifier_observations', 'project_participants',
    'project_status_observations', 'project_status_projection', 'sessions', 'agenda_items',
    'agenda_revisions', 'agenda_projects', 'events', 'event_projects', 'event_persons',
    'event_sessions',
    'votings', 'voting_projects', 'vote_observations', 'current_votes', 'voting_totals',
    'attendance_observations', 'interventions',
    'blobs', 'documents', 'document_revisions', 'document_origins', 'gacetas',
    'document_segments', 'project_document_links', 'project_versions', 'extraction_runs',
    'document_pages', 'chunks', 'chunk_project_links', 'chunk_embeddings',
    'document_comparisons', 'event_documents',
    'principals', 'telegram_identities', 'chat_contexts', 'jobs', 'review_cases',
    'review_resolutions', 'audit_log', 'query_runs', 'query_evidence', 'answer_records',
    'telegram_updates', 'delivery_attempts', 'budgets', 'usage_ledger'
  ];
  t text;
begin
  foreach t in array v_tables loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
  end loop;
end
$$;

revoke all on public.coverage_status, public.agenda_current from anon, authenticated;
grant select on public.coverage_status, public.agenda_current to authenticated;

revoke all on all sequences in schema public from anon, authenticated;

-- ---------------------------------------------------------------------------------------------
-- 2. Permisos y políticas por rol de aplicación
-- ---------------------------------------------------------------------------------------------

-- Principales asociados al usuario autenticado (acceso a su propio historial vía API).
create or replace function public.current_principal_ids() returns setof uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select p.id from public.principals p
   where p.auth_user_id = (select auth.uid()) and p.disabled_at is null
$$;

do $$
declare
  v_all_staff constant text[] := array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'];
  v_ingest    constant text[] := array['ingest_service'];
  r record;
begin
  for r in
    select * from (values
      -- Lectura de datos legislativos, procedencia y documentos: todo el personal y servicios.
      ('corporations', 'select', v_all_staff), ('legislative_periods', 'select', v_all_staff),
      ('sources', 'select', v_all_staff), ('source_policies', 'select', v_all_staff),
      ('coverage_scopes', 'select', v_all_staff), ('ingestion_runs', 'select', v_all_staff),
      ('source_records', 'select', v_all_staff), ('source_snapshots', 'select', v_all_staff),
      ('source_checks', 'select', v_all_staff), ('observations', 'select', v_all_staff),
      ('evidence', 'select', v_all_staff), ('observation_evidence', 'select', v_all_staff),
      ('persons', 'select', v_all_staff), ('person_identifiers', 'select', v_all_staff),
      ('parties', 'select', v_all_staff), ('organizations', 'select', v_all_staff),
      ('person_terms', 'select', v_all_staff), ('party_memberships', 'select', v_all_staff),
      ('commissions', 'select', v_all_staff), ('commission_memberships', 'select', v_all_staff),
      ('projects', 'select', v_all_staff), ('project_identifiers', 'select', v_all_staff),
      ('project_identifier_observations', 'select', v_all_staff),
      ('project_participants', 'select', v_all_staff),
      ('project_status_observations', 'select', v_all_staff),
      ('project_status_projection', 'select', v_all_staff), ('sessions', 'select', v_all_staff),
      ('agenda_items', 'select', v_all_staff), ('agenda_revisions', 'select', v_all_staff),
      ('agenda_projects', 'select', v_all_staff), ('events', 'select', v_all_staff),
      ('event_projects', 'select', v_all_staff), ('event_persons', 'select', v_all_staff),
      ('event_sessions', 'select', v_all_staff), ('event_documents', 'select', v_all_staff),
      ('votings', 'select', v_all_staff), ('voting_projects', 'select', v_all_staff),
      ('vote_observations', 'select', v_all_staff), ('current_votes', 'select', v_all_staff),
      ('voting_totals', 'select', v_all_staff), ('attendance_observations', 'select', v_all_staff),
      ('interventions', 'select', v_all_staff), ('blobs', 'select', v_all_staff),
      ('documents', 'select', v_all_staff), ('document_revisions', 'select', v_all_staff),
      ('document_origins', 'select', v_all_staff), ('gacetas', 'select', v_all_staff),
      ('document_segments', 'select', v_all_staff), ('project_document_links', 'select', v_all_staff),
      ('project_versions', 'select', v_all_staff), ('extraction_runs', 'select', v_all_staff),
      ('document_pages', 'select', v_all_staff), ('chunks', 'select', v_all_staff),
      ('chunk_project_links', 'select', v_all_staff), ('chunk_embeddings', 'select', v_all_staff),
      ('document_comparisons', 'select', v_all_staff),

      -- Carga normalizada directa del servicio de ingesta (sin DELETE en ninguna tabla).
      ('coverage_scopes', 'update', v_ingest),
      ('persons', 'insert', v_ingest), ('persons', 'update', v_ingest),
      ('person_identifiers', 'insert', v_ingest),
      ('parties', 'insert', v_ingest), ('parties', 'update', v_ingest),
      ('organizations', 'insert', v_ingest),
      ('person_terms', 'insert', v_ingest), ('party_memberships', 'insert', v_ingest),
      ('commissions', 'insert', v_ingest), ('commission_memberships', 'insert', v_ingest),
      ('projects', 'insert', v_ingest), ('projects', 'update', v_ingest),
      ('project_identifiers', 'insert', v_ingest),
      ('project_identifier_observations', 'insert', v_ingest),
      ('project_participants', 'insert', v_ingest),
      ('project_status_observations', 'insert', v_ingest),
      ('project_status_projection', 'insert', v_ingest), ('project_status_projection', 'update', v_ingest),
      ('sessions', 'insert', v_ingest), ('sessions', 'update', v_ingest),
      ('agenda_items', 'insert', v_ingest), ('agenda_items', 'update', v_ingest),
      ('agenda_revisions', 'insert', v_ingest), ('agenda_projects', 'insert', v_ingest),
      ('events', 'insert', v_ingest), ('event_projects', 'insert', v_ingest),
      ('event_persons', 'insert', v_ingest), ('event_sessions', 'insert', v_ingest),
      ('event_documents', 'insert', v_ingest),
      ('votings', 'insert', v_ingest), ('voting_projects', 'insert', v_ingest),
      ('vote_observations', 'insert', v_ingest),
      ('current_votes', 'insert', v_ingest), ('current_votes', 'update', v_ingest),
      ('voting_totals', 'insert', v_ingest), ('attendance_observations', 'insert', v_ingest),
      ('interventions', 'insert', v_ingest),
      ('blobs', 'insert', v_ingest), ('blobs', 'update', v_ingest),
      ('documents', 'insert', v_ingest), ('documents', 'update', v_ingest),
      ('document_revisions', 'insert', v_ingest), ('document_origins', 'insert', v_ingest),
      ('gacetas', 'insert', v_ingest), ('document_segments', 'insert', v_ingest),
      ('project_document_links', 'insert', v_ingest), ('project_versions', 'insert', v_ingest),
      ('extraction_runs', 'insert', v_ingest), ('extraction_runs', 'update', v_ingest),
      ('document_pages', 'insert', v_ingest),
      ('chunks', 'insert', v_ingest), ('chunk_project_links', 'insert', v_ingest),
      ('chunk_embeddings', 'insert', v_ingest), ('chunk_embeddings', 'update', v_ingest),

      -- Comparaciones (servicio de consulta).
      ('document_comparisons', 'insert', array['query_service']),
      ('document_comparisons', 'update', array['query_service']),

      -- Operación.
      ('jobs', 'select', v_all_staff),
      ('review_cases', 'select', array['ingest_service', 'reviewer', 'operator', 'admin']),
      ('review_resolutions', 'select', array['reviewer', 'operator', 'admin']),
      ('audit_log', 'select', array['admin']),
      ('principals', 'select', array['query_service', 'operator', 'admin']),
      ('telegram_identities', 'select', array['query_service', 'admin']),
      ('chat_contexts', 'select', array['query_service']),
      ('chat_contexts', 'insert', array['query_service']),
      ('chat_contexts', 'delete', array['query_service']),
      ('query_evidence', 'select', array['query_service', 'admin']),
      ('query_evidence', 'insert', array['query_service']),
      ('query_runs', 'insert', array['query_service']),
      ('query_runs', 'update', array['query_service']),
      ('answer_records', 'insert', array['query_service']),
      ('answer_records', 'update', array['query_service']),
      ('telegram_updates', 'select', array['query_service', 'operator', 'admin']),
      ('delivery_attempts', 'select', array['query_service', 'operator', 'admin']),
      ('delivery_attempts', 'insert', array['query_service']),
      ('delivery_attempts', 'update', array['query_service']),
      ('budgets', 'select', v_all_staff),
      ('budgets', 'insert', array['admin']), ('budgets', 'update', array['admin']),
      ('usage_ledger', 'select', array['ingest_service', 'query_service', 'operator', 'admin'])
    ) as v(tbl, cmd, roles)
  loop
    execute format('grant %s on public.%I to authenticated', r.cmd, r.tbl);
    if r.cmd = 'select' then
      execute format(
        'create policy %I on public.%I for select to authenticated using ((select public.has_app_role(%L::text[])))',
        'select_by_role', r.tbl, r.roles);
    elsif r.cmd = 'insert' then
      execute format(
        'create policy %I on public.%I for insert to authenticated with check ((select public.has_app_role(%L::text[])))',
        'insert_by_role', r.tbl, r.roles);
    elsif r.cmd = 'update' then
      execute format(
        'create policy %I on public.%I for update to authenticated using ((select public.has_app_role(%L::text[]))) with check ((select public.has_app_role(%L::text[])))',
        'update_by_role', r.tbl, r.roles, r.roles);
    elsif r.cmd = 'delete' then
      execute format(
        'create policy %I on public.%I for delete to authenticated using ((select public.has_app_role(%L::text[])))',
        'delete_by_role', r.tbl, r.roles);
    end if;
  end loop;
end
$$;

-- Roles: cada usuario ve los suyos; el administrador ve todos. Escritura solo vía RPC.
grant select on public.app_roles to authenticated;
create policy select_own_or_admin on public.app_roles for select to authenticated
  using (user_id = (select auth.uid()) or (select public.has_app_role(array['admin'])));

-- Historial de consultas: el servicio de consulta y el administrador ven todo; un usuario de
-- la API solo sus propias consultas y respuestas (T-24).
grant select on public.query_runs, public.answer_records to authenticated;
create policy select_service_or_owner on public.query_runs for select to authenticated
  using (
    (select public.has_app_role(array['query_service', 'admin']))
    or principal_id in (select public.current_principal_ids())
  );
create policy select_service_or_owner on public.answer_records for select to authenticated
  using (
    (select public.has_app_role(array['query_service', 'admin']))
    or query_id in (
      select q.id from public.query_runs q
       where q.principal_id in (select public.current_principal_ids())
    )
  );

-- Campos protegidos: el servicio de consulta no reasigna una consulta a otro principal ni
-- cambia su canal o su texto (solo estado, intención, métricas y versiones).
revoke update on public.query_runs from authenticated;
grant update (intent, status, context_token, model_version, prompt_version, corpus_revision,
              latency_ms, finished_at) on public.query_runs to authenticated;

revoke update on public.answer_records from authenticated;
grant update (answer_text, support_status, warnings) on public.answer_records to authenticated;

revoke update on public.delivery_attempts from authenticated;
grant update (state, attempts, provider_message_id, error_code, sent_at) on public.delivery_attempts
  to authenticated;

-- ---------------------------------------------------------------------------------------------
-- 3. Funciones: sin EXECUTE para PUBLIC/anon; authenticated solo en RPC y helpers de políticas
-- ---------------------------------------------------------------------------------------------
do $$
declare
  f regprocedure;
begin
  for f in
    select p.oid::regprocedure
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proowner = (select oid from pg_roles where rolname = current_user)
       and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
  loop
    execute format('revoke execute on function %s from public, anon, authenticated', f);
  end loop;
end
$$;

grant execute on function
  public.has_app_role(text[]),
  public.is_staff_or_service(),
  public.current_principal_ids(),
  public.jobs_enqueue(text, text, jsonb, text, timestamptz, integer, integer, uuid, uuid),
  public.jobs_claim(text, text[], integer, integer),
  public.jobs_heartbeat(uuid, text, integer),
  public.jobs_complete(uuid, text),
  public.jobs_fail(uuid, text, text, text, timestamptz),
  public.jobs_release_expired(),
  public.budget_reserve(text, text, numeric, numeric, uuid, uuid, uuid),
  public.budget_settle(uuid, numeric, numeric),
  public.budget_release(uuid),
  public.ingest_start_run(uuid, text, text, uuid, jsonb),
  public.ingest_register_fetch(uuid, text, text, text, text, text, text, integer, text, bigint, text,
                               text, text, text, text, timestamptz),
  public.ingest_register_check(uuid, text, text, integer, text, text),
  public.ingest_upsert_observation(uuid, text, text, text, jsonb, text, uuid, text, date, text,
                                   timestamptz, date, text, text, text),
  public.ingest_commit_cursor(uuid, jsonb),
  public.ingest_add_counters(uuid, integer, integer, integer, integer),
  public.ingest_finish_run(uuid, text, text),
  public.ingest_suspend_source(uuid, text, text),
  public.review_open_case(text, text, text, text, uuid, jsonb, uuid[], uuid),
  public.telegram_accept_update(bigint, bigint, text, text, text, text, integer),
  public.admin_set_source_state(uuid, text, text),
  public.admin_add_source_policy(uuid, text, text, text, text, text, text, text, text, text, text),
  public.admin_retry_job(uuid, text),
  public.admin_authorize_telegram(bigint, text, bigint, text, text),
  public.admin_revoke_telegram(uuid, text),
  public.admin_grant_role(uuid, text, text),
  public.admin_revoke_role(uuid, text, text),
  public.review_resolve(uuid, text, text, jsonb, jsonb, uuid[]),
  public.review_revert(uuid, text),
  public.maintenance_purge_expired(),
  public.search_chunks_lexical(text, uuid, integer),
  public.search_chunks_vector(vector, text, text, uuid, integer)
to authenticated;
