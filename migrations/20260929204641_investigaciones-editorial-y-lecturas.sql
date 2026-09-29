-- Investigaciones: flujo editorial, banderas y lecturas públicas (EVI-01/07/08/10, DB-03, ARC-04, CAS-05/06).
-- Reglas de dominio en la base (no solo en la interfaz):
--   * Nada se publica sin evidencia (T-30).
--   * En producción, una atribución sensible la aprueba alguien distinto del autor (T-31).
--   * Una revisión obsoleta no sobrescribe (T-32; código BC409).
--   * Publicar crea el evento de outbox en la misma transacción (SUB-03), con clave de deduplicación.
--   * Las lecturas públicas solo ven lo publicado (T-47).

create or replace function public.app_setting(p_key text) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$ select value from public.app_settings where key = p_key $$;

create or replace function public.feature_enabled(p_key text) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$ select coalesce((select enabled from public.feature_flags where key = p_key), false) $$;

create or replace function public.bot_feature_flags() returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'ingest_service', 'admin', 'operator', 'editor', 'reviewer']);
  return (select coalesce(jsonb_object_agg(key, enabled), '{}') from public.feature_flags);
end
$$;

create or replace function public.admin_set_feature_flag(p_key text, p_enabled boolean, p_reason text) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['admin']);
  v_old boolean;
begin
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo obligatorio';
  end if;
  select enabled into v_old from public.feature_flags where key = p_key for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'bandera inexistente';
  end if;
  update public.feature_flags set enabled = p_enabled, updated_at = now(), updated_by = v_uid where key = p_key;
  perform public.write_audit(v_uid, 'feature_flag.set', 'feature_flag', p_key, jsonb_build_object('enabled', v_old),
                             jsonb_build_object('enabled', p_enabled), p_reason);
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Afirmaciones
-- ---------------------------------------------------------------------------------------------

create or replace function public.editor_create_claim(
  p_claim_type text, p_statement text, p_subject_type text, p_subject_id uuid, p_object_type text,
  p_object_id uuid, p_value jsonb, p_sensitive boolean, p_evidence_ids uuid[]
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['editor', 'admin']);
  v_id  uuid;
begin
  if exists (select 1 from unnest(coalesce(p_evidence_ids, '{}')) e(id)
              where not exists (select 1 from public.evidence x where x.id = e.id)) then
    raise exception using errcode = 'P0002', message = 'evidencia inexistente';
  end if;
  insert into public.claims (claim_type, statement, subject_type, subject_id, object_type, object_id, value, sensitive,
                             origin, author_id)
  values (p_claim_type, p_statement, p_subject_type, p_subject_id, p_object_type, p_object_id,
          coalesce(p_value, '{}'), coalesce(p_sensitive, true), 'editorial', v_uid)
  returning id into v_id;
  insert into public.claim_evidence (claim_id, evidence_id)
  select v_id, e.id from unnest(coalesce(p_evidence_ids, '{}')) e(id) on conflict do nothing;
  return v_id;
end
$$;

create or replace function public.editor_submit_claim(p_claim_id uuid, p_expected_version integer) returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['editor', 'admin']);
  v_c   public.claims%rowtype;
begin
  select * into v_c from public.claims where id = p_claim_id for update;
  if not found then raise exception using errcode = 'P0002', message = 'afirmación inexistente'; end if;
  if v_c.version <> p_expected_version then
    raise exception using errcode = 'BC409', message = 'la afirmación cambió: recarga antes de enviarla';
  end if;
  if v_c.state <> 'draft' then
    raise exception using errcode = 'BC409', message = format('solo un borrador se envía a revisión (estado %s)', v_c.state);
  end if;
  if not exists (select 1 from public.claim_evidence where claim_id = p_claim_id and function = 'supports') then
    raise exception using errcode = 'BC422', message = 'una afirmación sin evidencia no puede enviarse a revisión';
  end if;
  update public.claims set state = 'pending_review', version = version + 1, submitted_at = now() where id = p_claim_id;
  insert into public.editorial_reviews (object_type, object_id, version, decision, actor_id)
  values ('claim', p_claim_id, v_c.version + 1, 'submitted', v_uid);
  return v_c.version + 1;
end
$$;

-- Casos a los que afecta una afirmación (para el outbox y los seguimientos).
create or replace function public.claim_case_ids(p_claim_id uuid) returns uuid[]
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select coalesce(array_agg(distinct c), '{}') from (
    select cp.case_id as c from public.claims cl join public.case_proceedings cp
        on cp.proceeding_id in (cl.subject_id, cl.object_id) where cl.id = p_claim_id
    union select cp.case_id from public.case_proceedings cp where cp.claim_id = p_claim_id
    union select cc.case_id from public.case_contracts cc where cc.claim_id = p_claim_id
    union select cp.case_id from public.participations pa join public.case_proceedings cp on cp.proceeding_id = pa.proceeding_id
     where pa.claim_id = p_claim_id
    union select cp.case_id from public.proceeding_events ev join public.case_proceedings cp on cp.proceeding_id = ev.proceeding_id
     where ev.claim_id = p_claim_id) x where c is not null
$$;

create or replace function public.reviewer_decide_claim(p_claim_id uuid, p_expected_version integer, p_decision text,
                                                        p_reason text) returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['reviewer', 'admin']);
  v_c   public.claims%rowtype;
begin
  select * into v_c from public.claims where id = p_claim_id for update;
  if not found then raise exception using errcode = 'P0002', message = 'afirmación inexistente'; end if;
  if v_c.version <> p_expected_version then
    raise exception using errcode = 'BC409', message = 'la afirmación cambió desde que la abriste: recarga';
  end if;
  if v_c.state <> 'pending_review' then
    raise exception using errcode = 'BC409', message = format('no está pendiente de revisión (estado %s)', v_c.state);
  end if;
  if p_decision = 'reject' then
    if coalesce(length(btrim(p_reason)), 0) < 5 then
      raise exception using errcode = '22023', message = 'motivo de rechazo obligatorio';
    end if;
    update public.claims set state = 'draft', version = version + 1, rejection_reason = p_reason, reviewed_at = now(),
                             reviewer_id = v_uid where id = p_claim_id;
    insert into public.editorial_reviews (object_type, object_id, version, decision, reason, actor_id)
    values ('claim', p_claim_id, v_c.version + 1, 'rejected', p_reason, v_uid);
    return 'draft';
  elsif p_decision <> 'approve' then
    raise exception using errcode = '22023', message = 'decisión inválida';
  end if;
  if not exists (select 1 from public.claim_evidence where claim_id = p_claim_id and function = 'supports') then
    raise exception using errcode = 'BC422', message = 'no se publica una afirmación sin evidencia';
  end if;
  if v_c.sensitive and public.app_setting('environment') = '"production"'
     and v_c.author_id is not null and v_c.author_id = v_uid then
    raise exception using errcode = '42501',
      message = 'en producción una atribución sensible la aprueba una persona distinta de su autor';
  end if;
  update public.claims set state = 'published', version = version + 1, reviewer_id = v_uid, reviewed_at = now(),
                           published_at = now(), rejection_reason = null where id = p_claim_id;
  insert into public.editorial_reviews (object_type, object_id, version, decision, reason, actor_id, evidence_ids)
  values ('claim', p_claim_id, v_c.version + 1, 'approved', p_reason, v_uid,
          (select array_agg(evidence_id) from public.claim_evidence where claim_id = p_claim_id));
  insert into public.outbox_events (event_type, object_type, object_id, case_ids, revision_ref, summary, dedupe_key)
  values ('claim_published', 'claim', p_claim_id, public.claim_case_ids(p_claim_id), (v_c.version + 1)::text,
          left(v_c.statement, 300), format('claim_published:%s:%s', p_claim_id, v_c.version + 1))
  on conflict (dedupe_key) do nothing;
  return 'published';
end
$$;

-- Corrección: se retira la afirmación, se suprimen entregas pendientes y quien ya recibió la
-- versión errónea recibe una corrección (F-06, T-44, T-45).
create or replace function public.correction_for_object(p_object_type text, p_object_id uuid, p_case_ids uuid[],
                                                        p_summary text, p_ref text) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_event uuid;
begin
  insert into public.outbox_events (event_type, object_type, object_id, case_ids, revision_ref, summary, dedupe_key)
  values ('correction', p_object_type, p_object_id, coalesce(p_case_ids, '{}'), p_ref, p_summary,
          format('correction:%s:%s:%s', p_object_type, p_object_id, p_ref))
  on conflict (dedupe_key) do nothing
  returning id into v_event;
  if v_event is null then
    return null;
  end if;
  update public.notification_deliveries d set state = 'suppressed', suppressed_reason = 'retirado o corregido'
    from public.outbox_events e
   where e.id = d.outbox_event_id and e.object_type = p_object_type and e.object_id = p_object_id
     and e.event_type <> 'correction' and d.state in ('pending', 'sending');
  insert into public.notification_deliveries (telegram_identity_id, outbox_event_id, subscription_id)
  select distinct on (d.telegram_identity_id) d.telegram_identity_id, v_event, d.subscription_id
    from public.notification_deliveries d join public.outbox_events e on e.id = d.outbox_event_id
   where e.object_type = p_object_type and e.object_id = p_object_id and e.event_type <> 'correction'
     and d.state in ('sent', 'unknown_delivery')
  on conflict do nothing;
  return v_event;
end
$$;

create or replace function public.reviewer_retract_claim(p_claim_id uuid, p_reason text) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['reviewer', 'admin']);
  v_c   public.claims%rowtype;
begin
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo público de retirada obligatorio';
  end if;
  select * into v_c from public.claims where id = p_claim_id for update;
  if not found or v_c.state <> 'published' then
    raise exception using errcode = 'BC409', message = 'solo se retira una afirmación publicada';
  end if;
  update public.claims set state = 'retracted', version = version + 1, retracted_at = now(), retraction_reason = p_reason
   where id = p_claim_id;
  insert into public.editorial_reviews (object_type, object_id, version, decision, reason, actor_id)
  values ('claim', p_claim_id, v_c.version + 1, 'retracted', p_reason, v_uid);
  insert into public.outbox_events (event_type, object_type, object_id, case_ids, revision_ref, summary, dedupe_key)
  values ('claim_retracted', 'claim', p_claim_id, public.claim_case_ids(p_claim_id), (v_c.version + 1)::text,
          'Retirada: ' || left(p_reason, 280), format('claim_retracted:%s:%s', p_claim_id, v_c.version + 1))
  on conflict (dedupe_key) do nothing;
  perform public.correction_for_object('claim', p_claim_id, public.claim_case_ids(p_claim_id),
                                       'Corrección: ' || left(p_reason, 280), (v_c.version + 1)::text);
  perform public.write_audit(v_uid, 'claim.retract', 'claim', p_claim_id::text, null, null, p_reason);
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Casos
-- ---------------------------------------------------------------------------------------------

create or replace function public.editor_create_case(
  p_slug text, p_title text, p_summary text, p_scope text, p_coverage_note text, p_topics text[],
  p_aliases text[], p_territory_ids uuid[], p_evidence_ids uuid[], p_featured boolean, p_featured_reason text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid := public.require_app_role(array['editor', 'admin']);
  v_case uuid;
begin
  insert into public.cases (slug, latest_revision_no, created_by) values (p_slug, 1, v_uid) returning id into v_case;
  insert into public.case_revisions (case_id, revision_no, title, summary, aliases, scope, topics, territory_ids,
                                     featured, featured_reason, coverage_note, evidence_ids, author_id)
  values (v_case, 1, p_title, p_summary, coalesce(p_aliases, '{}'), p_scope, coalesce(p_topics, '{}'),
          coalesce(p_territory_ids, '{}'), coalesce(p_featured, false), p_featured_reason, p_coverage_note,
          coalesce(p_evidence_ids, '{}'), v_uid);
  return v_case;
end
$$;

create or replace function public.editor_revise_case(
  p_case_id uuid, p_expected_latest integer, p_title text, p_summary text, p_scope text, p_coverage_note text,
  p_topics text[], p_aliases text[], p_territory_ids uuid[], p_evidence_ids uuid[], p_featured boolean,
  p_featured_reason text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid := public.require_app_role(array['editor', 'admin']);
  v_case public.cases%rowtype;
  v_rev  uuid;
begin
  select * into v_case from public.cases where id = p_case_id for update;
  if not found then raise exception using errcode = 'P0002', message = 'caso inexistente'; end if;
  if v_case.latest_revision_no <> p_expected_latest then
    raise exception using errcode = 'BC409', message = 'el caso tiene una revisión más reciente: recarga';
  end if;
  update public.cases set latest_revision_no = latest_revision_no + 1 where id = p_case_id;
  insert into public.case_revisions (case_id, revision_no, title, summary, aliases, scope, topics, territory_ids,
                                     featured, featured_reason, coverage_note, evidence_ids, author_id)
  values (p_case_id, v_case.latest_revision_no + 1, p_title, p_summary, coalesce(p_aliases, '{}'), p_scope,
          coalesce(p_topics, '{}'), coalesce(p_territory_ids, '{}'), coalesce(p_featured, false), p_featured_reason,
          p_coverage_note, coalesce(p_evidence_ids, '{}'), v_uid)
  returning id into v_rev;
  return v_rev;
end
$$;

create or replace function public.editor_submit_case_revision(p_revision_id uuid) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['editor', 'admin']);
  v_r   public.case_revisions%rowtype;
begin
  select * into v_r from public.case_revisions where id = p_revision_id for update;
  if not found or v_r.state <> 'draft' then
    raise exception using errcode = 'BC409', message = 'solo un borrador se envía a revisión';
  end if;
  if cardinality(v_r.evidence_ids) = 0 then
    raise exception using errcode = 'BC422', message = 'una revisión de caso sin evidencia no puede enviarse';
  end if;
  update public.case_revisions set state = 'pending_review', submitted_at = now() where id = p_revision_id;
  insert into public.editorial_reviews (object_type, object_id, version, decision, actor_id)
  values ('case_revision', p_revision_id, v_r.revision_no, 'submitted', v_uid);
end
$$;

create or replace function public.reviewer_decide_case_revision(p_revision_id uuid, p_expected_latest integer,
                                                                p_decision text, p_reason text) returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid := public.require_app_role(array['reviewer', 'admin']);
  v_r    public.case_revisions%rowtype;
  v_case public.cases%rowtype;
begin
  select * into v_r from public.case_revisions where id = p_revision_id for update;
  if not found then raise exception using errcode = 'P0002', message = 'revisión inexistente'; end if;
  select * into v_case from public.cases where id = v_r.case_id for update;
  if v_case.latest_revision_no <> p_expected_latest or v_r.revision_no <> v_case.latest_revision_no then
    raise exception using errcode = 'BC409', message = 'hay una revisión más reciente del caso: no se sobrescribe';
  end if;
  if v_r.state <> 'pending_review' then
    raise exception using errcode = 'BC409', message = format('la revisión no está pendiente (estado %s)', v_r.state);
  end if;
  if p_decision = 'reject' then
    if coalesce(length(btrim(p_reason)), 0) < 5 then
      raise exception using errcode = '22023', message = 'motivo de rechazo obligatorio';
    end if;
    update public.case_revisions set state = 'draft', rejection_reason = p_reason, reviewer_id = v_uid,
                                     reviewed_at = now() where id = p_revision_id;
    insert into public.editorial_reviews (object_type, object_id, version, decision, reason, actor_id)
    values ('case_revision', p_revision_id, v_r.revision_no, 'rejected', p_reason, v_uid);
    return 'draft';
  elsif p_decision <> 'approve' then
    raise exception using errcode = '22023', message = 'decisión inválida';
  end if;
  if cardinality(v_r.evidence_ids) = 0 or exists (
       select 1 from unnest(v_r.evidence_ids) e(id) where not exists (select 1 from public.evidence x where x.id = e.id)) then
    raise exception using errcode = 'BC422', message = 'no se publica un caso sin evidencia recuperable';
  end if;
  if public.app_setting('environment') = '"production"' and v_r.author_id = v_uid then
    raise exception using errcode = '42501', message = 'en producción el caso lo publica una persona distinta de su autor';
  end if;
  update public.case_revisions set state = 'superseded'
   where case_id = v_r.case_id and state = 'published';
  update public.case_revisions set state = 'published', reviewer_id = v_uid, reviewed_at = now(), published_at = now()
   where id = p_revision_id;
  update public.cases set published_revision_id = p_revision_id where id = v_r.case_id;
  insert into public.editorial_reviews (object_type, object_id, version, decision, reason, actor_id, evidence_ids)
  values ('case_revision', p_revision_id, v_r.revision_no, 'approved', p_reason, v_uid, v_r.evidence_ids);
  insert into public.outbox_events (event_type, object_type, object_id, case_ids, revision_ref, summary, dedupe_key)
  values ('case_published', 'case', v_r.case_id, array[v_r.case_id], v_r.revision_no::text,
          format('%s (revisión %s)', v_r.title, v_r.revision_no), format('case_published:%s', p_revision_id))
  on conflict (dedupe_key) do nothing;
  return 'published';
end
$$;

create or replace function public.reviewer_retract_case(p_case_id uuid, p_reason text) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid  uuid := public.require_app_role(array['reviewer', 'admin']);
  v_case public.cases%rowtype;
begin
  if coalesce(length(btrim(p_reason)), 0) < 5 then
    raise exception using errcode = '22023', message = 'motivo público de retirada obligatorio';
  end if;
  select * into v_case from public.cases where id = p_case_id for update;
  if v_case.published_revision_id is null then
    raise exception using errcode = 'BC409', message = 'el caso no tiene una revisión publicada';
  end if;
  update public.case_revisions set state = 'retracted', retracted_at = now(), retraction_reason = p_reason
   where id = v_case.published_revision_id;
  update public.cases set published_revision_id = null where id = p_case_id;
  insert into public.outbox_events (event_type, object_type, object_id, case_ids, revision_ref, summary, dedupe_key)
  values ('case_retracted', 'case', p_case_id, array[p_case_id], v_case.published_revision_id::text,
          'Retirado: ' || left(p_reason, 280), format('case_retracted:%s', v_case.published_revision_id))
  on conflict (dedupe_key) do nothing;
  perform public.correction_for_object('case', p_case_id, array[p_case_id], 'Corrección: ' || left(p_reason, 280),
                                       v_case.published_revision_id::text);
  perform public.write_audit(v_uid, 'case.retract', 'case', p_case_id::text, null, null, p_reason);
end
$$;

-- Expedientes, actores y relaciones editoriales (la visibilidad depende de su afirmación).
create or replace function public.editor_upsert_proceeding(
  p_system text, p_source_identifier text, p_authority text, p_jurisdiction text, p_radicado text, p_official_url text,
  p_status_original text, p_status_normalized text, p_finality text, p_territory_id uuid, p_claim_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_id uuid;
begin
  perform public.require_app_role(array['editor', 'admin', 'ingest_service']);
  insert into public.proceedings (system, source_identifier, authority, jurisdiction, radicado_original, official_url,
                                  status_original, status_normalized, finality, territory_id, claim_id, last_verified_at)
  values (p_system, p_source_identifier, p_authority, p_jurisdiction, p_radicado, p_official_url, p_status_original,
          coalesce(p_status_normalized, 'desconocido'), coalesce(p_finality, 'desconocida'), p_territory_id, p_claim_id,
          now())
  on conflict (system, source_identifier) do update
     set status_original = excluded.status_original, status_normalized = excluded.status_normalized,
         finality = excluded.finality, claim_id = coalesce(excluded.claim_id, proceedings.claim_id),
         last_verified_at = now(), official_url = coalesce(excluded.official_url, proceedings.official_url)
  returning id into v_id;
  return v_id;
end
$$;

create or replace function public.editor_create_actor(p_actor_type text, p_display_name text, p_territory_id uuid)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_id uuid;
begin
  perform public.require_app_role(array['editor', 'admin']);
  insert into public.actors (actor_type, display_name, normalized_name, territory_id)
  values (p_actor_type, p_display_name, public.fold_text(p_display_name), p_territory_id)
  returning id into v_id;
  return v_id;
end
$$;

create or replace function public.editor_link(p_kind text, p_a uuid, p_b uuid, p_role_original text, p_role_normalized text,
                                              p_text text, p_starts_on date, p_ends_on date, p_claim_id uuid) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_id uuid := gen_random_uuid();
begin
  perform public.require_app_role(array['editor', 'admin']);
  if not exists (select 1 from public.claims where id = p_claim_id) then
    raise exception using errcode = 'P0002', message = 'toda relación se sostiene en una afirmación';
  end if;
  if p_kind = 'case_proceeding' then
    insert into public.case_proceedings (case_id, proceeding_id, justification, claim_id)
    values (p_a, p_b, p_text, p_claim_id) on conflict (case_id, proceeding_id) do update set claim_id = excluded.claim_id;
  elsif p_kind = 'case_contract' then
    insert into public.case_contracts (case_id, contract_id, reason, claim_id)
    values (p_a, p_b, p_text, p_claim_id) on conflict (case_id, contract_id) do update set claim_id = excluded.claim_id;
  elsif p_kind = 'participation' then
    insert into public.participations (id, actor_id, proceeding_id, role_original, role_normalized, starts_on, ends_on, claim_id)
    values (v_id, p_a, p_b, p_role_original, p_role_normalized, p_starts_on, p_ends_on, p_claim_id);
  elsif p_kind = 'position' then
    insert into public.positions (id, person_actor_id, entity_actor_id, title_original, starts_on, ends_on,
                                  date_precision, claim_id)
    values (v_id, p_a, p_b, p_role_original, p_starts_on, p_ends_on, case when p_starts_on is null then 'unknown' else 'day' end,
            p_claim_id);
  elsif p_kind = 'affiliation' then
    insert into public.affiliations (id, person_actor_id, organization_actor_id, affiliation_type, starts_on, ends_on, claim_id)
    values (v_id, p_a, p_b, coalesce(p_role_normalized, 'partido'), p_starts_on, p_ends_on, p_claim_id);
  elsif p_kind = 'event' then
    insert into public.proceeding_events (id, proceeding_id, event_type, description, occurred_on, date_precision, claim_id)
    values (v_id, p_a, coalesce(p_role_normalized, 'actuacion'), p_text, p_starts_on,
            case when p_starts_on is null then 'unknown' else 'day' end, p_claim_id);
  else
    raise exception using errcode = '22023', message = 'tipo de relación desconocido';
  end if;
  return v_id;
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Lecturas públicas (solo lo publicado)
-- ---------------------------------------------------------------------------------------------

-- Estado de un expediente para mostrar: vigente solo si la verificación está dentro del umbral.
create or replace function public.proceeding_freshness(p_system text, p_last_verified timestamptz) returns text
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select case
    when p_last_verified is null then 'por_verificar'
    when p_last_verified >= now() - make_interval(days => coalesce(
           (public.app_setting('freshness_days') ->> lower(case p_system when 'SIRI' then 'siri' else 'editorial' end))::int, 7))
      then 'verificado'
    else 'ultimo_estado_conocido' end
$$;

create or replace function public.claim_is_public(p_claim_id uuid) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$ select exists (select 1 from public.claims where id = p_claim_id and state = 'published') $$;

create or replace function public.evidence_view(p_evidence_id uuid) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select jsonb_build_object(
    'evidence_id', e.id, 'kind', e.kind,
    'url', coalesce((select og.source_url from public.document_origins og where og.revision_id = e.document_revision_id
                      order by og.created_at limit 1),
                    (select sn.final_url from public.source_snapshots sn where sn.id = e.snapshot_id)),
    'record_pointer', e.record_pointer, 'pages', case when e.pdf_page_start is not null
                                                     then jsonb_build_array(e.pdf_page_start, coalesce(e.pdf_page_end, e.pdf_page_start)) end,
    'excerpt', (select left(substr(dp.text, e.char_start - dp.char_start + 1, e.char_end - e.char_start), 400)
                  from public.document_pages dp
                 where dp.extraction_id = e.extraction_id and dp.pdf_page = e.pdf_page_start and e.char_start is not null),
    'captured_at', coalesce((select sn.fetched_at from public.source_snapshots sn where sn.id = e.snapshot_id),
                            (select r.created_at from public.document_revisions r where r.id = e.document_revision_id)))
    from public.evidence e where e.id = p_evidence_id
$$;

create or replace function public.case_branches(p_case_id uuid) returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'proceeding_id', p.id, 'authority', p.authority, 'jurisdiction', p.jurisdiction,
           'radicado', p.radicado_original, 'status_original', p.status_original, 'status', p.status_normalized,
           'finality', p.finality, 'last_verified_at', p.last_verified_at,
           'freshness', public.proceeding_freshness(p.system, p.last_verified_at),
           'justification', cp.justification) order by p.last_verified_at desc nulls last), '[]')
    from public.case_proceedings cp join public.proceedings p on p.id = cp.proceeding_id
   where cp.case_id = p_case_id and public.claim_is_public(cp.claim_id)
     and (p.claim_id is null or public.claim_is_public(p.claim_id))
$$;

create or replace function public.public_cases_list(p_filters jsonb, p_limit integer default 200) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_tokens text[] := (select coalesce(array_agg(t), '{}') from regexp_split_to_table(
                        public.fold_text(p_filters ->> 'query'), '\s+') t where length(t) >= 3);
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    with pub as (
      select c.id, c.slug, r.title, r.summary, r.topics, r.territory_ids, r.featured, r.featured_reason,
             r.published_at, r.reviewed_at, public.case_branches(c.id) as branches,
             (select max(ev.occurred_on) from public.case_proceedings cp
                join public.proceeding_events ev on ev.proceeding_id = cp.proceeding_id
               where cp.case_id = c.id and public.claim_is_public(ev.claim_id) and public.claim_is_public(cp.claim_id))
               as last_activity
        from public.cases c join public.case_revisions r on r.id = c.published_revision_id
    ), filtered as (
      select * from pub p
       where (p_filters ->> 'topic' is null or p_filters ->> 'topic' = any (p.topics))
         and (p_filters ->> 'territory_id' is null or (p_filters ->> 'territory_id')::uuid = any (p.territory_ids))
         and (p_filters ->> 'jurisdiction' is null or exists (
               select 1 from jsonb_array_elements(p.branches) b where b ->> 'jurisdiction' = p_filters ->> 'jurisdiction'))
         and (p_filters ->> 'actor_id' is null or exists (
               select 1 from public.case_proceedings cp join public.participations pa on pa.proceeding_id = cp.proceeding_id
                where cp.case_id = p.id and pa.actor_id = (p_filters ->> 'actor_id')::uuid
                  and public.claim_is_public(pa.claim_id)))
         and (cardinality(v_tokens) = 0 or public.fold_text(concat_ws(' ', p.title, p.summary, array_to_string(p.topics, ' ')))
                                              like all (select '%' || t || '%' from unnest(v_tokens) t))
    )
    select jsonb_build_object(
      'as_of', now(), 'known_total', (select count(*) from filtered),
      'coverage', 'Casos publicados tras revisión editorial en nuestra cobertura; no es un censo de investigaciones del país.',
      'items', coalesce((select jsonb_agg(jsonb_build_object(
          'case_id', f.id, 'slug', f.slug, 'title', f.title, 'summary', left(f.summary, 280), 'featured', f.featured,
          'featured_reason', f.featured_reason, 'published_at', f.published_at, 'last_activity', f.last_activity,
          'branches', f.branches)
          order by case when p_filters ->> 'sort' = 'activity' then f.last_activity end desc nulls last,
                   f.featured desc, f.published_at desc, f.id)
          from (select * from filtered limit least(p_limit, 200)) f), '[]')));
end
$$;

create or replace function public.public_case(p_case_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'case_id', c.id, 'slug', c.slug, 'title', r.title, 'summary', r.summary, 'scope', r.scope, 'aliases', r.aliases,
      'topics', r.topics, 'coverage_note', r.coverage_note, 'featured', r.featured, 'featured_reason', r.featured_reason,
      'revision_no', r.revision_no, 'published_at', r.published_at, 'reviewed_at', r.reviewed_at,
      'branches', public.case_branches(c.id),
      'evidence', (select coalesce(jsonb_agg(public.evidence_view(e)), '[]') from unnest(r.evidence_ids) e),
      'territories', (select coalesce(jsonb_agg(jsonb_build_object('territory_id', t.id, 'name', t.name, 'code', t.code,
                        'department', (select d.name from public.territories d where d.id = t.parent_id))), '[]')
                        from public.territories t where t.id = any (r.territory_ids)),
      'contracts', (select count(*) from public.case_contracts cc where cc.case_id = c.id and public.claim_is_public(cc.claim_id)))
      from public.cases c join public.case_revisions r on r.id = c.published_revision_id
     where c.id = p_case_id);
end
$$;

create or replace function public.public_case_timeline(p_case_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  if not exists (select 1 from public.cases where id = p_case_id and published_revision_id is not null) then
    return null;
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'event_id', ev.id, 'proceeding_id', p.id, 'proceeding', concat_ws(' · ', p.authority, p.radicado_original),
            'event_type', ev.event_type, 'description', ev.description, 'occurred_on', ev.occurred_on,
            'date_precision', ev.date_precision, 'published_on', ev.published_on,
            'evidence', (select coalesce(jsonb_agg(public.evidence_view(ce.evidence_id)), '[]')
                           from public.claim_evidence ce where ce.claim_id = ev.claim_id and ce.function = 'supports'))
            order by ev.occurred_on desc nulls last, p.id, ev.id), '[]')
            from public.case_proceedings cp
            join public.proceedings p on p.id = cp.proceeding_id
            join public.proceeding_events ev on ev.proceeding_id = p.id
           where cp.case_id = p_case_id and public.claim_is_public(cp.claim_id) and public.claim_is_public(ev.claim_id));
end
$$;

create or replace function public.public_case_actors(p_case_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  if not exists (select 1 from public.cases where id = p_case_id and published_revision_id is not null) then
    return null;
  end if;
  return jsonb_build_object(
    'participations', (select coalesce(jsonb_agg(jsonb_build_object(
        'actor_id', a.id, 'name', a.display_name, 'actor_type', a.actor_type, 'role_original', pa.role_original,
        'role', pa.role_normalized, 'proceeding', concat_ws(' · ', p.authority, p.radicado_original),
        'proceeding_id', p.id) order by a.display_name), '[]')
        from public.case_proceedings cp join public.proceedings p on p.id = cp.proceeding_id
        join public.participations pa on pa.proceeding_id = p.id join public.actors a on a.id = pa.actor_id
       where cp.case_id = p_case_id and public.claim_is_public(cp.claim_id) and public.claim_is_public(pa.claim_id)),
    -- Contexto: afiliaciones y cargos de esas personas, marcados como contexto y no como sujeto procesal (REL-03).
    'context', (select coalesce(jsonb_agg(distinct jsonb_build_object(
        'actor_id', a.id, 'name', a.display_name, 'relation', af.affiliation_type, 'organization', o.display_name,
        'organization_id', o.id, 'starts_on', af.starts_on, 'ends_on', af.ends_on)), '[]')
        from public.case_proceedings cp join public.participations pa on pa.proceeding_id = cp.proceeding_id
        join public.actors a on a.id = pa.actor_id
        join public.affiliations af on af.person_actor_id = a.id join public.actors o on o.id = af.organization_actor_id
       where cp.case_id = p_case_id and public.claim_is_public(cp.claim_id) and public.claim_is_public(pa.claim_id)
         and public.claim_is_public(af.claim_id)));
end
$$;

create or replace function public.public_proceeding(p_proceeding_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'proceeding_id', p.id, 'authority', p.authority, 'jurisdiction', p.jurisdiction, 'system', p.system,
      'radicado', p.radicado_original, 'official_url', p.official_url, 'status_original', p.status_original,
      'status', p.status_normalized, 'finality', p.finality, 'last_verified_at', p.last_verified_at,
      'freshness', public.proceeding_freshness(p.system, p.last_verified_at),
      'participations', (select coalesce(jsonb_agg(jsonb_build_object('actor_id', a.id, 'name', a.display_name,
                           'role_original', pa.role_original, 'role', pa.role_normalized) order by a.display_name), '[]')
                           from public.participations pa join public.actors a on a.id = pa.actor_id
                          where pa.proceeding_id = p.id and public.claim_is_public(pa.claim_id)),
      'events', (select coalesce(jsonb_agg(jsonb_build_object('occurred_on', ev.occurred_on, 'event_type', ev.event_type,
                   'description', ev.description, 'date_precision', ev.date_precision)
                   order by ev.occurred_on desc nulls last), '[]')
                   from public.proceeding_events ev where ev.proceeding_id = p.id and public.claim_is_public(ev.claim_id)),
      'evidence', (select coalesce(jsonb_agg(public.evidence_view(ce.evidence_id)), '[]')
                     from public.claim_evidence ce where ce.claim_id = p.claim_id and ce.function = 'supports'))
      from public.proceedings p
     where p.id = p_proceeding_id and (p.claim_id is null or public.claim_is_public(p.claim_id))
       and (p.claim_id is not null or exists (select 1 from public.participations pa
                                               where pa.proceeding_id = p.id and public.claim_is_public(pa.claim_id))));
end
$$;

create or replace function public.public_actor_search(p_tokens text[], p_limit integer default 8) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  if coalesce(cardinality(p_tokens), 0) = 0 then return '[]'; end if;
  return (select coalesce(jsonb_agg(x), '[]') from (
    select jsonb_build_object('actor_id', a.id, 'name', a.display_name, 'actor_type', a.actor_type,
             'territory', (select concat_ws(', ', t.name, d.name) from public.territories t
                             left join public.territories d on d.id = t.parent_id where t.id = a.territory_id)) as x
      from public.actors a
     where a.merged_into_id is null and a.review_state <> 'rejected'
       and a.normalized_name like all (select '%' || public.fold_text(t) || '%' from unnest(p_tokens) t)
       -- Solo actores con algo publicado: una coincidencia de nombre no expone a nadie (T-19).
       and (a.actor_type <> 'persona' or exists (
             select 1 from public.participations pa where pa.actor_id = a.id and public.claim_is_public(pa.claim_id))
            or exists (select 1 from public.positions po where po.person_actor_id = a.id and public.claim_is_public(po.claim_id)))
     order by a.display_name limit least(p_limit, 20)) y);
end
$$;

create or replace function public.public_actor(p_actor_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'actor_id', a.id, 'name', a.display_name, 'actor_type', a.actor_type,
      'territory', (select jsonb_build_object('territory_id', t.id, 'name', t.name, 'code', t.code,
                      'department', (select d.name from public.territories d where d.id = t.parent_id))
                      from public.territories t where t.id = a.territory_id),
      'identifiers', (select coalesce(jsonb_agg(jsonb_build_object('type', i.id_type, 'value', i.value_masked)), '[]')
                        from public.actor_identifiers i where i.actor_id = a.id and a.actor_type <> 'persona'),
      'positions', (select coalesce(jsonb_agg(jsonb_build_object('title', po.title_original, 'entity', e.display_name,
                      'entity_id', e.id, 'person', pp.display_name, 'person_id', pp.id, 'starts_on', po.starts_on,
                      'ends_on', po.ends_on) order by po.starts_on desc nulls last), '[]')
                      from public.positions po join public.actors pp on pp.id = po.person_actor_id
                      left join public.actors e on e.id = po.entity_actor_id
                     where (po.person_actor_id = a.id or po.entity_actor_id = a.id) and public.claim_is_public(po.claim_id)),
      'affiliations', (select coalesce(jsonb_agg(jsonb_build_object('person', pp.display_name, 'person_id', pp.id,
                         'organization', o.display_name, 'organization_id', o.id, 'type', af.affiliation_type,
                         'starts_on', af.starts_on, 'ends_on', af.ends_on)), '[]')
                         from public.affiliations af join public.actors pp on pp.id = af.person_actor_id
                         join public.actors o on o.id = af.organization_actor_id
                        where (af.person_actor_id = a.id or af.organization_actor_id = a.id)
                          and public.claim_is_public(af.claim_id)),
      'participations', (select coalesce(jsonb_agg(jsonb_build_object('proceeding_id', p.id,
                           'proceeding', concat_ws(' · ', p.authority, p.radicado_original), 'role_original', pa.role_original,
                           'role', pa.role_normalized, 'status', p.status_normalized,
                           'freshness', public.proceeding_freshness(p.system, p.last_verified_at))), '[]')
                           from public.participations pa join public.proceedings p on p.id = pa.proceeding_id
                          where pa.actor_id = a.id and public.claim_is_public(pa.claim_id)),
      -- Casos relacionados con el tipo de relación explícito (BUS-02): sujeto procesal directo o por afiliación.
      'cases', (select coalesce(jsonb_agg(distinct jsonb_build_object('case_id', c.id, 'title', r.title, 'relation', rel.relation)), '[]')
                  from (select cp.case_id, 'sujeto_procesal' as relation
                          from public.participations pa join public.case_proceedings cp on cp.proceeding_id = pa.proceeding_id
                         where pa.actor_id = a.id and public.claim_is_public(pa.claim_id) and public.claim_is_public(cp.claim_id)
                        union
                        select cp.case_id, 'por_afiliacion_de_' || pp.display_name
                          from public.affiliations af join public.actors pp on pp.id = af.person_actor_id
                          join public.participations pa on pa.actor_id = af.person_actor_id
                          join public.case_proceedings cp on cp.proceeding_id = pa.proceeding_id
                         where af.organization_actor_id = a.id and public.claim_is_public(af.claim_id)
                           and public.claim_is_public(pa.claim_id) and public.claim_is_public(cp.claim_id)) rel
                  join public.cases c on c.id = rel.case_id join public.case_revisions r on r.id = c.published_revision_id))
      from public.actors a where a.id = p_actor_id and a.merged_into_id is null);
end
$$;

create or replace function public.public_territory_search(p_text text) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (select coalesce(jsonb_agg(jsonb_build_object('territory_id', t.id, 'code', t.code, 'name', t.name,
            'level', t.level, 'department', d.name) order by (t.normalized_name = public.fold_text(p_text)) desc, d.name, t.name), '[]')
            from public.territories t left join public.territories d on d.id = t.parent_id
           where t.level = 'municipio' and (t.normalized_name = public.fold_text(p_text)
                  or (length(p_text) >= 4 and t.normalized_name like public.fold_text(p_text) || '%')));
end
$$;

create or replace function public.public_territory(p_territory_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    select jsonb_build_object(
      'territory_id', t.id, 'code', t.code, 'name', t.name, 'kind', t.kind,
      'department', (select d.name from public.territories d where d.id = t.parent_id),
      'entities', (select coalesce(jsonb_agg(jsonb_build_object('actor_id', a.id, 'name', a.display_name)
                     order by a.display_name), '[]') from (
                     select a.* from public.actors a where a.territory_id = t.id and a.actor_type = 'entidad_publica'
                      and a.merged_into_id is null order by a.display_name limit 12) a),
      'positions', (select coalesce(jsonb_agg(jsonb_build_object('title', po.title_original, 'person', pp.display_name,
                      'person_id', pp.id, 'entity', e.display_name, 'starts_on', po.starts_on, 'ends_on', po.ends_on)
                      order by po.starts_on desc nulls last), '[]')
                      from public.positions po join public.actors pp on pp.id = po.person_actor_id
                      join public.actors e on e.id = po.entity_actor_id
                     where e.territory_id = t.id and public.claim_is_public(po.claim_id)),
      'contracts', (select jsonb_build_object('count', count(*), 'value', sum(v.value_current),
                      'published', bool_and(not s.shadow_mode), 'sources', array_agg(distinct s.code))
                      from public.contracts ct join public.sources s on s.id = ct.source_id
                      left join public.contract_versions v on v.id = ct.current_version_id
                     where ct.territory_id = t.id and not s.shadow_mode),
      'contracts_in_shadow', (select count(*) from public.contracts ct join public.sources s on s.id = ct.source_id
                               where ct.territory_id = t.id and s.shadow_mode),
      'cases', (select coalesce(jsonb_agg(jsonb_build_object('case_id', c.id, 'title', r.title)), '[]')
                  from public.cases c join public.case_revisions r on r.id = c.published_revision_id
                 where t.id = any (r.territory_ids)),
      'pilot', (public.app_setting('pilot_municipalities') -> 'codes') ? t.code)
      from public.territories t where t.id = p_territory_id);
end
$$;

create or replace function public.public_contracts(p_filters jsonb, p_limit integer default 200) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    with f as (
      select ct.id, ct.native_id, ct.object, ct.official_url, s.code as source, v.value_current, v.value_initial,
             v.status_original, v.signed_on, e.display_name as entity, k.display_name as contractor,
             (select count(*) from public.contract_versions x where x.contract_id = ct.id) as versions
        from public.contracts ct join public.sources s on s.id = ct.source_id and not s.shadow_mode
        left join public.contract_versions v on v.id = ct.current_version_id
        left join public.actors e on e.id = ct.entity_actor_id
        left join public.actors k on k.id = ct.contractor_actor_id
       where (p_filters ->> 'territory_id' is null or ct.territory_id = (p_filters ->> 'territory_id')::uuid)
         and (p_filters ->> 'actor_id' is null or (p_filters ->> 'actor_id')::uuid in (ct.entity_actor_id, ct.contractor_actor_id))
         and (p_filters ->> 'case_id' is null or exists (select 1 from public.case_contracts cc
               where cc.contract_id = ct.id and cc.case_id = (p_filters ->> 'case_id')::uuid and public.claim_is_public(cc.claim_id)))
    )
    select jsonb_build_object(
      'known_total', (select count(*) from f),
      'items', coalesce((select jsonb_agg(to_jsonb(x) order by x.signed_on desc nulls last, x.id)
                          from (select * from f order by signed_on desc nulls last, id limit least(p_limit, 200)) x), '[]')));
end
$$;

create or replace function public.public_coverage() returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer', 'operator']);
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'code', s.code, 'name', s.name, 'institution', s.institution, 'approval', s.approval_state,
            'health', s.health_state, 'coverage', s.coverage_state, 'shadow', s.shadow_mode,
            'dataset_updated_at', s.dataset_updated_at,
            'last_success', (select max(finished_at) from public.ingestion_runs r
                              where r.source_id = s.id and r.status in ('succeeded', 'partial')))
            order by s.code), '[]')
            from public.sources s where s.code >= 'SRC-15' or s.capabilities <> '{}');
end
$$;

do $$
declare f text;
begin
  foreach f in array array[
    'app_setting(text)', 'feature_enabled(text)', 'bot_feature_flags()', 'admin_set_feature_flag(text, boolean, text)',
    'editor_create_claim(text, text, text, uuid, text, uuid, jsonb, boolean, uuid[])', 'editor_submit_claim(uuid, integer)',
    'claim_case_ids(uuid)', 'reviewer_decide_claim(uuid, integer, text, text)',
    'correction_for_object(text, uuid, uuid[], text, text)', 'reviewer_retract_claim(uuid, text)',
    'editor_create_case(text, text, text, text, text, text[], text[], uuid[], uuid[], boolean, text)',
    'editor_revise_case(uuid, integer, text, text, text, text, text[], text[], uuid[], uuid[], boolean, text)',
    'editor_submit_case_revision(uuid)', 'reviewer_decide_case_revision(uuid, integer, text, text)',
    'reviewer_retract_case(uuid, text)',
    'editor_upsert_proceeding(text, text, text, text, text, text, text, text, text, uuid, uuid)',
    'editor_create_actor(text, text, uuid)', 'editor_link(text, uuid, uuid, text, text, text, date, date, uuid)',
    'proceeding_freshness(text, timestamptz)', 'claim_is_public(uuid)', 'evidence_view(uuid)', 'case_branches(uuid)',
    'public_cases_list(jsonb, integer)', 'public_case(uuid)', 'public_case_timeline(uuid)', 'public_case_actors(uuid)',
    'public_proceeding(uuid)', 'public_actor_search(text[], integer)', 'public_actor(uuid)',
    'public_territory_search(text)', 'public_territory(uuid)', 'public_contracts(jsonb, integer)', 'public_coverage()']
  loop
    execute format('revoke execute on function public.%s from public, anon', f);
  end loop;
  -- Solo las RPC con verificación de rol se exponen a usuarios autenticados.
  foreach f in array array[
    'bot_feature_flags()', 'admin_set_feature_flag(text, boolean, text)',
    'editor_create_claim(text, text, text, uuid, text, uuid, jsonb, boolean, uuid[])', 'editor_submit_claim(uuid, integer)',
    'reviewer_decide_claim(uuid, integer, text, text)', 'reviewer_retract_claim(uuid, text)',
    'editor_create_case(text, text, text, text, text, text[], text[], uuid[], uuid[], boolean, text)',
    'editor_revise_case(uuid, integer, text, text, text, text, text[], text[], uuid[], uuid[], boolean, text)',
    'editor_submit_case_revision(uuid)', 'reviewer_decide_case_revision(uuid, integer, text, text)',
    'reviewer_retract_case(uuid, text)',
    'editor_upsert_proceeding(text, text, text, text, text, text, text, text, text, uuid, uuid)',
    'editor_create_actor(text, text, uuid)', 'editor_link(text, uuid, uuid, text, text, text, date, date, uuid)',
    'public_cases_list(jsonb, integer)', 'public_case(uuid)', 'public_case_timeline(uuid)', 'public_case_actors(uuid)',
    'public_proceeding(uuid)', 'public_actor_search(text[], integer)', 'public_actor(uuid)',
    'public_territory_search(text)', 'public_territory(uuid)', 'public_contracts(jsonb, integer)', 'public_coverage()']
  loop
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end
$$;
