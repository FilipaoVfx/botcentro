"""Errores tipados y su clasificación operativa (SRS-F05, §10.2)."""

from __future__ import annotations

from enum import StrEnum


class FailureKind(StrEnum):
    """Clase de fallo; decide si se reintenta, se suspende la fuente o se pone en cuarentena."""

    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    SCHEMA_CHANGED = "schema_changed"
    INVALID_CONTENT = "invalid_content"
    RESOURCE_LIMIT = "resource_limit"
    POLICY = "policy"
    UNKNOWN = "unknown"


class BotcentroError(Exception):
    """Error con código estable, apto para registrar y para la respuesta uniforme de la API."""

    kind: FailureKind = FailureKind.UNKNOWN

    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: FailureKind | None = None,
        retry_after: float | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if kind is not None:
            self.kind = kind
        self.retry_after = retry_after
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in {FailureKind.TRANSIENT, FailureKind.RATE_LIMITED}


class FetchError(BotcentroError):
    """Fallo al adquirir un recurso de una fuente."""


class PolicyViolation(BotcentroError):
    """Operación no habilitada por el perfil de uso de la fuente (SRS-F02)."""

    kind = FailureKind.POLICY
