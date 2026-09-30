"""Plataforma y migraciones en el PostgreSQL autoalojado (DEC-21).

* `bootstrap`: aplica `ops/sql/plataforma.sql` (roles, auth, extensiones) y fija la contraseña de
  `botcentro_app`. Idempotente.
* `migrate`: aplica en orden los archivos de `migrations/` que no estén en `schema_migrations`, cada
  uno en su transacción y como `project_admin` (igual que InsForge). Un archivo ya aplicado cuyo
  contenido cambió detiene el proceso: las migraciones aplicadas son inmutables.

Ambas usan la conexión de superusuario `BOTCENTRO_DATABASE_ADMIN_URL`, que solo usa la operación.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "migrations"
PLATFORM = ROOT / "ops" / "sql" / "plataforma.sql"


class MigrationError(RuntimeError):
    pass


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def bootstrap(admin_conninfo: str, app_password: str) -> None:
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        conn.execute(PLATFORM.read_text())
        conn.execute(sql.SQL("alter role botcentro_app with password {}").format(sql.Literal(app_password)))


def pending(admin_conninfo: str, directory: Path = MIGRATIONS) -> list[Path]:
    with psycopg.connect(admin_conninfo) as conn:
        applied = {r[0]: r[1] for r in conn.execute("select version, checksum from public.schema_migrations")}
    out = []
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if version in applied:
            if applied[version] != checksum(path.read_text()):
                raise MigrationError(f"{path.name} cambió después de aplicarse; crea una migración nueva")
            continue
        out.append(path)
    return out


def migrate(admin_conninfo: str, directory: Path = MIGRATIONS, *, record_only: bool = False,
            log: Callable[[str], None] = print) -> int:
    """Aplica las pendientes. `record_only` las registra sin ejecutarlas (base restaurada con ese esquema)."""
    done = 0
    for path in pending(admin_conninfo, directory):
        text = path.read_text()
        version, _, name = path.stem.partition("_")
        with psycopg.connect(admin_conninfo) as conn:
            with conn.transaction():
                if not record_only:
                    conn.execute("set local role project_admin")
                    conn.execute(text)
                    conn.execute("reset role")
                conn.execute("insert into public.schema_migrations (version, name, checksum) values (%s, %s, %s)",
                             (version, name, checksum(text)))
        log(f"{'registrada' if record_only else 'aplicada'} {path.name}")
        done += 1
    return done
