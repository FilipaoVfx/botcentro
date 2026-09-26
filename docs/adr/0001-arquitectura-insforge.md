# ADR 0001 — Arquitectura sobre InsForge y servicios Python

| Campo | Valor |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-09-26 |
| Relacionada con | SRS §3.2, §4, §7.3, §12; PRD DEC-04, DEC-05, DEC-06 |

## Contexto

El SRS propone PostgreSQL administrado con Supabase (§3.2, DEC-04). El responsable del proyecto decidió usar **InsForge** (proyecto `botcentro`, región us-east), que ofrece PostgreSQL 15 con pgvector, PostgREST, autenticación, almacenamiento, funciones, cómputo y tareas programadas. InsForge impone restricciones que condicionan el diseño:

- Todo objeto de aplicación vive en el esquema `public`; no se crean esquemas propios.
- `anon` y `authenticated` reciben por defecto `SELECT/INSERT/UPDATE/DELETE` sobre cada tabla nueva de `public`.
- Las migraciones se ejecutan como `project_admin` (BYPASSRLS) y **no pueden cambiar la configuración de sesión** (`set_config`, `SET LOCAL`).
- No hay cadena de conexión directa a Postgres para aplicaciones: el acceso es REST (`/api/database/records`) y RPC (`/api/database/rpc/{fn}`).
- La API key del proyecto es una credencial administrativa completa.

## Decisiones

1. **Servicios en Python sobre REST/RPC.** La adquisición, el OCR y la extracción documental se benefician del ecosistema Python. Los servicios (API y webhook, workers) se despliegan con `insforge compute` y hablan con la base solo por HTTP (`botcentro.insforge.client`).

2. **Cuentas de servicio en lugar de API key (SRS-N01).** `ingest_service` y `query_service` son usuarios de InsForge Auth con su rol en `public.app_roles`, igual que el personal (`reviewer`, `operator`, `admin`). Los administradores del proyecto en InsForge (`auth.users.is_project_admin`) cuentan como `admin`. La API key queda para migraciones y soporte.

3. **Denegar por defecto.** Todas las tablas tienen RLS. La migración de control de acceso revoca todo a `anon` y `authenticated` y concede operación por operación según el rol (`has_app_role`). `anon` no tiene ningún privilegio: el MVP no tiene superficie pública.

4. **Escrituras sensibles mediante RPC `SECURITY DEFINER`.** Las capturas, observaciones, evidencia, cola, presupuesto, recepción de Telegram, auditoría y decisiones administrativas se escriben solo mediante funciones que:
   - empiezan con `require_app_role(...)`;
   - fijan `search_path = pg_catalog, public, pg_temp`;
   - no son ejecutables por `anon` ni por `PUBLIC`.

   Por eso las tablas append-only no necesitan permisos de escritura directos. Además, `write_audit` no es ejecutable por nadie más que el propietario, así que la auditoría no se puede falsificar.

   *Consecuencia conocida:* el advisor de InsForge marca estas funciones con la regla `dangerous-function` porque `authenticated` puede invocarlas. Es intencional; suprimirlas o no es decisión del responsable del proyecto (ver «Pendientes»).

5. **Cola durable en PostgreSQL** (`public.jobs`, SRS §7.3). Los leases con expiración, el heartbeat, el fencing (solo el dueño del lease completa o falla) y la dead-letter se implementan con `FOR UPDATE SKIP LOCKED`. Al encolar en la misma transacción que publica los datos, la cola cumple la función del *outbox* transaccional exigido por SRS §7.3. Se puede sustituir por otra cola sin cambiar los handlers.

6. **Idempotencia en la base.** `observation_key` (hash canónico de fuente, sujeto, predicado, valor y fecha, con instantes en UTC) y `evidence_key` se calculan en SQL, de modo que la reingesta nunca duplica (T-02). Las capturas son únicas por `(registro, hash)`; volver a unos bytes ya vistos se registra como `reverted`.

7. **Vocabularios como `text + CHECK`.** Evolucionan con migraciones revisables y son más simples de extender que los tipos `enum`.

8. **Embeddings con dimensión variable por modelo** (`chunk_embeddings.embedding vector` más `dimensions`). El índice HNSW se creará como índice parcial por modelo cuando se decida DEC-06; hasta entonces la búsqueda vectorial es exacta.

9. **Inmutabilidad sin escape por sesión.** Como InsForge prohíbe cambiar la configuración de sesión en migraciones, las tablas append-only son inmutables sin excepciones. La retención de `audit_log` (365 días, SRS-N05) se codifica en su guardián, que solo admite borrar filas vencidas.

10. **Pruebas contra un clon local del entorno.** `tests/integration/insforge_shim.sql` reproduce roles, `auth.jwt()/auth.uid()` y privilegios por defecto. `PsycopgRpc` emula una petición PostgREST (`SET ROLE authenticated` más claims JWT). Las migraciones se prueban completas antes de aplicarlas con `npx -y @insforge/cli db migrations up`.

## Pendientes

- Decidir si se suprimen los hallazgos `dangerous-function` como `accepted_risk` o se migra alguna RPC a `SECURITY INVOKER` con políticas por fila.
- DEC-05/DEC-06: modelo de embeddings y benchmark de pgvector antes de crear el índice ANN.
- Adaptador de almacenamiento sobre buckets privados de InsForge (hoy existe `LocalObjectStore` para desarrollo).
