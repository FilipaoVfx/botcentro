"""Perfil de uso de fuentes y diagnóstico de activación (SRS-F01, F02; T-01).

Un permiso desconocido nunca equivale a aprobación. El trigger enforce_source_activation de
la base aplica las mismas reglas; aquí se obtienen diagnósticos completos antes de intentar
el cambio de estado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from botcentro.domain.enums import SourceState
from botcentro.errors import PolicyViolation


class Permission(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class Operation(StrEnum):
    CAPTURE_METADATA = "capture_metadata"
    DOWNLOAD_FILES = "download_files"
    RETAIN_CONTENT = "retain_content"
    GENERATE_DERIVATIVES = "generate_derivatives"
    INDEX_CONTENT = "index_content"
    REDISTRIBUTE = "redistribute"


# Operación → operación de la que depende (mismas restricciones que source_policies).
_DEPENDS_ON = {
    Operation.DOWNLOAD_FILES: Operation.CAPTURE_METADATA,
    Operation.RETAIN_CONTENT: Operation.DOWNLOAD_FILES,
    Operation.GENERATE_DERIVATIVES: Operation.DOWNLOAD_FILES,
    Operation.INDEX_CONTENT: Operation.GENERATE_DERIVATIVES,
}


@dataclass(frozen=True)
class UsageProfile:
    capture_metadata: Permission = Permission.UNKNOWN
    download_files: Permission = Permission.UNKNOWN
    retain_content: Permission = Permission.UNKNOWN
    generate_derivatives: Permission = Permission.UNKNOWN
    index_content: Permission = Permission.UNKNOWN
    redistribute: Permission = Permission.UNKNOWN
    reviewed_at: datetime | None = None
    reviewer: str | None = None

    def permission(self, operation: Operation) -> Permission:
        return Permission(getattr(self, operation.value))

    def permits(self, operation: Operation) -> bool:
        """Solo 'allowed' explícito, con sus dependencias también permitidas."""
        if self.permission(operation) is not Permission.ALLOWED:
            return False
        dependency = _DEPENDS_ON.get(operation)
        return dependency is None or self.permits(dependency)

    def require(self, operation: Operation) -> None:
        if not self.permits(operation):
            raise PolicyViolation(
                "OPERATION_NOT_PERMITTED",
                f"el perfil de uso no habilita {operation.value} ({self.permission(operation).value})",
            )

    def inconsistencies(self) -> list[str]:
        problems = []
        for operation, dependency in _DEPENDS_ON.items():
            if self.permission(operation) is Permission.ALLOWED and self.permission(dependency) is not Permission.ALLOWED:
                problems.append(f"{operation.value} permitido sin {dependency.value}")
        return problems


@dataclass(frozen=True)
class SourceActivationInput:
    code: str
    target_state: SourceState
    profile: UsageProfile | None
    coverage_scopes: int
    base_url: str | None
    allowed_domains: Sequence[str]
    adapter: str | None
    adapter_version: str | None
    owner: str | None


def activation_diagnostics(source: SourceActivationInput) -> list[str]:
    """Motivos por los que la fuente no puede pasar al estado solicitado (vacío = permitido)."""
    if source.target_state not in (SourceState.VALIDATED, SourceState.ACTIVE, SourceState.DEGRADED):
        return []
    problems: list[str] = []
    profile = source.profile
    if profile is None:
        problems.append("sin perfil de uso asociado")
    else:
        if profile.reviewed_at is None or not profile.reviewer:
            problems.append("perfil de uso sin revisión registrada")
        if profile.capture_metadata is not Permission.ALLOWED:
            problems.append(f"captura de metadata no aprobada ({profile.capture_metadata.value})")
        problems.extend(profile.inconsistencies())
    if source.coverage_scopes == 0:
        problems.append("sin alcance de cobertura declarado")
    if source.target_state in (SourceState.ACTIVE, SourceState.DEGRADED):
        if not source.base_url:
            problems.append("sin URL de entrada")
        if not source.allowed_domains:
            problems.append("sin dominios permitidos")
        if not source.adapter or not source.adapter_version:
            problems.append("sin adaptador versionado")
        if not source.owner:
            problems.append("sin responsable")
    return problems
