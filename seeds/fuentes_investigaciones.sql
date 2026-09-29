-- Fuentes del módulo de investigaciones (investigaciones §6; DEC-19). Idempotente.
-- Las fuentes de datos.gov.co quedan activas en MODO SOMBRA: ingieren y se normalizan, pero no
-- publican ni notifican (MIG-02). Aprobación «pilot» ≠ certificadas para producción (§2).
-- CPNU, Corte Suprema, Contraloría y Fiscalía quedan como candidatas (§3.2, §4).

with specs(code, name, institution, adapter, base_url, domains, objects, capabilities, redistribute, notes) as (values
  ('SRC-15', 'SECOP II · Contratos electrónicos (jbjy-vk9h)', 'Colombia Compra Eficiente', 'secop2_contracts',
   'https://www.datos.gov.co/resource/jbjy-vk9h.json', '{www.datos.gov.co}'::text[], '{contract,actor}'::text[],
   '{"discovery":"yes","detail":"yes","incremental":"yes","history":"partial","identifiers":"yes","deletions":"unsupported","totals":"yes","documents":"unsupported"}'::jsonb,
   'allowed', 'CC BY-SA 4.0. Contratación no acredita irregularidad. Alcance piloto por municipio y fecha de firma (incluye contratos sin firma).'),
  ('SRC-16', 'SECOP II · Procesos de contratación (p6dx-8zbt)', 'Colombia Compra Eficiente', 'secop2_processes',
   'https://www.datos.gov.co/resource/p6dx-8zbt.json', '{www.datos.gov.co}'::text[], '{contract_process}'::text[],
   '{"discovery":"yes","detail":"yes","incremental":"unsupported","history":"partial","identifiers":"yes","totals":"yes"}'::jsonb,
   'allowed', 'CC BY-SA 4.0. Mapeo de columnas verificado contra metadatos antes de cargar (T-08).'),
  ('SRC-17', 'SECOP I · Procesos de compra pública (f789-7hwg)', 'Colombia Compra Eficiente', 'secop1',
   'https://www.datos.gov.co/resource/f789-7hwg.json', '{www.datos.gov.co}'::text[], '{contract,actor}'::text[],
   '{"discovery":"yes","detail":"yes","incremental":"yes","history":"yes","identifiers":"yes","totals":"yes"}'::jsonb,
   'allowed', 'CC BY-SA 4.0. Sin heredar filtros de adjudicado (§11.1).'),
  ('SRC-18', 'SIRI · Antecedentes disciplinarios, penales, fiscales y contractuales (iaeu-rcn6)',
   'Procuraduría General de la Nación', 'siri', 'https://www.datos.gov.co/resource/iaeu-rcn6.json',
   '{www.datos.gov.co}'::text[], '{sanction_record,actor}'::text[],
   '{"discovery":"yes","detail":"yes","incremental":"unsupported","history":"yes","identifiers":"protected","totals":"yes"}'::jsonb,
   'denied', 'CC BY-SA 4.0, pero contiene datos personales: documentos solo como HMAC y enmascarados; cada sanción es una afirmación pendiente de revisión (EVI-10). No representa todas las investigaciones activas.'),
  ('SRC-19', 'Relatoría de la Procuraduría (rhun-uf37)', 'Procuraduría General de la Nación', 'relatoria_pgn',
   'https://www.datos.gov.co/resource/rhun-uf37.json', '{www.datos.gov.co}'::text[], '{official_document}'::text[],
   '{"discovery":"yes","documents":"link_only","detail":"unsupported"}'::jsonb,
   'allowed', 'CC BY-SA 4.0. Índice documental, no censo de expedientes.'),
  ('SRC-20', 'DIVIPOLA · Códigos de municipios (gdxc-w37w)', 'DANE', 'divipola',
   'https://www.datos.gov.co/resource/gdxc-w37w.json', '{www.datos.gov.co}'::text[], '{territory}'::text[],
   '{"discovery":"yes","identifiers":"yes"}'::jsonb, 'allowed', 'CC BY-SA 4.0. Identidad territorial oficial (DAT-03).')
), upserted as (
  insert into public.sources (code, name, authority, phase, base_url, allowed_domains, supported_objects, notes, adapter,
                              adapter_version, owner, poll_interval, institution, access_method, capabilities,
                              shadow_mode, approval_state)
  select code, name, 'primary', 'mvp', base_url, domains, objects, notes, adapter, '0.1.0', 'juanku2003@gmail.com',
         case when code in ('SRC-15', 'SRC-16', 'SRC-17') then interval '12 hours' else interval '1 day' end,
         institution, 'socrata_api', capabilities, true, 'pilot'
    from specs
  on conflict (code) do update set name = excluded.name, capabilities = excluded.capabilities,
                                    institution = excluded.institution, access_method = excluded.access_method
  returning id, code
), policies as (
  insert into public.source_policies (source_id, version, capture_metadata, download_files, retain_content,
                                      generate_derivatives, index_content, redistribute, retention_policy, evidence_url,
                                      reviewer, reviewed_at, approval_reason)
  select u.id, 1, 'allowed', 'allowed', 'allowed', 'allowed', 'allowed', s.redistribute,
         'Capturas JSON con acceso restringido; conservar mientras la fuente esté activa; citar la fuente (CC BY-SA 4.0).',
         'https://www.datos.gov.co/api/views/' || split_part(split_part(s.base_url, '/resource/', 2), '.json', 1) || '.json',
         'juanku2003@gmail.com', now(),
         'Licencia CC BY-SA 4.0 verificada en metadatos el 2026-09-29; piloto en modo sombra por encargo del responsable («desarrolla el feature completo»).'
    from upserted u join specs s on s.code = u.code
  on conflict (source_id, version) do nothing
  returning id, source_id
), coverage as (
  insert into public.coverage_scopes (source_id, object_type, from_date, status, limitations)
  select u.id, s.objects[1], case when u.code in ('SRC-15', 'SRC-16', 'SRC-17') then date '2026-01-01' end, 'planned',
         case when u.code in ('SRC-15', 'SRC-16', 'SRC-17', 'SRC-18')
              then 'Piloto: Florencia (18001), Buenaventura (76109) y Arauca (81001), selección propuesta por disponibilidad documental'
              else 'Conjunto completo' end
    from upserted u join specs s on s.code = u.code
   where not exists (select 1 from public.coverage_scopes c where c.source_id = u.id)
  returning source_id
)
select 1;

-- Activación en una sentencia aparte: un UPDATE no ve filas insertadas por CTEs de la misma sentencia.
update public.sources s set policy_id = p.id, state = 'active',
       state_reason = 'Descubrimiento verificado 2026-09-29; activa en modo sombra (MIG-02)'
  from public.source_policies p
 where p.source_id = s.id and p.version = 1 and s.code between 'SRC-15' and 'SRC-20' and s.state = 'candidate';

insert into public.sources (code, name, authority, phase, base_url, allowed_domains, supported_objects, notes,
                            institution, access_method, capabilities, shadow_mode, approval_state)
values
  ('SRC-21', 'CPNU · Consulta de procesos de la Rama Judicial', 'primary', 'mvp_conditional',
   'https://consultaprocesos.ramajudicial.gov.co/Procesos/Index', '{consultaprocesos.ramajudicial.gov.co}', '{proceeding}',
   'Consulta humana enlazada; automatización no certificada (ENABLE_CPNU_AUTOMATION=false).', 'Rama Judicial', 'portal_web',
   '{"discovery":"unsupported","detail":"manual_link"}', true, 'candidate'),
  ('SRC-22', 'Corte Suprema · Estados y boletines', 'primary', 'mvp', 'https://cortesuprema.gov.co/',
   '{cortesuprema.gov.co,www.cortesuprema.gov.co}', '{official_document,proceeding_event}',
   'Ingreso asistido de publicaciones oficiales con revisión editorial antes de publicar (§11.2).', 'Corte Suprema de Justicia',
   'carga_asistida', '{"discovery":"manual","documents":"assisted"}', true, 'candidate'),
  ('SRC-23', 'Contraloría · Boletines de responsables fiscales', 'primary', 'mvp_conditional', 'https://www.contraloria.gov.co/',
   '{contraloria.gov.co,www.contraloria.gov.co}', '{sanction_record}',
   'Candidata: la auditoría registró fallo de verificación TLS; no se desactiva TLS (ING-06).', 'Contraloría General de la República',
   'portal_web', '{"discovery":"unsupported"}', true, 'candidate'),
  ('SRC-24', 'Fiscalía · Estadísticas y publicaciones', 'primary', 'mvp_conditional', 'https://www.fiscalia.gov.co/',
   '{fiscalia.gov.co,www.fiscalia.gov.co}', '{official_document}',
   'Contexto y documentos verificables; las estadísticas no reconstruyen cada investigación.', 'Fiscalía General de la Nación',
   'portal_web', '{"discovery":"unsupported"}', true, 'candidate')
on conflict (code) do nothing;
