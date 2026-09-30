# PostgreSQL autoalojado (DEC-21)

## Componentes

| Pieza | Dónde | Notas |
|---|---|---|
| PostgreSQL 16 + pgvector 0.8.1 | contenedor `botcentro-postgres`, `127.0.0.1:5434` | Datos en `/var/lib/botcentro/postgres`; límite de 1,5 GB de RAM |
| Plataforma | `ops/sql/plataforma.sql` | Roles `anon`, `authenticated`, `project_admin` y `botcentro_app`; `auth.users`; `auth.uid()`; `schema_migrations`. Es la misma plataforma que usan las pruebas |
| Migraciones | `migrations/*.sql` | `python -m botcentro.cli migrate`. Cada archivo corre en su propia transacción como `project_admin`. Un archivo ya aplicado no se edita: se crea uno nuevo |
| Cliente | `botcentro.db.pgclient.PgClient` | Por llamada: `SET LOCAL ROLE authenticated` y los claims del usuario, de modo que `require_app_role` y RLS deciden igual que antes |
| Cuentas de servicio | `BOTCENTRO_INGEST_EMAIL` y `BOTCENTRO_QUERY_EMAIL` | Resueltas con `service_user_id()`; solo resuelve usuarios con rol `ingest_service` o `query_service` |
| Panel | `botcentro.panel.local_auth.LocalAuth` | Código de 6 dígitos por Telegram al chat del operador (`BOTCENTRO_PANEL_OPERATORS=correo=chat_id`) |
| Respaldo | `ops/postgres.sh backup` y el temporizador `botcentro-respaldo` (03:30 Bogotá) | Conserva 14 copias en `/var/lib/botcentro/backups` |

## Variables de `.env`

| Variable | Uso |
|---|---|
| `BOTCENTRO_DATABASE_URL` | Rol `botcentro_app`. La usan el bot, el panel y la CLI. Si está definida, reemplaza a InsForge |
| `BOTCENTRO_DATABASE_ADMIN_URL` | Superusuario. Solo para la operación: bootstrap, migraciones, restauración |
| `BOTCENTRO_PG_SUPERPASSWORD` y `BOTCENTRO_PG_APP_PASSWORD` | Contraseñas generadas en el servidor |
| `BOTCENTRO_PANEL_OPERATORS` | Operadores del panel y su chat de Telegram |

## Operación

```bash
ops/postgres.sh up | status | psql | backup
.venv/bin/python -m botcentro.cli db-bootstrap   # idempotente
.venv/bin/python -m botcentro.cli migrate
```

**Restaurar un respaldo:**

1. Crear una base vacía.
2. `docker exec -i botcentro-postgres pg_restore -U postgres -d <base> < archivo.dump`.
3. Verificar conteos y `select * from public.schema_migrations order by version desc limit 3`.

**Copia fuera del servidor (pendiente):** los respaldos viven en el mismo disco. Hay que copiarlos periódicamente a otro lugar (por ejemplo Google Drive o un disco externo).

## Migración desde InsForge

1. Crear un respaldo en InsForge (`npx -y @insforge/cli backups create`), descargarlo y restaurarlo completo en la base auxiliar `insforge_raw`.
2. En la base `botcentro`, que ya tiene la plataforma y las migraciones, ejecutar `python -m botcentro.db.import_insforge`. Copia cada tabla de `public` y `auth.users` por columnas comunes, compara conteos y ajusta secuencias.
3. Aplicar la limpieza de SIRI (DEC-20) y volver a normalizar.
4. Cambio: definir `BOTCENTRO_DATABASE_URL` y reiniciar `botcentro-bot`, `botcentro-panel` y el temporizador de actualización.
