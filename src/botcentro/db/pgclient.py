"""Cliente de base de datos sobre PostgreSQL autoalojado (DEC-21): reemplaza al cliente HTTP de InsForge.

Conserva la semántica que las funciones SQL esperan de PostgREST: una transacción por llamada,
`SET LOCAL ROLE authenticated` y `request.jwt.claims` con el `sub` del usuario, de modo que
`auth.uid()`, los roles de aplicación (`require_app_role`) y RLS deciden igual que antes. Los
parámetros se pasan por nombre con el tipo exacto de cada argumento de la función.

La conexión usa un rol de inicio de sesión sin privilegios propios (`botcentro_app`), miembro de
`authenticated` y `anon`: no es dueño de tablas ni supera RLS. El secreto de esa conexión es del
servidor, igual que antes la API key de InsForge.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from botcentro.insforge.client import InsForgeError, error_from_sqlstate

_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()
_OPS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def conninfo_from_env(env: Mapping[str, str] | None = None) -> str:
    env = env if env is not None else os.environ
    url = env.get("BOTCENTRO_DATABASE_URL")
    if not url:
        raise RuntimeError("falta BOTCENTRO_DATABASE_URL")
    return url


def shared_pool(conninfo: str, *, max_size: int = 8) -> ConnectionPool:
    """Un pool por proceso y cadena de conexión (los clientes por usuario lo comparten)."""
    with _POOLS_LOCK:
        pool = _POOLS.get(conninfo)
        if pool is None:
            pool = ConnectionPool(conninfo, min_size=1, max_size=max_size, open=True, name="botcentro",
                                  kwargs={"row_factory": dict_row, "application_name": "botcentro"})
            _POOLS[conninfo] = pool
        return pool


def _adapt(value: Any, pg_type: str) -> Any:
    if value is None:
        return None
    if pg_type in ("jsonb", "json"):
        return Jsonb(value)
    if pg_type == "vector":
        return "[" + ",".join(str(float(v)) for v in value) + "]"
    return value


def _jsonable(value: Any) -> Any:
    """El resultado como lo devolvería PostgREST (JSON)."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _raise(exc: psycopg.Error) -> None:
    diag = exc.diag
    raise error_from_sqlstate(diag.sqlstate, diag.message_primary or str(exc), detail=diag.message_detail) from exc


class PgClient:
    """Llamadas como un usuario concreto (`user_id`) o como anónimo (None)."""

    _signatures: dict[str, tuple[list[tuple[str, str]], bool]] = {}
    _sig_lock = threading.Lock()

    def __init__(self, pool: ConnectionPool, user_id: UUID | str | None) -> None:
        self.pool = pool
        self.user_id = str(user_id) if user_id else None

    # -- identidad -------------------------------------------------------------------------------

    def _enter(self, conn: psycopg.Connection[Any]) -> None:
        role = "authenticated" if self.user_id else "anon"
        conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
        claims = {"sub": self.user_id, "role": role} if self.user_id else {"role": "anon"}
        conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))

    # -- RPC -------------------------------------------------------------------------------------

    def _signature(self, conn: psycopg.Connection[Any], fn: str) -> tuple[list[tuple[str, str]], bool]:
        with self._sig_lock:
            cached = self._signatures.get(fn)
        if cached is not None:
            return cached
        row = conn.execute(
            """
            select p.proargnames, p.proargmodes,
                   array(select format_type(t, null) from unnest(p.proargtypes::oid[]) t) as in_types,
                   p.proretset, format_type(p.prorettype, null) as rettype,
                   (select typtype from pg_type where oid = p.prorettype) as typtype
              from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'public' and p.proname = %s
            """, (fn,)).fetchone()
        if row is None:
            raise InsForgeError("FUNCTION_NOT_FOUND", f"función inexistente: {fn}", status=404)
        names, modes = row["proargnames"] or [], row["proargmodes"]
        in_names = [n for n, m in zip(names, modes or ["i"] * len(names)) if m in ("i", "b")]
        result = (list(zip(in_names, row["in_types"])),
                  bool(row["proretset"] or row["typtype"] == "c" or row["rettype"] == "record"))
        with self._sig_lock:
            self._signatures[fn] = result
        return result

    def call(self, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        params = dict(params or {})
        with self.pool.connection() as conn:
            try:
                with conn.transaction():
                    args, returns_rows = self._signature(conn, fn)
                    unknown = set(params) - {name for name, _ in args}
                    if unknown:
                        raise InsForgeError("INVALID_PARAMS", f"parámetros desconocidos para {fn}: {sorted(unknown)}",
                                            status=400)
                    parts = [sql.SQL("{} => {}::{}").format(sql.Identifier(n), sql.Placeholder(n), sql.SQL(t))
                             for n, t in args if n in params]
                    values = {n: _adapt(params[n], t) for n, t in args if n in params}
                    call = sql.SQL("public.{}({})").format(sql.Identifier(fn), sql.SQL(", ").join(parts))
                    query = (sql.SQL("select * from {}") if returns_rows else sql.SQL("select {} as result")).format(call)
                    self._enter(conn)
                    rows = conn.execute(query, values).fetchall()
            except psycopg.Error as exc:
                _raise(exc)
        if returns_rows:
            return [_jsonable(r) for r in rows]
        return _jsonable(rows[0]["result"]) if rows else None

    # -- registros (subconjunto PostgREST usado por la CLI) ----------------------------------------

    def _filters(self, params: Mapping[str, str]) -> tuple[sql.Composable, dict[str, Any]]:
        clauses, values = [], {}
        for i, (column, expr) in enumerate((k, v) for k, v in params.items() if k not in ("select", "order", "limit", "offset")):
            op, _, raw = str(expr).partition(".")
            if op == "is" and raw == "null":
                clauses.append(sql.SQL("{} is null").format(sql.Identifier(column)))
                continue
            if op not in _OPS:
                raise InsForgeError("INVALID_FILTER", f"operador no admitido: {op}", status=400)
            clauses.append(sql.SQL("{} {} {}").format(sql.Identifier(column), sql.SQL(_OPS[op]), sql.Placeholder(f"f{i}")))
            values[f"f{i}"] = raw
        where = sql.SQL(" where ") + sql.SQL(" and ").join(clauses) if clauses else sql.SQL("")
        return where, values

    def _run(self, query: sql.Composable, values: Mapping[str, Any] | Sequence[Any] | None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            try:
                with conn.transaction():
                    self._enter(conn)
                    cur = conn.execute(query, values)
                    rows = cur.fetchall() if cur.description else []
            except psycopg.Error as exc:
                _raise(exc)
        return [_jsonable(r) for r in rows]

    def select(self, table: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        cols = params.get("select", "*")
        columns = sql.SQL("*") if cols == "*" else sql.SQL(", ").join(sql.Identifier(c.strip()) for c in cols.split(","))
        where, values = self._filters(params)
        query = sql.SQL("select {} from public.{}{}").format(columns, sql.Identifier(table), where)
        if params.get("order"):
            column, _, direction = params["order"].partition(".")
            query += sql.SQL(" order by {} {}").format(sql.Identifier(column),
                                                       sql.SQL("desc" if direction == "desc" else "asc"))
        if params.get("limit"):
            query += sql.SQL(" limit {}").format(sql.Literal(int(params["limit"])))
        if params.get("offset"):
            query += sql.SQL(" offset {}").format(sql.Literal(int(params["offset"])))
        return self._run(query, values)

    def insert(self, table: str, rows: Sequence[Mapping[str, Any]], *, returning: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            cols = list(row)
            query = sql.SQL("insert into public.{} ({}) values ({}){}").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)), sql.SQL(" returning *" if returning else ""))
            out += self._run(query, [Jsonb(v) if isinstance(v, (dict, list)) else v for v in row.values()])
        return out

    def update(self, table: str, filters: Mapping[str, str], values: Mapping[str, Any]) -> list[dict[str, Any]]:
        where, fvalues = self._filters(filters)
        sets = sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(f"v_{k}")) for k in values)
        query = sql.SQL("update public.{} set {}{} returning *").format(sql.Identifier(table), sets, where)
        params = {**fvalues, **{f"v_{k}": Jsonb(v) if isinstance(v, (dict, list)) else v for k, v in values.items()}}
        return self._run(query, params)


def service_client(email: str, *, conninfo: str | None = None) -> PgClient:
    """Cliente de una cuenta de servicio (ingesta, consulta) identificada por su correo en auth.users."""
    pool = shared_pool(conninfo or conninfo_from_env())
    with pool.connection() as conn:
        row = conn.execute("select public.service_user_id(%s) as id", (email,)).fetchone()
    if not row or not row["id"]:
        raise InsForgeError("SERVICE_ACCOUNT", f"cuenta de servicio inexistente: {email}", status=401)
    return PgClient(pool, row["id"])
