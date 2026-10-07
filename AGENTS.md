# AGENTS.md

Instrucciones para agentes de código que trabajan en botcentro (plataforma de información legislativa de
Colombia con bot de Telegram). Responder y documentar en español.

## Backend: PostgreSQL autoalojado (DEC-21)

Desde el 2026-09-30 la base de datos es **PostgreSQL 16 + pgvector en Docker en este servidor**
(`botcentro-postgres`, `127.0.0.1:5434`). **InsForge ya no se usa**: no instalar ni invocar sus skills, SDK
ni CLI para este proyecto. Detalle en [docs/plataforma-postgres.md](docs/plataforma-postgres.md).

- **Plataforma:** `ops/sql/plataforma.sql` (roles `anon`, `authenticated`, `project_admin`, `botcentro_app`;
  `auth.users`, `auth.uid()`; extensiones). Las pruebas usan la misma plataforma.
- **Acceso desde el código:** `botcentro.db.pgclient.PgClient`. Cada llamada hace `SET LOCAL ROLE
  authenticated` con los claims del usuario; las funciones SQL verifican el rol de aplicación
  (`require_app_role`) y RLS. La aplicación conecta como `botcentro_app`, que no tiene privilegios propios.
- **Migraciones:** archivos `migrations/AAAAMMDDhhmmss_nombre.sql`, aplicados con
  `.venv/bin/python -m botcentro.cli migrate`. Cada uno corre en su transacción como `project_admin`, sin
  `BEGIN`/`COMMIT`. **Una migración aplicada no se edita** (el ejecutor compara su suma SHA-256): se crea otra.
- **Nada de borrados masivos dentro de una migración.** Se hacen por operación, con índices en las claves
  foráneas antes de borrar.
- **Variables:** `.env` (nunca se versiona). `BOTCENTRO_DATABASE_URL` (aplicación) y
  `BOTCENTRO_DATABASE_ADMIN_URL` (solo operación).

## Pruebas

```bash
.venv/bin/pytest tests/unit
BOTCENTRO_TEST_PG_HOST=127.0.0.1 BOTCENTRO_TEST_PG_PORT=54330 BOTCENTRO_TEST_PG_USER=postgres \
  BOTCENTRO_TEST_REDIS_URL=redis://127.0.0.1:63790/0 .venv/bin/pytest
```

La base de pruebas es el contenedor aislado `botcentro-pg-test` (127.0.0.1:54330, en memoria) y el Redis de
pruebas `botcentro-redis-test` (127.0.0.1:63790). Sin `BOTCENTRO_TEST_REDIS_URL` las pruebas del bot de punta a
punta se omiten. Nunca correr pruebas contra la base ni el Redis de producción. Probar con el rol real `botcentro_app` cuando el código dependa de
permisos (un test como superusuario ocultó una falta de permisos).

## Servicios en este servidor

| Servicio | Qué hace |
|---|---|
| `botcentro-bot` | Bot de Telegram (aiogram, sondeo largo, sin IA). Único consumidor del token |
| `botcentro-panel` | Panel FastAPI + React; acceso con código por Telegram |
| `botcentro-actualizar.timer` | Actualización diaria 05:30 (Senado, Cámara, SECOP, Relatoría, planes, actas) |
| `botcentro-videos.timer` | 5 veces al día: agenda y votos del Senado, videos oficiales de sesiones |
| `botcentro-gacetas` | Carga continua de gacetas (OCR → texto, actas, Qdrant), reanudable |
| `botcentro-respaldo.timer` | `pg_dump` diario, 14 copias |
| Contenedores | `botcentro-postgres`, `botcentro-qdrant` (vectores), `botcentro-redis` (sesiones del bot) |

## Reglas del proyecto

- **Fuentes:** decisiones en [docs/decisiones.md](docs/decisiones.md). No eludir controles anti-bot ni de
  acceso de terceros (SRC-02 Senado, YouTube): usar fuentes oficiales o pedir autorización.
- **Datos personales:** documentos de personas solo como HMAC y enmascarados; listas del bot sin nombres de
  personas naturales (DEC-22). Nunca secretos en el repositorio, registros o callbacks.
- **PDF:** no se conservan (DEC-11); solo texto derivado, hash y enlace oficial.
- **Publicación:** nada sensible se publica sin revisión editorial; fuentes nuevas entran en modo sombra.
- **Commits:** solo con autorización del responsable; rama de trabajo `feat/fundaciones-insforge`.
