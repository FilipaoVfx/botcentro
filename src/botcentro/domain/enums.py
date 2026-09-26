"""Vocabularios cerrados compartidos con el esquema (CHECK de las migraciones)."""

from enum import StrEnum


class Authority(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    PUBLIC_DISCOURSE = "public_discourse"
    NORMATIVE = "normative"


class SourceState(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    ACTIVE = "active"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class Corporation(StrEnum):
    SENADO = "senado"
    CAMARA = "camara"


class InitiativeType(StrEnum):
    PROYECTO_LEY = "proyecto_ley"
    PROYECTO_ACTO_LEGISLATIVO = "proyecto_acto_legislativo"


class AgendaStatus(StrEnum):
    """Estados de programación (SRS-F11). La realización se registra como evento aparte."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class QualityStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class ObservationStatus(StrEnum):
    CANDIDATE = "candidate"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class QueryStatus(StrEnum):
    """Estados de resultado de una consulta (SRS §10.1)."""

    QUEUED = "queued"
    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    FAILED = "failed"


class QueryIntent(StrEnum):
    STRUCTURED = "structured"
    DOCUMENT = "document"
    HYBRID = "hybrid"
    COMPARISON = "comparison"
    AGENDA = "agenda"
    UNSUPPORTED = "unsupported"
