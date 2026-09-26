# Trazabilidad de implementación

Estado del código respecto al SRS (sección 17) a fecha 2026-09-26. Leyenda:
- ✅ implementado y probado;
- 🟡 parcial (base lista y falta una pieza que se indica);
- ⬜ pendiente.

Rutas relativas a `src/botcentro/`, salvo las migraciones.

## Requisitos funcionales

| Req. | Estado | Implementación | Pruebas | Falta |
|---|---|---|---|---|
| F01 Registro de fuentes | ✅ | `sources`, `admin_set_source_state`, `enforce_source_activation` | T-01 | — |
| F02 Perfil de uso | ✅ | `source_policies` (versionada, append-only), `sources/policy.py`, el runner aplica captura, descarga y conservación | T-01, `test_query.py` | — |
| F03 Incrementalidad | 🟡 | `connectors/base.py` (cursor con versión y alcance), `ingest/runner.py` (confirmación por página), deduplicación por hash | T-02 | Pasar ETag/Last-Modified previos al conector; conectores reales (H0) |
| F04 Idempotencia | ✅ | `jobs` (clave única), `observation_key`, `evidence_key`, capturas únicas por hash | T-02, T-03 | — |
| F05 Tolerancia a fallos | ✅ | `connectors/retry.py`, `jobs/queue.py`, `ingest_suspend_source`, cuarentena y caso de revisión por cambio de esquema | T-04 (401, esquema, clasificación HTTP) | Prueba de carga con 429/5xx reales |
| F06 Capturas y procedencia | ✅ | `source_snapshots`, `source_checks`, `storage/objects.py` | T-02, T-12 | Adaptador de buckets de InsForge |
| F07 Identidad de proyectos | 🟡 | `project_identifiers` (clave contextual), `domain/project_ids.py` | T-05, T-06 (parser) | Resolutor que crea identificadores y casos de revisión |
| F08 Personas y membresías | 🟡 | Esquema con intervalos y evidencia, `domain/names.py` | — | Resolutor de personas (T-07) |
| F09 Votaciones y asistencia | 🟡 | Esquema nominal y agregado separado, `domain/votes.py` | T-08 (unitaria) | Cargadores y consultas de votos |
| F10 Observaciones y estado | 🟡 | `observations`, `project_status_projection`, evidencia obligatoria al publicar | BC003 | Regla que calcula la proyección y marca conflictos (T-10) |
| F11 Agenda e historia | 🟡 | `agenda_items/revisions` con fecha de Bogotá validada, `domain/dates.py` | T-11 | Consultas de agenda |
| F12 Versionamiento documental | 🟡 | `blobs`, `document_revisions` inmutables, `document_origins` | — | Pipeline documental (§8) |
| F13 Extracción y OCR | 🟡 | `documents/quality.py`, `extraction_runs`, `document_pages` | T-13 (calidad) | Extractor nativo y adaptador de OCR |
| F14 Segmentación | ✅ | `documents/chunking.py` | T-14, T-15 | Persistir chunks desde el pipeline |
| F15 Comparación | ⬜ | Esquema `document_comparisons` | — | Alineación por artículos (T-16) |
| F16 Resolución de intención | 🟡 | `query/intent.py` (reglas, entidades, fechas explícitas) | `test_query.py` | Contexto de aclaración de corta duración |
| F17 Consultas estructuradas | ⬜ | — | — | Plantillas parametrizadas de solo lectura (T-17) |
| F18 Búsqueda híbrida | 🟡 | `search_chunks_lexical`, `search_chunks_vector` | búsqueda en `test_telegram_and_review.py` | Fusión RRF, reranking, filtros por autoridad |
| F19 Respuesta sustentada | 🟡 | `query/validator.py` | T-19 | Compositor de respuestas y registro de `query_evidence` |
| F20 Calidad y desacuerdos | 🟡 | Separación de autoridades y bloqueo de citas de baja calidad en el validador | T-20 | Presentación de conflictos |
| F21 Enriquecimiento | ⬜ | Autoridad `secondary` en el esquema | — | Adaptador de Congreso Visible |
| F22 Recepción del bot | ✅ | `telegram/webhook.py`, `telegram_accept_update`, `api/app.py` | T-21 | — |
| F23 Entrega y continuidad | 🟡 | `telegram/render.py`, callbacks firmados, `delivery_attempts` | T-22 | Worker de envío y reconciliación de envíos ambiguos |
| F24 Administración | 🟡 | RPC `admin_*`, `review_*`, `jobs_*` auditadas | T-23 | API HTTP y vista mínima de salud y casos |
| F25 Correcciones | 🟡 | `review_resolutions` (antes/después, reversión), auditoría, trabajo `maintenance.apply_resolution` | T-23 | Worker que aplica e invalida derivados |
| F26 Telemetría y evaluación | ⬜ | Contadores por ejecución, `trace_id` | — | Métricas y corpus de evaluación |
| F27–F28 | ⬜ | Futuro (fases 2–3) | — | — |

## Requisitos no funcionales

| Req. | Estado | Implementación | Pruebas |
|---|---|---|---|
| N01 Autenticación y autorización | ✅ | `app_roles`, RLS en las 71 tablas, cero privilegios para `anon`, RPC con `require_app_role` | T-24, `test_access_and_sources.py` |
| N02 Datos y secretos | 🟡 | Secretos fuera del repositorio, seudonimización HMAC, usuarios no autorizados sin almacenar | `test_telegram.py` |
| N03 Contenido no confiable | 🟡 | Contenido tratado como datos; el validador rechaza citas inventadas | T-19 | 
| N04 Protección de adquisición | ✅ | `security/url_guard.py` y `http/fetcher.py` (validación en la conexión, redirecciones, límites) | T-25 (SSRF) |
| N05 Retención | 🟡 | Columnas `expires_at`, `maintenance_purge_expired`, guardián de auditoría a 365 días | Retención en `test_telegram_and_review.py` |
| N11 Integridad | ✅ | Hashes, append-only, deduplicación | T-02, T-12 |
| N14 Observabilidad | 🟡 | `trace_id` en ejecuciones, trabajos y consultas | — |
| N17 Costos | ✅ | `budget_reserve` atómica, alerta al 80 %, bloqueo, conciliación | T-29 |
| N07–N10, N12, N13, N15, N16, N18 | ⬜ | Dependen del piloto, la carga y la restauración | — |

## Pruebas de aceptación cubiertas

T-01, T-02, T-03, T-04 (parcial), T-05, T-06 (parser), T-08 (unitaria), T-11, T-12, T-13 (calidad y límites), T-14, T-15, T-19, T-20, T-21, T-22, T-23, T-24, T-25 (SSRF), T-29.
