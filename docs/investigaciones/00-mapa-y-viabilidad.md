# Investigaciones y grandes casos: fase 0 (mapa, viabilidad y decisiones)

Documento de entrada: [`investigaciones`](../../investigaciones), v1.0 del 2026-09-29. Esta fase inspeccionó el repositorio real y las fuentes en vivo el 2026-09-29, antes de modificar código (§1, §23 fase 0).

## 1. Veredicto

La especificación es **viable como P0 en este producto**, con tres límites que no dependen del código:

1. **Corpus editorial:** no puede fabricarse. La meta de cinco casos, 15 expedientes y 3 jurisdicciones (§22) exige investigación con documentos oficiales y revisión humana. §1 y §27 prohíben inventarla. Se entrega la herramienta completa: carga asistida, afirmaciones con evidencia, revisión y publicación. El corpus real queda como tarea editorial.
2. **Segunda persona revisora:** EVI-10 exige, en producción, que una persona distinta del autor apruebe cada atribución sensible. Hoy solo hay un administrador. Por eso la publicación pública de atribuciones sensibles queda **bloqueada en producción** hasta que exista un segundo revisor. Es una regla de dominio en la base, no de la interfaz.
3. **Espacio de InsForge:** está en el plan gratuito, con 336 de 500 MB. El piloto acotado de 3 municipios cabe: se estiman unos 25 MB de contratos y sanciones. Una cobertura nacional de SECOP (millones de filas) no cabe sin InsForge Pro.

Se descartan o condicionan, como pide §4:
- **Repositorios externos:** no se copia código de `pysecop`, `secop2-etl-pipeline`, `JudicialAIProject`, `openquery`, `antecedentes-…` ni `gitreverse`. Los adaptadores son propios (INT-01). Los cuatro defectos auditados de `pysecop` (INT-02) se prueban como requisitos del adaptador propio (T-01, T-02, T-04, T-05).
- **CPNU:** desactivada (`ENABLE_CPNU_AUTOMATION=false`).
- **Contraloría y Fiscalía:** quedan como candidatas.
- **Proveedores comerciales** (CoreSoft, Verifik, Solverio): deshabilitados, presupuesto 0 (COS-01).

**Archivos citados que no están en el repositorio:** `certificacion-repos-fuentes.md`, `certificacion-evidencias.json` y `certificacion-reproducciones.json` (§2, §29). Las condiciones de las fuentes se verificaron de nuevo en vivo (sección 3). Los hallazgos sobre repositorios de terceros se toman del resumen de §4.

## 2. Mapa de integración sobre la arquitectura real

| Pieza pedida | Se reutiliza | Se agrega |
|---|---|---|
| Registro de fuentes (SRC-01..06) | `sources`, `source_policies`, `coverage_scopes` y activación con perfil de uso | Columnas **aditivas** de estados independientes (aprobación, salud y cobertura), `capabilities`, `shadow_mode` y `source_schema_versions` con huella de esquema |
| Ejecuciones y checkpoints (ING-01..07) | `ingestion_runs`, `ingest_commit_cursor`, `jobs` con lease y generación, y `IngestionRunner` | Corrección del runner: el límite de páginas da `partial` con continuación. Corte congelado en el cursor. Límites de filas, solicitudes y minutos |
| Observaciones inmutables (EVI-04) | `source_records`, `source_snapshots`, `observations` idempotentes por hash, y originales por hash | — |
| Documentos y evidencia (EVI-01..05) | `documents`, `document_revisions`, `extraction_runs`, `document_pages`, `evidence`, OCR (`pdf_text`) y `SafeFetcher` anti-SSRF | Carga asistida con lista de dominios oficiales |
| Actores y territorios (DAT-01..06) | `persons`, `parties`, `party_memberships` y `organizations` (legislativo) | `territories` (DIVIPOLA), `actors` con enlace opcional a `persons`/`parties`, `actor_identifiers` (seudónimo HMAC y valor enmascarado), `positions` y `affiliations` |
| Expedientes, casos y afirmaciones (CAS, REL, EVI-07) | `review_cases` e `identity_candidates` como cola de revisión | `proceedings`, `proceeding_events`, `participations`, `cases`, `case_revisions`, `case_proceedings`, `case_contracts`, `claims`, `claim_evidence` y `editorial_reviews` |
| Contratación (DAT-06, 11.1) | Observaciones de SECOP | `contracts` y `contract_versions` inmutables con `numeric` y signo |
| Seguimientos (SUB-01..07) | `telegram_identities` (seudónimo) y `delivery_attempts` como modelo | `subscriptions`, `outbox_events` (creado en la misma transacción que la publicación) y `notification_deliveries` |
| Comandos del panel (ADM-03) | `audit_log` de solo inserción y roles en `app_roles` | `admin_commands` idempotentes y roles `editor` y `auditor` |
| Banderas (§18) | — | `feature_flags` y `app_settings` (entorno development o production) |
| Telegram (§13) | Interfaz aiogram 3 + Redis (DEC-18): intenciones, `ViewModel`, tokens, CAS, ordinales y paginación | Intenciones y vistas de investigaciones, casos, entidades y territorios, y seguimientos, detrás de banderas |
| Panel (§15) | BFF FastAPI, sesión OTP, SSE y tablero | Vista «Investigaciones» y acciones editoriales POST con CSRF, verificadas en el servidor por la RPC |
| Colas (ARC-03) | `jobs` en InsForge y temporizadores systemd | Temporizador del digest de seguimientos (18:00 de Bogotá) dentro del proceso del bot |

No se sustituye el framework del bot, la cola, la autenticación ni la base (§1). El bot sigue siendo el único consumidor del token.

## 3. Fuentes verificadas en vivo (2026-09-29)

Todas las fuentes de datos.gov.co publican licencia **CC BY-SA 4.0**: permite reutilizar citando y compartiendo igual. Se registran en el perfil de uso (SEG-07).

| Fuente | Id | Columnas | Datos sensibles | Decisión |
|---|---|---|---|---|
| SECOP II contratos | `jbjy-vk9h` | 95 | Documento del proveedor y del representante legal | Piloto en **modo sombra**. Documentos de personas naturales solo como HMAC y valor enmascarado |
| SECOP II procesos | `p6dx-8zbt` | 59 | Proveedor adjudicado | Mapeo verificado contra los metadatos antes de cargar (T-08) |
| SECOP I | `f789-7hwg` | 79 | Contratista y representante | Modo sombra. Sin heredar filtros de «adjudicado» (11.1) |
| SIRI | `iaeu-rcn6` | 24 | **Cédula, nombres y sanción** de personas | Modo sombra. Participaciones como afirmaciones `pending_review`: nunca se publican sin revisión (EVI-10) |
| Relatoría PGN | `rhun-uf37` | 7 | — | Índice documental; se registran documentos por URL, sin descargar |
| DIVIPOLA municipios | `gdxc-w37w` | 1.122 filas | — | Territorios oficiales (DAT-03) |

Tipos observados:
- Los importes llegan como texto numérico y se guardan en `numeric`.
- `nit_entidad` es `number` en contratos y `text` en procesos (DAT-04): la identidad se guarda como texto, sin pasar por flotantes.
- La cédula de SIRI trae espacios de relleno.
- `fecha_efectos_juridicos` es texto con formato `dd/mm/aaaa`.

### Piloto de tres municipios (propuesta basada en datos, a confirmar editorialmente)

Criterio (§22, «por disponibilidad documental»): entre municipios medianos, con 800 a 6.000 contratos de SECOP II firmados desde 2025, los de más registros SIRI.

| Municipio | DIVIPOLA | Contratos SECOP II desde 2025 | Registros SIRI |
|---|---|---|---|
| Florencia (Caquetá) | 18001 | 3.629 | 441 |
| Buenaventura (Valle del Cauca) | 76109 | 4.995 | 440 |
| Arauca (Arauca) | 81001 | 4.443 | 321 |

Para probar homonimia (T-36) existen tres «San Pedro»: Antioquia, Sucre y Valle del Cauca.

## 4. Banderas y valores iniciales (§18)

`FEATURE_CASES=false`, `FEATURE_SUBSCRIPTIONS=false`, `ENABLE_CPNU_AUTOMATION=false`, `ENABLE_COMMERCIAL_PROVIDERS=false` y `PAID_MONTHLY_BUDGET=0`. El resto (concurrencia, tiempos, filas, páginas, zona horaria, digest) está en `app_settings` y en la configuración del conector. Las banderas viven en la base, cambian con auditoría y el bot las lee con caché breve.

## 5. Riesgos

- **Datos personales de SIRI y SECOP:** mitigado con seudónimo HMAC más valor enmascarado. Nunca se muestran documentos completos (SEG-02).
- **Confusión de roles** («sancionado» con «condenado»): diccionario versionado de roles y rol original conservado (REL-02).
- **Vigencia falsa:** «investigación activa» solo con estado vigente dentro del umbral de frescura. En otro caso se muestra «último estado conocido» (CAS-06).
- **Crecimiento de la base:** límites por ejecución (10.000 filas, 100 solicitudes, 30 minutos) y alcance del piloto.
