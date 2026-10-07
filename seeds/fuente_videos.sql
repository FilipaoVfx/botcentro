-- SRC-26: videos oficiales de sesiones del Congreso (feeds RSS oficiales de YouTube). Idempotente.
-- Solo metadatos y enlace (download_files denegado: no se descarga video ni audio).
with src as (
  insert into public.sources (code, name, authority, phase, base_url, allowed_domains, supported_objects, notes, adapter,
                              adapter_version, owner, poll_interval, institution, access_method, capabilities,
                              shadow_mode, approval_state)
  values ('SRC-26', 'YouTube oficial del Congreso · videos de sesiones (RSS)', 'primary', 'mvp',
          'https://www.youtube.com/feeds/videos.xml', '{www.youtube.com}', '{session_video}',
          'Canal Congreso (enlazado desde senado.gov.co y camara.gov.co) y Cámara de Representantes (@CamaraColombia). '
          'Feed oficial: últimos 15 videos por canal; se lee cinco veces al día. YouTube no entrega subtítulos ni detalle '
          'a servidores sin iniciar sesión, y eso no se elude.',
          'youtube_congreso', '0.1.0', 'juanku2003@gmail.com', interval '6 hours', 'Congreso de la República',
          'rss_oficial', '{"discovery":"yes","metadata":"yes","transcripts":"unsupported","history":"unsupported"}',
          true, 'pilot')
  on conflict (code) do update set name = excluded.name, notes = excluded.notes, capabilities = excluded.capabilities
  returning id
), pol as (
  insert into public.source_policies (source_id, version, capture_metadata, download_files, retain_content,
                                      generate_derivatives, index_content, redistribute, retention_policy, evidence_url,
                                      reviewer, reviewed_at, approval_reason)
  select id, 1, 'allowed', 'denied', 'denied', 'denied', 'denied', 'allowed',
         'Metadatos y enlace del video oficial; sin descarga de video ni audio.',
         'https://www.youtube.com/feeds/videos.xml?channel_id=UCJfKMFp7dtO8mpHT56X0kJQ', 'juanku2003@gmail.com', now(),
         'Feed RSS oficial y público de YouTube; enlazar el video oficial de la sesión al dato que consulta el usuario.'
    from src
  on conflict (source_id, version) do nothing
  returning id
), cov as (
  insert into public.coverage_scopes (source_id, object_type, from_date, status, limitations)
  select id, 'session_video', date '2026-10-06', 'planned',
         'Feed oficial: últimos 15 videos por canal (unos 4 días en Canal Congreso y 2-3 semanas en la Cámara); '
         'sin historial anterior a la primera lectura salvo lo que el feed traía ese día; sin subtítulos ni duración; '
         'solo sesiones cuyo título identifica cuerpo y fecha'
    from src where not exists (select 1 from public.coverage_scopes c where c.source_id = src.id)
  returning 1
)
select 1;

update public.sources s set policy_id = p.id, state = 'active', state_reason = 'Feeds oficiales verificados 2026-10-06'
  from public.source_policies p
 where p.source_id = s.id and p.version = 1 and s.code = 'SRC-26' and s.state = 'candidate';
