"""Soporte de pruebas de integración contra un PostgreSQL local que replica InsForge.

PsycopgRpc emula una petición PostgREST: una transacción por llamada, `SET ROLE
authenticated`, `request.jwt.claims` con el sub del usuario, y parámetros nombrados con el
tipo exacto de cada argumento. Así las pruebas ejercitan las mismas funciones, grants y RLS
que en InsForge, sin HTTP.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from botcentro.insforge.client import error_from_sqlstate


def _adapt(value: Any, pg_type: str) -> Any:
    if value is None:
        return None
    if pg_type in ("jsonb", "json"):
        return Jsonb(value)
    if pg_type == "vector":
        return "[" + ",".join(str(float(v)) for v in value) + "]"
    return value


def _jsonable(value: Any) -> Any:
    """Convierte el resultado a lo que devolvería PostgREST (JSON)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class PsycopgRpc:
    def __init__(self, conninfo: str, user_id: UUID | None) -> None:
        self.conninfo = conninfo
        self.user_id = user_id
        self._signatures: dict[str, tuple[list[tuple[str, str]], bool, str]] = {}

    def _signature(self, conn: psycopg.Connection[Any], fn: str) -> tuple[list[tuple[str, str]], bool, str]:
        if fn not in self._signatures:
            row = conn.execute(
                """
                select p.proargnames, p.proargmodes,
                       array(select format_type(t, null) from unnest(p.proargtypes::oid[]) t) as in_types,
                       p.proretset, format_type(p.prorettype, null) as rettype,
                       (select typtype from pg_type where oid = p.prorettype) as typtype
                  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public' and p.proname = %s
                """,
                (fn,),
            ).fetchone()
            if row is None:
                raise LookupError(f"función inexistente: {fn}")
            names, modes, in_types = row["proargnames"], row["proargmodes"], row["in_types"]
            retset, rettype, typtype = row["proretset"], row["rettype"], row["typtype"]
            in_names = [n for n, m in zip(names or [], modes or ["i"] * len(names or [])) if m in ("i", "b")]
            args = list(zip(in_names, in_types))
            composite = retset or typtype == "c" or rettype == "record"
            self._signatures[fn] = (args, composite, rettype)
        return self._signatures[fn]

    def call(self, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        params = dict(params or {})
        with psycopg.connect(self.conninfo, row_factory=dict_row) as conn:
            args, returns_rows, rettype = self._signature(conn, fn)
            known = {name for name, _ in args}
            unknown = set(params) - known
            if unknown:
                raise TypeError(f"parámetros desconocidos para {fn}: {unknown}")
            parts = [
                sql.SQL("{} => {}::{}").format(sql.Identifier(name), sql.Placeholder(name), sql.SQL(pg_type))
                for name, pg_type in args
                if name in params
            ]
            values = {name: _adapt(params[name], pg_type) for name, pg_type in args if name in params}
            call = sql.SQL("public.{}({})").format(sql.Identifier(fn), sql.SQL(", ").join(parts))
            query = sql.SQL("select * from {}").format(call) if returns_rows else sql.SQL("select {} as result").format(call)
            try:
                with conn.transaction():
                    conn.execute("set local role authenticated")
                    claims = {"sub": str(self.user_id), "role": "authenticated"} if self.user_id else {"role": "anon"}
                    conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
                    rows = conn.execute(query, values).fetchall()
            except psycopg.Error as exc:
                diag = exc.diag
                raise error_from_sqlstate(diag.sqlstate, diag.message_primary or str(exc),
                                          detail=diag.message_detail) from exc
            if returns_rows:
                return [_jsonable(r) for r in rows]
            return _jsonable(rows[0]["result"]) if rows else None


class Db:
    """Acceso de superusuario para preparar datos y del rol authenticated para verificar RLS."""

    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo
        self._pool: Any = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def admin(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.conninfo, row_factory=dict_row, autocommit=True)

    def execute(self, query: str, params: Any = None) -> list[dict[str, Any]]:
        with self.admin() as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else []

    def create_user(self, *roles: str, email: str | None = None) -> UUID:
        user_id = uuid.uuid4()
        self.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email or f"{user_id}@test.local"))
        for role in roles:
            self.execute("insert into public.app_roles (user_id, role) values (%s, %s)", (user_id, role))
        return user_id

    def rpc(self, user_id: UUID | None) -> Any:
        """Cliente de producción (PgClient, DEC-21): las pruebas ejercitan el mismo código que los servicios."""
        from psycopg_pool import ConnectionPool

        from botcentro.db.pgclient import PgClient

        if self._pool is None:
            self._pool = ConnectionPool(self.conninfo, min_size=1, max_size=4, open=True,
                                        kwargs={"row_factory": dict_row})
        return PgClient(self._pool, user_id)

    def as_user(self, user_id: UUID | None, query: str, params: Any = None) -> list[dict[str, Any]]:
        """Consulta directa como authenticated (o anon si user_id es None), como vía REST."""
        with psycopg.connect(self.conninfo, row_factory=dict_row) as conn:
            with conn.transaction():
                role = "authenticated" if user_id else "anon"
                conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
                claims = {"sub": str(user_id), "role": role} if user_id else {"role": "anon"}
                conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
                cur = conn.execute(query, params)
                return cur.fetchall() if cur.description else []
