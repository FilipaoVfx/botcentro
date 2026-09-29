"""Contrato lógico de conectores (SRS §7.1) y sobre común de normalización (SRS §7.2).

    validate_source(config) -> capabilities, policy_status, diagnostics
    discover(cursor, coverage_scope) -> items[], next_cursor, has_more
    fetch(item, conditional_headers) -> snapshot | not_modified | typed_error
    parse(snapshot, parser_version) -> normalized_candidates[], document_links[], issues[]
    checkpoint(batch_id) -> committed_cursor        (lo hace el runner vía ingest_commit_cursor)
    health() -> last_success, lag, error_rate, source_state

Reglas: `discover` no declara inexistente un objeto por faltar en una página; el cursor
incluye la versión del conector y los parámetros de cobertura, y un cursor de otra versión
no se reinterpreta en silencio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from botcentro.domain.dates import PartialDate
from botcentro.http.fetcher import Fetched, NotModified

ENVELOPE_SCHEMA_VERSION = "1.0"


class IncompatibleCursor(ValueError):
    """El cursor pertenece a otra versión del conector o a otro alcance: abrir ejecución nueva."""


@dataclass(frozen=True)
class Cursor:
    connector_version: str
    scope: Mapping[str, Any]
    position: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"connector_version": self.connector_version, "scope": dict(self.scope), "position": dict(self.position)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None, *, connector_version: str, scope: Mapping[str, Any]) -> Cursor:
        """Cursor inicial si no hay uno previo; error si el previo es incompatible."""
        if data is None:
            return cls(connector_version, dict(scope))
        if data.get("connector_version") != connector_version:
            raise IncompatibleCursor(
                f"cursor de la versión {data.get('connector_version')!r}, conector {connector_version!r}"
            )
        if dict(data.get("scope") or {}) != dict(scope):
            raise IncompatibleCursor("el cursor pertenece a otro alcance de cobertura")
        return cls(connector_version, dict(scope), dict(data.get("position") or {}))

    def advance(self, **position: Any) -> Cursor:
        return Cursor(self.connector_version, self.scope, {**self.position, **position})


@dataclass(frozen=True)
class DiscoveredItem:
    record_type: str
    logical_key: str
    url: str
    external_id: str | None = None
    accept_mimes: frozenset[str] | None = None
    is_file: bool = False  # descarga de archivo (PDF...): exige download_files, no solo metadata
    hints: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoverPage:
    items: Sequence[DiscoveredItem]
    next_cursor: Cursor
    has_more: bool
    # Límite de ejecución alcanzado (filas, solicitudes, minutos): la ejecución termina en
    # `partial` y el cursor queda listo para continuar; nunca cuenta como éxito completo (ING-04).
    limit_reached: str | None = None


class IssueSeverity(StrEnum):
    WARNING = "warning"
    QUARANTINE = "quarantine"  # el registro se conserva pero no se publica
    SCHEMA_CHANGE = "schema_change"  # la estructura esperada cambió: alerta y cuarentena


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: IssueSeverity = IssueSeverity.WARNING
    pointer: str | None = None


@dataclass(frozen=True)
class NormalizedCandidate:
    """Afirmación extraída de una captura, lista para registrarse como observación."""

    subject_type: str
    subject_ref: str
    predicate: str
    value: Mapping[str, Any]
    value_raw: str | None = None
    effective: PartialDate = field(default_factory=PartialDate.unknown)
    effective_at: datetime | None = None
    published_on: PartialDate = field(default_factory=PartialDate.unknown)
    record_pointer: str | None = None
    quarantine_reason: str | None = None


@dataclass(frozen=True)
class DocumentLink:
    """Documento enlazado desde un registro; se descarga como ítem propio si la política lo permite."""

    url: str
    document_type: str
    logical_key: str
    title: str | None = None
    record_pointer: str | None = None


@dataclass(frozen=True)
class ParseResult:
    candidates: Sequence[NormalizedCandidate] = ()
    document_links: Sequence[DocumentLink] = ()
    issues: Sequence[Issue] = ()
    # La fuente respondió con un conjunto vacío válido (p. ej. una semana de receso).
    empty_source: bool = False

    @property
    def schema_changed(self) -> bool:
        return any(issue.severity is IssueSeverity.SCHEMA_CHANGE for issue in self.issues)

    @property
    def is_empty_mapping(self) -> bool:
        """Sin candidatos ni enlaces sin que la fuente declarara vacío: nunca es carga exitosa (SRS-F05)."""
        return not self.candidates and not self.document_links and not self.empty_source


@dataclass(frozen=True)
class ValidationReport:
    capabilities: Sequence[str]
    diagnostics: Sequence[str]

    @property
    def ok(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class HealthReport:
    last_success_at: datetime | None
    lag_seconds: float | None
    error_rate: float | None
    source_state: str


class Connector(Protocol):
    adapter: str
    version: str
    parser_version: str

    def validate_source(self, config: Mapping[str, Any]) -> ValidationReport: ...

    def discover(self, cursor: Cursor) -> DiscoverPage: ...

    def fetch(self, item: DiscoveredItem, *, etag: str | None = None, last_modified: str | None = None) -> Fetched | NotModified: ...

    def parse(self, item: DiscoveredItem, snapshot: Fetched) -> ParseResult: ...
