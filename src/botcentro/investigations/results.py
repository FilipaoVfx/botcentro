"""Resultado de adaptadores y respuestas multifuente (CON-01..05; T-01, T-02, T-03).

`empty` exige una respuesta válida sin resultados en el alcance; un fallo nunca es «vacío».
Si una rama falla y otra devuelve registros: `partial` con la fuente fallida identificada.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class ErrorCode(StrEnum):
    TIMEOUT = "UPSTREAM_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH = "AUTHENTICATION"
    RESTRICTED = "ACCESS_RESTRICTED"
    SCHEMA = "SCHEMA_INCOMPATIBLE"
    PARSE = "PARSE_ERROR"
    INVALID = "INVALID_CONTENT"
    BUDGET = "BUDGET_EXHAUSTED"
    UNAVAILABLE = "SOURCE_UNAVAILABLE"
    UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    TLS = "TLS_ERROR"


SAFE_MESSAGES = {
    ErrorCode.TIMEOUT: "Una fuente no respondió a tiempo",
    ErrorCode.RATE_LIMITED: "Una fuente limitó las consultas; se reintentará",
    ErrorCode.AUTH: "La fuente rechazó la autenticación",
    ErrorCode.RESTRICTED: "La fuente restringe el acceso a esa información",
    ErrorCode.SCHEMA: "La estructura de la fuente cambió y la consulta se detuvo",
    ErrorCode.PARSE: "La respuesta de la fuente no se pudo interpretar",
    ErrorCode.INVALID: "La fuente devolvió contenido inválido",
    ErrorCode.BUDGET: "Se agotó el presupuesto configurado",
    ErrorCode.UNAVAILABLE: "Una fuente no está disponible",
    ErrorCode.UNSUPPORTED: "La fuente no ofrece esa capacidad",
    ErrorCode.TLS: "La conexión segura con la fuente falló; no se desactiva la verificación",
}
RETRYABLE = {ErrorCode.TIMEOUT, ErrorCode.RATE_LIMITED, ErrorCode.UNAVAILABLE}


@dataclass(frozen=True)
class SourceError:
    source_id: str
    code: ErrorCode
    detail: str = ""  # diagnóstico restringido: no se muestra al usuario

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def safe_message(self) -> str:
        return SAFE_MESSAGES[self.code]

    def public(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "code": self.code.value, "retryable": self.retryable,
                "safe_message": self.safe_message}


@dataclass
class AdapterResult:
    source_id: str
    status: Status
    items: list[dict[str, Any]] = field(default_factory=list)
    returned_count: int = 0
    known_total: int | None = None  # nulo si no se conoce: nunca el tamaño de una página
    next_cursor: str | None = None
    errors: list[SourceError] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failed(cls, source_id: str, code: ErrorCode, detail: str = "", scope: dict[str, Any] | None = None) -> AdapterResult:
        return cls(source_id, Status.UNAVAILABLE if code is not ErrorCode.UNSUPPORTED else Status.UNSUPPORTED,
                   errors=[SourceError(source_id, code, detail)], scope=scope or {})

    def public(self) -> dict[str, Any]:
        return {"schema_version": "1.0", "source_id": self.source_id, "status": self.status.value,
                "items": self.items, "pagination": {"next_cursor": self.next_cursor, "returned_count": self.returned_count,
                                                    "known_total": self.known_total},
                "coverage": {"scope": self.scope, "complete_for_scope": self.status is Status.OK and not self.next_cursor},
                "errors": [e.public() for e in self.errors]}


def combine(results: Sequence[AdapterResult]) -> dict[str, Any]:
    """Respuesta multifuente (CON-05): el estado global no oculta fuentes fallidas."""
    ok = [r for r in results if r.status in (Status.OK, Status.EMPTY, Status.PARTIAL)]
    failed = [r for r in results if r.status is Status.UNAVAILABLE]
    items = [item for r in ok for item in r.items]
    if not results or all(r.status is Status.UNSUPPORTED for r in results):
        status = Status.UNSUPPORTED
    elif not ok:
        status = Status.UNAVAILABLE
    elif failed or any(r.status is Status.PARTIAL for r in ok):
        status = Status.PARTIAL
    elif not items:
        status = Status.EMPTY
    else:
        status = Status.OK
    return {"status": status.value, "items": items, "returned_count": len(items),
            "failed_sources": [r.source_id for r in failed],
            "errors": [e.public() for r in results for e in r.errors],
            "sources": {r.source_id: r.status.value for r in results}}
