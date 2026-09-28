# Interfaz conversacional de Telegram — avance y trazabilidad

La especificación está en [`centrorequirement.md`](../centrorequirement.md). Las decisiones que la rigen son DEC-18 (aiogram 3, Redis local y prueba de aiogram-dialog antes de adoptarlo) y DEC-16 (respuestas sin IA).

## Arquitectura

```
Telegram ⇄ aiogram 3 (sondeo largo, único consumidor)
             │ update crudo
             ▼
          UiRuntime ── texto ──► telegram_accept_update → cola → UiApplication → query_finish → envío → delivery_mark
             │
             └── clic ──► acuse inmediato → deduplicación → token → dueño → autorización (caché) → UiApplication → edición
                                   ▲                                                                              │
                                Redis: sesión con CAS, listas mostradas, tokens de botón, caché de autorización ◄──┘
```

Módulos en `src/botcentro/telegram_ui/`:

| Módulo | Contenido |
|---|---|
| `contracts.py` | `UiAction`, `ViewModel`, `SessionContext`, `ResultSet`, `CallbackRecord` |
| `intents.py` | Texto a intención |
| `state.py` | Redis |
| `app.py` | Casos de uso y vistas |
| `runtime.py` | Orquestación |
| `aiogram_adapter.py` | Transporte |

## Estado por etapa

| Etapa | Estado |
|---|---|
| I0 — Inventario | ✅ Revisión de `centrorequirement.md` y DEC-18 |
| I1 — Contratos, contexto y callbacks | ✅ Esta entrega |
| I2 — Proyectos (explorador, filtros, ficha, contexto) | ✅ |
| I3 — Portada del día, agenda, cronología y debates | ✅ |
| I4 — Votaciones, documentos y fuentes por respuesta | ✅ |
| I5 — Robustez, observabilidad y corpus de 240 entradas | ✅ (salvo prueba con usuarios) |

## Requisitos cubiertos en I1

| Requisito | Evidencia |
|---|---|
| UI-F01 Entradas equivalentes | `test_ui_intents.py::test_equivalent_entries` |
| UI-F02 Interpretación completa | `test_keywords_inside_sentences_do_not_trigger_navigation` |
| UI-F04 Inicio controlado | `test_repeated_start_keeps_navigation_bounded` |
| UI-F05 Menús según capacidad | `test_start_opens_home_with_only_available_sections`. Lo pendiente se explica por texto; la búsqueda pendiente pasa al motor de preguntas, sin bucle |
| UI-F23 Volver, inicio y cancelar | `test_button_edits_the_same_message_and_back_returns` |
| UI-F26 Callback protegido | `test_callback_payload_is_short_and_opaque` y `test_foreign_and_forged_callbacks_are_rejected` |
| UI-F27 Deduplicación | `test_duplicate_update_is_answered_once` y `test_double_click_has_one_effect` |
| UI-F28 Carreras controladas | `test_stale_render_does_not_overwrite_newer_view` y `test_session_compare_and_swap` |
| UI-F29 Mensajes dinámicos | Edición del mensaje ancla; «not modified» se trata como no-op (`test_aiogram_adapter.py`) |
| UI-F35 Límites y entrega | Reintento ante 429, HTML rechazado reenviado como texto plano, bloqueo registrado sin reintentos |
| UI-F36 Observabilidad (parcial) | Eventos JSON `ui.*` en el log del servicio, con usuario seudonimizado y sin texto. Aún no llegan al panel |

La cobertura de datos por caso de uso está en la sección «Brecha de datos» de la revisión (DEC-18). Debates, «Cámara hoy» y ponentes son P0 con cobertura limitada.

## Requisitos cubiertos en I2

| Requisito | Evidencia |
|---|---|
| UI-F06 Actividad reciente | `bot_projects_page`: orden por la última radicación o votación. Las fechas imposibles de la fuente se descartan (`valid_legislative_date`). `test_projects_list_orders_by_legislative_activity_and_opens_card` |
| UI-F07 Búsqueda y filtros | Corporación, periodo y tipo se aplican de inmediato; «Limpiar» y ampliar el periodo cuando no hay resultados. Si el título no coincide, la búsqueda pasa a buscar por significado en fichas y gacetas. `test_filters_apply_immediately…` y `test_search_by_title_words…` |
| UI-F08 Paginación estable | Se materializan hasta 200 IDs por lista en Redis (30 min); las páginas no repiten la consulta |
| UI-F09 Ordinales | `test_ordinal_opens_item_of_the_page_shown` y `test_ordinal_without_list_asks_instead_of_guessing` |
| UI-F10 Ficha | Botones de Autores, Votaciones, Documentos, Resultados e Inicio |
| UI-F11 Contexto de proyecto | `test_contextual_question_uses_open_project` |
| UI-F12 Participantes (limitado) | Autores con partido cuando la fuente lo trae. **Ponentes**: se declara que aún no están disponibles |
| UI-F19/F20 (por proyecto) | Votaciones y gacetas del proyecto; sin datos, se explica la cobertura |

## Pendientes conocidos de I2

- **Latencia de la lista:** ~2 s con datos reales, en el límite de UI-O05. Hay que materializar la actividad por proyecto.
- **Filtros sin tema ni estado:** falta un catálogo temático; el estado solo existe para la Cámara.

## Requisitos cubiertos en I3

| Requisito | Evidencia |
|---|---|
| UI-F13 Cronología | `bot_project_timeline` suma radicaciones, votaciones y agenda, más las gacetas publicadas desde Qdrant. Los hechos del mismo día no tienen orden horario, y el estado sin fecha va aparte. `test_timeline_lists_dated_facts…` |
| UI-F14 Jornada | `bot_day_overview` separa lo **Confirmado**, lo **Programado** y lo **Publicado**. `test_day_overview_separates…` |
| UI-F15 Fechas | Relativas en hora de Colombia y reinterpretadas al «Actualizar»; los botones de día llevan fecha explícita; las fechas futuras no tienen hechos. `test_day_navigation…` |
| UI-F16 Agenda (limitado) | Navegación por semanas. «Hora no publicada» cuando la fuente no la trae. Sin estados de aplazamiento: la fuente no los publica |
| UI-F17 Debates (P0 limitado) | Explica que la cobertura es limitada y busca solo en actas de gacetas, advirtiendo que un acta puede ser parcial. `test_debates_have_limited_coverage…` |
| UI-F18 / UI-T25 | La conversación pública se declara no habilitada |

Pendientes de I3:
- **«Cámara hoy»:** solo trae radicaciones, porque no hay fuente de agenda ni de votos de la Cámara (SRC-07).
- **Días de la semana:** «el martes» se resuelve a la próxima ocurrencia y se muestra la fecha explícita. No se ofrecen opciones para elegir.

## Requisitos cubiertos en I4

| Requisito | Evidencia |
|---|---|
| UI-F19 Votaciones | Explorador general por periodo o persona, y por proyecto desde su ficha. El detalle muestra el acto, el órgano y la fecha. Los totales se **calculan del registro nominal** y se rotulan así, porque la fuente no publica un resultado oficial. Una persona sin fila en el acto es «falta de dato» (UI-T28). Pruebas en `test_bot_votings.py` |
| UI-F22 Evidencias | «🔗 Fuentes» muestra las **fuentes de esta respuesta**: los enlaces citados más la captura de origen con su fecha. Sin respuesta previa muestra el **catálogo**. `test_sources_of_this_answer_differ_from_catalog` |
| UI-F09 Ordinales en votaciones | `test_ordinal_on_votings_list_opens_that_act` |
| UI-F20 Documentos (por proyecto) | Gacetas enlazadas al proyecto, con tipo de pieza y páginas (I2) |

Pendientes de I4:
- **Explorador general de documentos:** todavía no existe; por ahora los documentos se ven solo por proyecto.
- **Votaciones de la Cámara:** sin fuente (SRC-07).
- **Distinguir abstención, impedimento y ausencia:** la fuente del Senado no publica esas categorías.

## Requisitos cubiertos en I5

| Requisito | Evidencia |
|---|---|
| UX-O03 Continuidad (corpus) | `tests/corpus/ui_corpus.jsonl` tiene 240 entradas: 60 de comandos, 60 de filtros, 60 de seguimiento y 60 adversariales. Resultado: 60/60 en ajuste y **180/180 en evaluación**, y el grupo adversarial exige 100 % (`test_ui_corpus.py`) |
| UX-O05 Render ≤ 2 s | La lista de proyectos pasó de ~2 s a **0,04–0,09 s** con la vista materializada `project_activity`, que se refresca al normalizar |
| UI-F25 Contexto persistente breve | `test_context_survives_process_restart` y `test_expired_context_is_not_revived` |
| UI-F26 Token vencido | `test_expired_button_reopens_home_without_data` |
| UI-F36 Observabilidad | Eventos `ui.*` como contadores diarios y latencias p95 por tipo de entrada en Redis, sin texto ni identificadores. El panel los muestra en la etapa **Bot** (`test_interaction_metrics_reach_the_panel`) |
| Panel: índice real | La etapa **Índice** cuenta vectores en Qdrant por tipo; antes contaba `chunk_embeddings` en InsForge, vacía desde DEC-12, y mostraba 0. La etapa nueva **Gacetas** muestra el avance de la carga y detecta cuando se detiene (`test_panel_live_stats.py`) |

## Pendiente fuera de lo automatizable

- **UI-N06:** prueba de usabilidad móvil con 10 participantes y 5 tareas (UX-O01, UX-O02). La recluta y la moderación las hace el equipo.
- **UI-N02:** carga con dos réplicas. Hoy hay un solo proceso; el diseño (CAS y Redis compartido) ya lo admite.
