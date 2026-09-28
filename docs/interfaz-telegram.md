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
| I3 — Portada del día, agenda y cronología | Pendiente |
| I4 — Votaciones, documentos y fuentes por respuesta | Pendiente |
| I5 — Robustez, observabilidad y corpus de 240 entradas | Pendiente |

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
