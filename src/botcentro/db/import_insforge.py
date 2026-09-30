"""Importa los datos del respaldo de InsForge al PostgreSQL autoalojado (DEC-21).

El respaldo se restaura antes, completo, en una base auxiliar (`insforge_raw`). Este módulo copia
cada tabla de `public` que exista en ambas bases, más `auth.users`, usando solo las columnas
comunes: el esquema de destino es el de nuestras migraciones, no el de la plataforma de InsForge.
La carga corre con `session_replication_role = replica` (sin triggers ni verificación de claves,
porque el origen ya era consistente); después se ajustan secuencias, se refrescan las vistas
materializadas y se comparan los conteos por tabla.

    python -m botcentro.db.import_insforge            # usa BOTCENTRO_DATABASE_ADMIN_URL
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

SKIP = {("public", "schema_migrations")}


def _with_db(conninfo: str, dbname: str) -> str:
    parts = urlsplit(conninfo)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def _tables(conn: psycopg.Connection) -> dict[tuple[str, str], list[str]]:
    rows = conn.execute("""
        select c.table_schema, c.table_name, array_agg(c.column_name::text order by c.ordinal_position)
          from information_schema.columns c
          join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name
         where t.table_type = 'BASE TABLE'
           and (c.table_schema = 'public' or (c.table_schema = 'auth' and c.table_name = 'users'))
           and c.is_generated = 'NEVER'
         group by 1, 2""").fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def import_all(target: str, source: str, *, log: Callable[[str], None] = print) -> dict[str, tuple[int, int]]:
    report: dict[str, tuple[int, int]] = {}
    with psycopg.connect(source) as src, psycopg.connect(target) as dst:
        src_tables, dst_tables = _tables(src), _tables(dst)
        common = sorted(set(src_tables) & set(dst_tables) - SKIP)
        missing = sorted(set(dst_tables) - set(src_tables) - SKIP)
        if missing:
            log(f"sin datos de origen (tablas nuevas): {', '.join(f'{s}.{t}' for s, t in missing)}")
        with dst.transaction():
            dst.execute("set local session_replication_role = replica")
            # Todo el destino se vacía primero y de una vez: un TRUNCATE ... CASCADE por tabla vaciaría
            # tablas hijas ya copiadas (p. ej. `sources` arrastra a `observations`).
            dst.execute(sql.SQL("truncate {} cascade").format(
                sql.SQL(", ").join(sql.Identifier(sch, tbl) for sch, tbl in common)))
            for schema, table in common:
                cols = [c for c in dst_tables[(schema, table)] if c in set(src_tables[(schema, table)])]
                ident = sql.Identifier(schema, table)
                col_list = sql.SQL(", ").join(map(sql.Identifier, cols))
                with src.cursor().copy(sql.SQL("copy (select {} from {}) to stdout (format binary)").format(col_list, ident)) as out, \
                        dst.cursor().copy(sql.SQL("copy {} ({}) from stdin (format binary)").format(ident, col_list)) as inp:
                    for chunk in out:
                        inp.write(chunk)
            # Conteos al final, sobre todas las tablas: detecta cualquier pérdida posterior a la copia.
            for schema, table in common:
                ident = sql.Identifier(schema, table)
                copied = dst.execute(sql.SQL("select count(*) from {}").format(ident)).fetchone()[0]
                expected = src.execute(sql.SQL("select count(*) from {}").format(ident)).fetchone()[0]
                report[f"{schema}.{table}"] = (expected, copied)
                if copied != expected:
                    raise RuntimeError(f"{schema}.{table}: origen {expected}, copiadas {copied}")
            # Secuencias de columnas serial/identity al máximo copiado.
            for seq_schema, seq, tbl_schema, tbl, col in dst.execute("""
                    select sn.nspname, s.relname, tn.nspname, t.relname, a.attname
                      from pg_depend d
                      join pg_class s on s.oid = d.objid and s.relkind = 'S'
                      join pg_namespace sn on sn.oid = s.relnamespace
                      join pg_class t on t.oid = d.refobjid
                      join pg_namespace tn on tn.oid = t.relnamespace
                      join pg_attribute a on a.attrelid = t.oid and a.attnum = d.refobjsubid
                     where tn.nspname in ('public', 'auth')""").fetchall():
                dst.execute(sql.SQL("select setval({}, coalesce((select max({}) from {}), 0) + 1, false)").format(
                    sql.Literal(f"{seq_schema}.{seq}"), sql.Identifier(col), sql.Identifier(tbl_schema, tbl)))
        for (view,) in dst.execute("select format('%I.%I', schemaname, matviewname) from pg_matviews "
                                   "where schemaname = 'public'").fetchall():
            dst.execute(f"refresh materialized view {view}")
            dst.commit()
    for name, (expected, copied) in report.items():
        if expected:
            log(f"{name}: {copied}")
    return report


def main() -> None:
    admin = os.environ.get("BOTCENTRO_DATABASE_ADMIN_URL")
    if not admin:
        sys.exit("falta BOTCENTRO_DATABASE_ADMIN_URL")
    import_all(admin, _with_db(admin, os.environ.get("BOTCENTRO_IMPORT_SOURCE_DB", "insforge_raw")))


if __name__ == "__main__":
    from botcentro.cli import load_env

    load_env()
    main()
