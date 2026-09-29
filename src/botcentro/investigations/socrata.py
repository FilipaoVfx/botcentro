"""Cliente Socrata propio para datos.gov.co (INT-01, ING-02, ING-10, SRC-04, SRC-05, CON-01..04).

* SoQL solo con campos del mapeo y operadores permitidos; los literales se escapan (T-04). Nunca
  se acepta SoQL escrito por usuarios o modelos.
* Huella de esquema sobre nombres y tipos (sin fechas ni conteos). Un campo crítico ausente
  detiene el adaptador (T-06); uno opcional lo degrada de forma visible (T-07). Un mapeo que cita
  un campo inexistente se rechaza antes de ingerir (T-08).
* Un fallo total nunca se devuelve como lista vacía (T-01). `known_total` es nulo si no se pidió.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

from botcentro.errors import FailureKind, FetchError
from botcentro.investigations.results import AdapterResult, ErrorCode, SourceError, Status

BASE = "https://www.datos.gov.co"
DOMAINS = ("www.datos.gov.co", "datos.gov.co")
PAGE_SIZE = 1000
_FIELD_RE = re.compile(r"^:?[a-z_][a-z0-9_]*$")
OPERATORS = frozenset({"=", "!=", ">", ">=", "<", "<=", "is null", "is not null", "in", "starts_with"})


class SchemaIncompatible(Exception):
    def __init__(self, missing_critical: Sequence[str], fingerprint: str) -> None:
        super().__init__(f"faltan campos críticos: {', '.join(missing_critical)}")
        self.missing_critical = list(missing_critical)
        self.fingerprint = fingerprint


class MappingRejected(ValueError):
    """El mapeo cita campos que la fuente no publica (T-08): se corrige antes de cargar."""


def soql_literal(value: object) -> str:
    """Literal SoQL seguro: texto entre comillas simples con apóstrofes duplicados (D'ANGELO → 'D''ANGELO')."""
    if value is None:
        raise ValueError("usar «is null» en lugar de comparar con None")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        raise ValueError("los números se pasan como Decimal o int, no float")
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%dT%H:%M:%S") + "'"
    if isinstance(value, date):
        return "'" + value.isoformat() + "T00:00:00'"
    text = str(value)
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("un literal no puede contener caracteres de control")
    return "'" + text.replace("'", "''") + "'"


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any = None


@dataclass
class Query:
    """Consulta construida solo con campos permitidos (los del mapeo más los de sistema de Socrata)."""

    allowed_fields: frozenset[str]
    conditions: list[Condition] = field(default_factory=list)
    any_of: list[list[Condition]] = field(default_factory=list)  # grupos OR, combinados con AND
    order: list[str] = field(default_factory=list)
    limit: int = PAGE_SIZE
    offset: int = 0

    def _check(self, name: str) -> str:
        if not _FIELD_RE.match(name) or (name not in self.allowed_fields and not name.startswith(":")):
            raise ValueError(f"campo no permitido en SoQL: {name!r}")
        return name

    def _cond(self, c: Condition) -> str:
        if c.op not in OPERATORS:
            raise ValueError(f"operador no permitido: {c.op!r}")
        name = self._check(c.field)
        if c.op in ("is null", "is not null"):
            return f"{name} {c.op}"
        if c.op == "in":
            values = list(c.value or [])
            if not values:
                return "false"  # lista vacía: no coincide nada (y no rompe la consulta)
            return f"{name} in({', '.join(soql_literal(v) for v in values)})"
        if c.op == "starts_with":
            return f"starts_with({name}, {soql_literal(c.value)})"
        return f"{name} {c.op} {soql_literal(c.value)}"

    def params(self) -> dict[str, str]:
        parts = [self._cond(c) for c in self.conditions]
        parts += ["(" + " OR ".join(self._cond(c) for c in group) + ")" for group in self.any_of if group]
        params = {"$limit": str(min(max(self.limit, 1), PAGE_SIZE)), "$offset": str(max(self.offset, 0))}
        if parts:
            params["$where"] = " AND ".join(parts)
        if self.order:
            params["$order"] = ", ".join(self._check(o.split()[0]) + (" DESC" if o.endswith(" DESC") else "")
                                         for o in self.order)
        return params


@dataclass(frozen=True)
class SchemaCheck:
    fingerprint: str
    fields: dict[str, str]
    missing_critical: list[str]
    missing_optional: list[str]
    dataset_updated_at: datetime | None

    @property
    def result(self) -> str:
        return "incompatible" if self.missing_critical else "degraded" if self.missing_optional else "ok"


def schema_fingerprint(fields: Mapping[str, str]) -> str:
    canonical = json.dumps(sorted(fields.items()), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_schema(metadata: Mapping[str, Any], critical: Sequence[str], optional: Sequence[str]) -> SchemaCheck:
    fields = {c["fieldName"]: c["dataTypeName"] for c in metadata.get("columns", [])
              if not str(c["fieldName"]).startswith(":")}
    updated = metadata.get("rowsUpdatedAt")
    from datetime import timezone
    return SchemaCheck(schema_fingerprint(fields), fields, [f for f in critical if f not in fields],
                       [f for f in optional if f not in fields],
                       datetime.fromtimestamp(int(updated), timezone.utc) if updated else None)


class Fetcher(Protocol):
    def fetch(self, url: str, *, method: str = "GET", data: Any = None, accept_mimes: Any = None) -> Any: ...


def classify(exc: Exception) -> ErrorCode:
    if isinstance(exc, FetchError):
        if exc.status == 429 or exc.kind is FailureKind.RATE_LIMITED:
            return ErrorCode.RATE_LIMITED
        if exc.status in (401,):
            return ErrorCode.AUTH
        if exc.status in (403,) or exc.kind is FailureKind.PERMISSION:
            return ErrorCode.RESTRICTED
        if exc.code == "TIMEOUT":
            return ErrorCode.TIMEOUT
        if "SSL" in exc.message or "CERTIFICATE" in exc.message.upper():
            return ErrorCode.TLS
        if exc.kind is FailureKind.INVALID_CONTENT:
            return ErrorCode.INVALID
        return ErrorCode.UNAVAILABLE
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return ErrorCode.PARSE
    return ErrorCode.UNAVAILABLE


class SocrataClient:
    """Consultas de lectura con reintentos acotados (ING-05): Retry-After, backoff exponencial con
    variación y máximo cinco intentos. Autenticación, esquema o validación no se reintentan."""

    def __init__(self, fetcher: Fetcher, *, max_attempts: int = 5, sleep=time.sleep) -> None:  # noqa: ANN001
        self.fetcher = fetcher
        self.max_attempts = max_attempts
        self.sleep = sleep

    def _get_json(self, url: str) -> Any:
        import random
        for attempt in range(1, self.max_attempts + 1):
            try:
                fetched = self.fetcher.fetch(url, accept_mimes=frozenset({"application/json", "text/plain"}))
                return json.loads(fetched.content)
            except FetchError as exc:
                code = classify(exc)
                if code not in (ErrorCode.TIMEOUT, ErrorCode.RATE_LIMITED, ErrorCode.UNAVAILABLE) or attempt == self.max_attempts:
                    raise
                wait = exc.retry_after or min(2 ** attempt, 30) * (0.5 + random.random() / 2)
                self.sleep(min(wait, 60))
        raise AssertionError("inalcanzable")

    def metadata(self, dataset_id: str) -> dict[str, Any]:
        return dict(self._get_json(f"{BASE}/api/views/{dataset_id}.json"))

    def page_url(self, dataset_id: str, query: Query) -> str:
        return f"{BASE}/resource/{dataset_id}.json?{urlencode(query.params())}"

    def fetch_page(self, source_id: str, dataset_id: str, query: Query, *, scope: dict[str, Any] | None = None) -> AdapterResult:
        try:
            rows = self._get_json(self.page_url(dataset_id, query))
        except Exception as exc:  # noqa: BLE001 — se tipifica; nunca se convierte en «vacío»
            return AdapterResult.failed(source_id, classify(exc), str(exc)[:300], scope)
        if not isinstance(rows, list):
            return AdapterResult.failed(source_id, ErrorCode.INVALID, "la respuesta no es una lista", scope)
        more = len(rows) >= query.limit
        return AdapterResult(source_id, Status.OK if rows else Status.EMPTY, items=rows, returned_count=len(rows),
                             next_cursor=str(query.offset + len(rows)) if more else None, scope=scope or {})

    def count(self, dataset_id: str, query: Query) -> int:
        params = query.params()
        params.pop("$order", None)
        params.pop("$offset", None)
        params["$select"] = "count(*) as n"
        params["$limit"] = "1"
        rows = self._get_json(f"{BASE}/resource/{dataset_id}.json?{urlencode(params)}")
        return int(rows[0]["n"]) if rows else 0


def source_error(result: AdapterResult) -> SourceError | None:
    return result.errors[0] if result.errors else None
