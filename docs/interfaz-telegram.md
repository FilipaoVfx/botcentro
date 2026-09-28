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
| I2 — Proyectos (explorador, filtros, ficha, contexto) | Pendiente |
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
