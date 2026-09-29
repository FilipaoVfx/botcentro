# Investigaciones · trazabilidad de pruebas T-01..T-60

Estado al 2026-09-29. **Automatizada** significa que la prueba corre en la suite (`pytest`; las de integración usan Postgres local con las migraciones reales).

- `I:` → `tests/integration/test_investigations.py`
- `U-ui:` → `tests/unit/test_investigations_ui.py`
- `U-soc:` → `tests/unit/test_investigations_socrata.py`

| Prueba | Estado | Evidencia |
|---|---|---|
| T-01 Todas las fuentes fallan | Automatizada | U-soc: `test_combined_status_never_hides_failures` |
| T-02 Una falla, otra responde | Automatizada | U-soc: `test_combined_status_never_hides_failures` |
| T-03 Vacío válido | Automatizada | U-soc: `test_combined_status_never_hides_failures` |
| T-04 `D'ANGELO` | Automatizada | U-soc: `test_soql_escaping_and_whitelist` |
| T-05 `-12.50` | Automatizada | U-soc: `test_money_identifier_and_dates`; I: `test_contracts_keep_sign_zeros_and_unsigned_contracts` |
| T-06 Campo crítico desaparece | Automatizada | U-soc: `test_schema_change_is_detected` |
| T-07 Campo opcional desaparece | Automatizada | U-soc: `test_schema_change_is_detected` (resultado `degraded` en `SchemaCheck`) |
| T-08 Campo inexistente en el mapeo | Automatizada | U-soc: `test_mappings_only_cite_published_fields` (metadatos reales versionados) |
| T-09 Página falla a mitad | Automatizada | I: `test_failed_page_is_not_success` |
| T-10 Caída entre persistir y confirmar | Parcial | Cola con lease e idempotencia (`tests/integration/test_jobs_and_budget.py`); falta una prueba de caída específica del conector Socrata |
| T-11 Cambios durante la paginación | Automatizada | I: `test_run_limits_end_partial_with_frozen_cutoff` (corte único por ejecución) |
| T-12 Máximo timestamp de BD | Cubierta por diseño | El cursor usa `:id` de la fuente y el corte congelado, no el máximo de la BD; sin prueba dedicada |
| T-13 Más resultados que el límite | Automatizada | I: `test_run_limits_end_partial_with_frozen_cutoff` |
| T-14 Observación repetida | Automatizada | I: `test_repeated_observation_creates_no_version_and_changes_do` |
| T-15 Misma URL, documento modificado | Automatizada | I: `test_same_url_new_bytes_is_a_new_version_and_quarantine_blocks_citation` |
| T-16 Registro ausente | Automatizada | I: `test_repeated_observation_creates_no_version_and_changes_do` |
| T-17 Contrato sin firma | Automatizada | U-soc: `test_scope_keeps_unsigned_contracts`; I: `test_contracts_keep_sign_zeros_and_unsigned_contracts` |
| T-18 ID cambia de tipo | Automatizada | U-soc: `test_money_identifier_and_dates` (rechaza float, conserva ceros) |
| T-19 Homónimos | Automatizada | I: `test_homonyms_are_two_actors_and_documents_are_never_stored` |
| T-20 Cambio de cargo o partido | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` (dos periodos) |
| T-21 Partido por afiliación | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors`; U-ui: `test_actor_relation_by_affiliation_is_labelled` |
| T-22 Testigo | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` |
| T-23 Sentencia recurrida | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` (`finality = recurrida`) |
| T-24 Ramas activas y archivadas | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors`; U-ui: `test_case_card_separates_branches_and_states_freshness` |
| T-25 Estado antiguo | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` (`ultimo_estado_conocido`) |
| T-26 Dos publicaciones de una actuación | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` (una actuación, dos evidencias) |
| T-27 Dos expedientes con la misma fecha | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` |
| T-28 OCR ambiguo | Automatizada | I: `test_same_url_new_bytes_is_a_new_version_and_quarantine_blocks_citation`; U-soc: `test_assisted_upload_keeps_no_pdf_and_reports_quarantine` |
| T-29 Fuentes contradictorias | Automatizada | I: `test_contradicting_evidence_is_kept` |
| T-30 Publicar sin evidencia | Automatizada | I: `test_claim_without_evidence_cannot_be_submitted` |
| T-31 Autor aprueba lo propio | Automatizada | I: `test_author_cannot_approve_own_sensitive_work_in_production` |
| T-32 Revisión obsoleta | Automatizada | I: `test_stale_revision_cannot_overwrite` |
| T-33..T-35 Proyectos, jornada y discusiones | Existentes | Corpus de 240 frases (`tests/unit/test_ui_corpus.py`) y `tests/integration/test_bot.py`, sin regresión |
| T-36 Municipio homónimo | Automatizada | I: `test_territories_and_homonyms`, `test_telegram_views_render_against_real_sql`; U-ui: `test_homonym_municipalities_ask_for_department` |
| T-37 Volver conserva la lista | Existente | `tests/integration/test_bot.py::test_button_edits_the_same_message_and_back_returns` |
| T-38 Botón vencido u ordinal sin lista | Existente | `tests/integration/test_bot.py` y `tests/unit/test_telegram.py` |
| T-39 Callback de otra persona | Existente | `tests/integration/test_bot.py::test_foreign_and_forged_callbacks_are_rejected` |
| T-40 Mensaje no editable | Existente | `tests/unit/test_aiogram_adapter.py` |
| T-41 Doble clic en Seguir | Automatizada | I: `test_subscriptions_digest_and_corrections`; U-ui: `test_follow_requires_confirmation_and_uses_principal` |
| T-42 Varios seguidores | Automatizada | I: `test_subscriptions_digest_and_corrections` |
| T-43 Cancelar antes del resumen | Automatizada | I: `test_subscriptions_digest_and_corrections` |
| T-44 Corrección antes de enviar | Automatizada | I: `test_subscriptions_digest_and_corrections` |
| T-45 Corrección después de enviar | Automatizada | I: `test_subscriptions_digest_and_corrections` |
| T-46 Timeout de Telegram | Automatizada | I: `test_subscriptions_digest_and_corrections`; U-ui: `test_worker_is_silent_at_night_and_marks_ambiguous_delivery` |
| T-47 Borrador visible | Automatizada | I: `test_draft_case_is_invisible_until_published`, `test_siri_records_are_pending_claims_never_published_automatically` |
| T-48 Suscripción ajena | Automatizada | I: `test_subscriptions_digest_and_corrections` |
| T-49 Redirección a red privada | Existente | `tests/unit/test_security_and_fetch.py` (DNS privado y redirecciones validadas) |
| T-50 Instrucción maliciosa en PDF | Cubierta por diseño | El texto extraído nunca se interpreta como instrucción: no hay LLM (DEC-16) y el texto solo se guarda y se cita escapado |
| T-51 Presupuesto cero | Automatizada | I: `test_zero_paid_budget_and_commercial_providers_disabled` |
| T-52 Dos reservas del último saldo | Existente | `tests/integration/test_jobs_and_budget.py::test_concurrent_reservations_never_exceed_limit` |
| T-53 Lease perdido | Existente | `tests/integration/test_jobs_and_budget.py::test_enqueue_is_idempotent_and_claim_leases` |
| T-54 Error TLS | Automatizada | U-soc: `test_tls_verification_is_always_on` |
| T-55 Comando repetido | Automatizada | I: `test_admin_command_idempotency`; `tests/unit/test_panel_bff.py::test_editorial_action_is_idempotent` |
| T-56 Restauración | Manual | Procedimiento en el manual §7; sin ejecución automatizada |
| T-57 Rollback de banderas | Automatizada | U-ui: `test_flags_off_hide_menu_and_explain` (el menú desaparece y el núcleo sigue) |
| T-58 Conteos con denominador | Automatizada | I: `test_publish_case_and_read_branches_timeline_and_actors` |
| T-59 «¿Es corrupto?» | Automatizada | U-ui: `test_sensitive_question_is_not_answered_with_a_label` |
| T-60 Rendimiento | Pendiente | No se ejecutó la carga de 100 mil contratos: el plan gratuito está sobre su cupo (manual §8) |

**Resumen:**

| Estado | Pruebas |
|---|---|
| Automatizadas nuevas | 45 |
| Cubiertas por pruebas existentes | 10 |
| Parciales o cubiertas por diseño | 3 (T-10, T-12, T-50) |
| Manual | 1 (T-56) |
| Pendiente | 1 (T-60) |
