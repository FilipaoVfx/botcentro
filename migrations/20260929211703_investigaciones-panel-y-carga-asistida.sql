-- Investigaciones: listas de expedientes, lectura del panel, carga documental asistida y comandos
-- idempotentes (investigaciones §11.2, §15, CAS-06/08, EVI-04/05, ADM-03; T-15, T-28, T-55).

create or replace function public.public_proceedings_list(p_filters jsonb, p_limit integer default 200) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    with visible as (
      select p.*, public.proceeding_freshness(p.system, p.last_verified_at) as freshness,
             (select t.name from public.territories t where t.id = p.territory_id) as territory,
             (select d.name from public.territories t join public.territories d on d.id = t.parent_id
               where t.id = p.territory_id) as department,
             (select string_agg(a.display_name, ', ' order by a.display_name) from public.participations pa
                join public.actors a on a.id = pa.actor_id
               where pa.proceeding_id = p.id and public.claim_is_public(pa.claim_id)) as actors
        from public.proceedings p
       where (p.claim_id is null or public.claim_is_public(p.claim_id))
         and exists (select 1 from public.participations pa where pa.proceeding_id = p.id and public.claim_is_public(pa.claim_id))
    ), filtered as (
      select * from visible v
       where (p_filters ->> 'territory_id' is null or v.territory_id = (p_filters ->> 'territory_id')::uuid)
         and (p_filters ->> 'jurisdiction' is null or v.jurisdiction = p_filters ->> 'jurisdiction')
         and (p_filters ->> 'status' is null or v.status_normalized = p_filters ->> 'status')
         -- «Investigaciones activas» solo con estado vigente verificado dentro del umbral (CAS-06).
         and (coalesce((p_filters ->> 'active_only')::boolean, false) = false
              or (v.freshness = 'verificado' and v.status_normalized in ('indagacion', 'investigacion', 'juicio')))
    )
    select jsonb_build_object(
      'as_of', now(), 'known_total', (select count(*) from filtered),
      'coverage', 'Expedientes publicados tras revisión en nuestra cobertura; no es un censo nacional.',
      'items', coalesce((select jsonb_agg(jsonb_build_object(
          'proceeding_id', f.id, 'authority', f.authority, 'jurisdiction', f.jurisdiction, 'system', f.system,
          'radicado', f.radicado_original, 'status_original', f.status_original, 'status', f.status_normalized,
          'finality', f.finality, 'freshness', f.freshness, 'last_verified_at', f.last_verified_at,
          'territory', f.territory, 'department', f.department, 'actors', f.actors)
          order by f.last_verified_at desc nulls last, f.id)
          from (select * from filtered order by last_verified_at desc nulls last, id limit least(p_limit, 200)) f), '[]')));
end
$$;

-- Lectura del panel: fuentes, esquema, cola editorial, casos, outbox y entregas.
create or replace function public.ops_investigations() returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['admin', 'operator', 'editor', 'reviewer', 'auditor']);
begin
  return jsonb_build_object(
    'as_of', now(), 'access', true,
    'environment', public.app_setting('environment'),
    'flags', (select coalesce(jsonb_agg(jsonb_build_object('key', key, 'enabled', enabled, 'description', description,
                'updated_at', updated_at) order by key), '[]') from public.feature_flags),
    'sources', (select coalesce(jsonb_agg(jsonb_build_object(
                  'code', s.code, 'name', s.name, 'institution', s.institution, 'state', s.state,
                  'approval', s.approval_state, 'health', s.health_state, 'coverage', s.coverage_state,
                  'shadow', s.shadow_mode, 'capabilities', s.capabilities, 'dataset_updated_at', s.dataset_updated_at,
                  'schema', (select jsonb_build_object('result', v.result, 'fingerprint', left(v.fingerprint, 12),
                               'missing_optional', v.missing_optional, 'missing_critical', v.missing_critical,
                               'validated_at', v.validated_at)
                               from public.source_schema_versions v where v.source_id = s.id order by v.validated_at desc limit 1),
                  'last_run', (select jsonb_build_object('status', r.status, 'started_at', r.started_at,
                                 'finished_at', r.finished_at, 'error_code', r.error_code)
                                 from public.ingestion_runs r where r.source_id = s.id order by r.started_at desc limit 1),
                  'observations', (select count(*) from public.observations o where o.source_id = s.id))
                  order by s.code), '[]')
                  from public.sources s where s.code >= 'SRC-15'),
    'claims', (select jsonb_object_agg(state, n) from (select state, count(*) n from public.claims group by state) x),
    'queue', (select coalesce(jsonb_agg(jsonb_build_object(
                'claim_id', c.id, 'statement', c.statement, 'origin', c.origin, 'author', coalesce(c.author_label, 'editor'),
                'own', c.author_id = v_uid, 'version', c.version, 'submitted_at', c.submitted_at, 'sensitive', c.sensitive,
                'evidence', (select coalesce(jsonb_agg(public.evidence_view(ce.evidence_id)), '[]')
                               from public.claim_evidence ce where ce.claim_id = c.id))
                order by c.submitted_at nulls last, c.id), '[]')
                from (select * from public.claims where state = 'pending_review' order by submitted_at nulls last, id limit 30) c),
    'cases', (select coalesce(jsonb_agg(jsonb_build_object(
                'case_id', c.id, 'slug', c.slug, 'latest_revision_no', c.latest_revision_no,
                'published', c.published_revision_id is not null,
                'latest', (select jsonb_build_object('revision_id', r.id, 'title', r.title, 'state', r.state,
                             'own', r.author_id = v_uid, 'evidence', cardinality(r.evidence_ids))
                             from public.case_revisions r where r.case_id = c.id and r.revision_no = c.latest_revision_no))
                order by c.updated_at desc), '[]') from public.cases c),
    'contracts', (select jsonb_build_object('contracts', count(*), 'versions', (select count(*) from public.contract_versions))
                    from public.contracts),
    'actors', (select jsonb_object_agg(actor_type, n) from (select actor_type, count(*) n from public.actors group by 1) x),
    'territories', (select count(*) from public.territories),
    'outbox', (select coalesce(jsonb_agg(jsonb_build_object('event_type', e.event_type, 'summary', e.summary,
                 'created_at', e.created_at) order by e.created_at desc), '[]')
                 from (select * from public.outbox_events order by created_at desc limit 15) e),
    'deliveries', (select jsonb_object_agg(state, n) from (select state, count(*) n from public.notification_deliveries group by 1) x),
    'subscriptions', (select count(*) from public.subscriptions where active));
end
$$;

-- Carga documental asistida (§11.2): el editor aporta la URL de una publicación oficial; el
-- servidor descarga, extrae el texto y registra captura, versión y páginas. El mismo URL con otros
-- bytes crea otra versión (T-15); con los mismos bytes no crea nada nuevo (T-14).
create or replace function public.editor_register_document(
  p_source_code text, p_url text, p_final_url text, p_title text, p_document_type text, p_content_hash text,
  p_byte_size bigint, p_mime text, p_object_key text, p_pages jsonb, p_extractor_version text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid      uuid := public.require_app_role(array['editor', 'admin']);
  v_source   public.sources%rowtype;
  v_record   uuid;
  v_snapshot uuid;
  v_doc      uuid;
  v_rev      uuid;
  v_ext      uuid;
  v_new      boolean := false;
  v_bad      integer;
begin
  select * into v_source from public.sources where code = p_source_code;
  if not found then raise exception using errcode = 'P0002', message = 'fuente inexistente'; end if;
  insert into public.source_records (source_id, record_type, logical_key, canonical_url)
  values (v_source.id, 'documento_asistido', p_url, p_url)
  on conflict (source_id, logical_key) do update set last_seen_at = now()
  returning id into v_record;
  insert into public.source_snapshots (record_id, requested_url, final_url, http_status, content_hash, byte_size, mime_type,
                                       object_key, adapter_version, fetched_at)
  values (v_record, p_url, coalesce(p_final_url, p_url), 200, p_content_hash, p_byte_size, p_mime, p_object_key,
          'carga-asistida-1', now())
  on conflict (record_id, content_hash) do nothing
  returning id into v_snapshot;
  if v_snapshot is null then
    select id into v_snapshot from public.source_snapshots where record_id = v_record and content_hash = p_content_hash;
  end if;
  insert into public.blobs (content_hash, byte_size, detected_mime, object_key, stored_at, verified_at)
  values (p_content_hash, p_byte_size, p_mime, p_object_key, case when p_object_key is not null then now() end,
          case when p_object_key is not null then now() end)
  on conflict (content_hash) do nothing;
  insert into public.documents (document_key, document_type, title)
  values ('asistido:' || encode(sha256(convert_to(p_url, 'UTF8')), 'hex'), coalesce(p_document_type, 'otro'), p_title)
  on conflict (document_key) do update set title = coalesce(excluded.title, documents.title)
  returning id into v_doc;
  select id into v_rev from public.document_revisions where document_id = v_doc and blob_hash = p_content_hash;
  if v_rev is null then
    v_new := true;
    insert into public.document_revisions (document_id, blob_hash, mime_type, byte_size, supersedes_id)
    values (v_doc, p_content_hash, coalesce(p_mime, 'application/octet-stream'), p_byte_size,
            (select r.id from public.document_revisions r where r.document_id = v_doc order by r.created_at desc limit 1))
    returning id into v_rev;
    insert into public.document_origins (revision_id, snapshot_id, source_record_id, source_url)
    values (v_rev, v_snapshot, v_record, coalesce(p_final_url, p_url));
    v_bad := (select count(*) from jsonb_array_elements(p_pages) pg where pg ->> 'quality' <> 'accepted');
    insert into public.extraction_runs (revision_id, extractor_version, ocr_version, config_hash, status, quality_status,
                                        page_count, pages_ocr, started_at, finished_at)
    values (v_rev, p_extractor_version, 'tesseract-5-spa', encode(sha256(convert_to(p_extractor_version, 'UTF8')), 'hex'),
            case when v_bad > 0 then 'partial' else 'succeeded' end,
            case when v_bad > 0 then 'review_required' else 'accepted' end,
            jsonb_array_length(p_pages), (select count(*) from jsonb_array_elements(p_pages) pg where pg ->> 'method' = 'ocr'),
            now(), now())
    returning id into v_ext;
    insert into public.document_pages (extraction_id, pdf_page, text, char_start, char_end, method, quality_status)
    select v_ext, (pg ->> 'page')::int, pg ->> 'text', 0, char_length(coalesce(pg ->> 'text', '')), pg ->> 'method',
           pg ->> 'quality'
      from jsonb_array_elements(p_pages) pg;
    perform public.write_audit(v_uid, 'document.assisted_upload', 'document_revision', v_rev::text, null,
                               jsonb_build_object('url', p_url, 'hash', p_content_hash, 'pages', jsonb_array_length(p_pages),
                                                  'quarantined_pages', v_bad), 'Carga documental asistida');
  else
    select id into v_ext from public.extraction_runs where revision_id = v_rev order by created_at desc limit 1;
  end if;
  return jsonb_build_object('revision_id', v_rev, 'extraction_id', v_ext, 'new_version', v_new, 'document_id', v_doc,
                            'quarantined_pages', (select count(*) from public.document_pages
                                                   where extraction_id = v_ext and quality_status <> 'accepted'));
end
$$;

-- Evidencia por fragmento: solo sobre páginas aceptadas (T-28: una página ilegible queda en cuarentena).
create or replace function public.editor_create_evidence_passage(p_revision_id uuid, p_page integer, p_char_start integer,
                                                                 p_char_end integer) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_ext  uuid;
  v_page public.document_pages%rowtype;
  v_key  text;
  v_id   uuid;
begin
  perform public.require_app_role(array['editor', 'admin']);
  select id into v_ext from public.extraction_runs where revision_id = p_revision_id order by created_at desc limit 1;
  select * into v_page from public.document_pages where extraction_id = v_ext and pdf_page = p_page;
  if not found then raise exception using errcode = 'P0002', message = 'página inexistente'; end if;
  if v_page.quality_status <> 'accepted' then
    raise exception using errcode = 'BC422', message = 'la página está en cuarentena por calidad de extracción: revísala antes de citarla';
  end if;
  if p_char_start < 0 or p_char_end <= p_char_start or p_char_end > char_length(coalesce(v_page.text, '')) then
    raise exception using errcode = '22023', message = 'rango de caracteres fuera de la página';
  end if;
  v_key := encode(sha256(convert_to(format('passage:%s:%s:%s:%s', p_revision_id, p_page, p_char_start, p_char_end), 'UTF8')), 'hex');
  insert into public.evidence (evidence_key, kind, document_revision_id, extraction_id, pdf_page_start, pdf_page_end,
                               char_start, char_end, excerpt_hash)
  values (v_key, 'document_passage', p_revision_id, v_ext, p_page, p_page, p_char_start, p_char_end,
          encode(sha256(convert_to(substr(v_page.text, p_char_start + 1, p_char_end - p_char_start), 'UTF8')), 'hex'))
  on conflict (evidence_key) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.evidence where evidence_key = v_key;
  end if;
  return v_id;
end
$$;

-- Comandos del panel con clave de idempotencia (ADM-03, API-03): la misma clave con el mismo
-- contenido devuelve el resultado anterior; con otro contenido, conflicto.
create or replace function public.admin_command_begin(p_type text, p_key text, p_payload_hash text) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['admin', 'operator', 'editor', 'reviewer']);
  v_cmd public.admin_commands%rowtype;
begin
  select * into v_cmd from public.admin_commands where actor_id = v_uid and command_type = p_type and idempotency_key = p_key;
  if found then
    if v_cmd.payload_hash <> p_payload_hash then
      raise exception using errcode = 'BC409', message = 'la clave de idempotencia ya se usó con otro contenido';
    end if;
    return jsonb_build_object('command_id', v_cmd.id, 'replayed', true, 'state', v_cmd.state, 'result', v_cmd.result);
  end if;
  insert into public.admin_commands (actor_id, command_type, idempotency_key, payload_hash)
  values (v_uid, p_type, p_key, p_payload_hash) returning * into v_cmd;
  return jsonb_build_object('command_id', v_cmd.id, 'replayed', false, 'state', v_cmd.state);
end
$$;

create or replace function public.admin_command_finish(p_command_id uuid, p_state text, p_result jsonb) returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := public.require_app_role(array['admin', 'operator', 'editor', 'reviewer']);
begin
  update public.admin_commands set state = p_state, result = p_result
   where id = p_command_id and actor_id = v_uid and state = 'accepted';
end
$$;

do $$
declare f text;
begin
  foreach f in array array['public_proceedings_list(jsonb, integer)', 'ops_investigations()',
    'editor_register_document(text, text, text, text, text, text, bigint, text, text, jsonb, text)',
    'editor_create_evidence_passage(uuid, integer, integer, integer)', 'admin_command_begin(text, text, text)',
    'admin_command_finish(uuid, text, jsonb)']
  loop
    execute format('revoke execute on function public.%s from public, anon', f);
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end
$$;
