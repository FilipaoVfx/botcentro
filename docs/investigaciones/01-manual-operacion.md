# Investigaciones · manual de operación

Procedimientos para operar el módulo (investigaciones §25). Todos los comandos se ejecutan desde la raíz del repositorio con `.env` cargado. Los cambios de esquema van siempre por migración (`npx -y @insforge/cli db migrations new|up`); nunca se editan migraciones aplicadas.

## 1. Estado y banderas

| Bandera | Efecto | Predeterminado |
|---|---|---|
| `FEATURE_CASES` | Muestra en Telegram Investigaciones, Grandes casos y Entidades y territorios | apagada |
| `FEATURE_SUBSCRIPTIONS` | Habilita Seguir y Mis seguimientos, y el resumen diario | apagada |
| `ENABLE_CPNU_AUTOMATION` | Reservada; CPNU no está certificada | apagada |
| `ENABLE_COMMERCIAL_PROVIDERS` | Reservada; el presupuesto de pago es 0 | apagada |

Se cambian en el panel (**Investigaciones → Banderas**) con motivo obligatorio; queda en auditoría. Apagar una bandera oculta el menú sin borrar contenido (rollback, T-57). El bot relee las banderas cada 60 s.

**Antes de encender `FEATURE_CASES`:** debe haber al menos un caso publicado por un revisor distinto de su autor. Con la bandera encendida y sin casos, el bot muestra la lista vacía con su explicación de cobertura; no hay datos de ejemplo.

## 2. Fuentes

| Acción | Cómo |
|---|---|
| Cargar en sombra | `python -m botcentro.cli ingest SRC-15 --from 2026-01-01 --filters '{"territories":[{"departamento":"Caquetá","municipio":"Florencia"}]}'` |
| Normalizar | `python -m botcentro.cli normalize SRC-15` (lotes de 60; cada paso marca sus observaciones y termina) |
| Pausar | `update sources set state = 'paused', state_reason = '…' where code = 'SRC-xx'` por migración o desde el panel de fuentes |
| Publicar una fuente (salir de sombra) | Solo tras comparar una muestra revisada manualmente (§24.6): `shadow_mode = false`, `approval_state = 'approved'`, con registro en `audit_log` |
| Esquema cambiado | La ingesta se detiene con `SCHEMA_INCOMPATIBLE` si falta un campo crítico; la huella queda en `source_schema_versions` y en el panel. Corregir el mapeo en `investigations/datasets.py` y volver a validar (T-06/T-08) |

Límites por ejecución (`--max-pages`, filas, solicitudes y minutos): al agotarse, la ejecución termina en **parcial** con `LIMIT_REACHED` y continúa desde el cursor en la siguiente. Una página fallida nunca se registra como éxito.

## 3. Recuperar una ejecución

1. Revisar el error en **Ingestas** (panel) o en `ingestion_runs.error_code`.
2. Volver a lanzar la misma orden de ingesta: el cursor guarda `:id` y el corte congelado, y las observaciones repetidas no crean versiones nuevas (T-14).
3. Si el contenido no cambió pero hay que reinterpretarlo (parser corregido), usar `--reparse`.

## 4. Revisión editorial

- **Cola:** Panel → Investigaciones → Cola editorial. Cada afirmación muestra su evidencia (URL oficial, página y fragmento).
- **Aprobar/Rechazar:** exige motivo. En producción, quien aprueba debe ser distinto del autor (EVI-10). Una versión desactualizada devuelve conflicto: recargar y revisar.
- **Registros SIRI:** entran como afirmaciones `pending_review` con origen *Ingesta*. Aprobar uno publica la participación «sancionado(a)» en ese expediente. SIRI solo registra sanciones: no es el universo de investigaciones.
- **Identidad:** dos documentos distintos siempre son dos actores. Si se detecta un error de identidad en algo publicado, se **retira** la afirmación (motivo obligatorio): se suprimen los avisos pendientes y quienes ya recibieron la versión errónea reciben una corrección (T-44/T-45).

## 5. Carga documental asistida

Panel → Investigaciones → Carga documental asistida. Se descarga desde el dominio oficial de la fuente (Corte Suprema, Contraloría o Fiscalía), se extrae el texto por página (nativo u OCR) y **no se guarda el PDF** (DEC-11). Las páginas con calidad dudosa quedan en cuarentena y no se pueden citar como evidencia. La misma URL con bytes nuevos crea una revisión nueva. Contraloría conserva el fallo TLS registrado: si la descarga falla por certificado, no se desactiva la verificación (ING-06).

## 6. Seguimientos y entregas

- El resumen sale a las 18:00 de Bogotá. Entre las 21:00 y las 08:00 no se envía nada, y las correcciones salen en la siguiente ventana permitida.
- **Estados de entrega:** `pending`, `sending`, `sent`, `failed`, `suppressed` y `unknown_delivery`.
- **Entrega ambigua (`unknown_delivery`):** Telegram pudo haber entregado el mensaje. No se reintenta automáticamente.
  1. Revisar en el chat del destinatario, si es posible.
  2. Si no llegó, marcar la entrega como `failed` y dejar que el próximo resumen la reprograme. Si llegó, marcarla como `sent`.
  3. Nunca reenviar en bloque.
- **Bot bloqueado:** la entrega queda como `failed/BOT_BLOCKED` y los seguimientos de esa identidad se desactivan.

## 7. Respaldo y restauración

InsForge gestiona los respaldos del plan. Para una prueba de restauración (T-56) en un Postgres local:

1. `pg_dump` de las tablas del módulo: `sources`, `observations`, `claims`, `claim_evidence`, `editorial_reviews`, `cases`, `case_revisions`, `outbox_events`, `notification_deliveries` y `subscriptions`.
2. Restaurar sobre una base con las migraciones aplicadas.
3. Verificar:
   - los conteos por tabla;
   - que `public_case` devuelve la misma revisión publicada;
   - que `outbox_events` conserva sus `dedupe_key`.

## 8. Incidente 2026-09-29: afirmaciones SIRI duplicadas

La primera versión del paso `siri` reprocesaba observaciones que compartían número SIRI y creó unas 318 mil afirmaciones `pending_review`, nunca publicadas, que llevaron la base a unos 877 MB. La migración `20260929212337_investigaciones-normalizacion-idempotente.sql` corrige el paso con marcas explícitas de observaciones normalizadas.

**Pendiente de autorización del responsable:** borrar por lotes las filas derivadas de SRC-18. Son afirmaciones con `origin = 'system_ingest'` y `value->>'source' = 'SRC-18'`, junto con sus actuaciones, participaciones, evidencias, revisiones y los expedientes `system = 'SIRI'`. Orden de borrado:

1. Primero las tablas dependientes, cada una en una llamada.
2. Luego los expedientes `system = 'SIRI'`.
3. Al final las afirmaciones.

Después de borrar:

1. Aplicar la migración.
2. Ejecutar `normalize SRC-18`, que crea unos 445 registros.
3. Recuperar espacio con `VACUUM`.
