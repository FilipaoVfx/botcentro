"""Reintentos con backoff y decisiones ante fallos (SRS-F05, §7.3; T-04).

Valores iniciales propuestos por el SRS: 5 intentos, backoff exponencial de 30 s a 1 h con
jitter, respeto de Retry-After. Autenticación/permisos suspenden la fuente; cambios de
esquema van a cuarentena; nada se acepta como carga exitosa por un mapeo vacío.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum

from botcentro.errors import FailureKind

MAX_RETRY_AFTER_SECONDS = 24 * 3600


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 30.0
    max_delay: float = 3600.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("política de reintentos inválida")


class Action(StrEnum):
    RETRY = "retry"
    SUSPEND_SOURCE = "suspend_source"
    QUARANTINE = "quarantine"
    SKIP = "skip"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class Decision:
    action: Action
    delay_seconds: float | None = None


def backoff_delay(attempt: int, policy: RetryPolicy, rng: random.Random | None = None) -> float:
    """Backoff exponencial con «equal jitter»: entre la mitad y el total del escalón."""
    if attempt < 1:
        raise ValueError("attempt empieza en 1")
    rng = rng or random.Random()
    step = min(policy.max_delay, policy.base_delay * 2 ** (attempt - 1))
    return step / 2 + rng.uniform(0, step / 2)


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After en segundos o como fecha HTTP. Valores inválidos o negativos → None."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds < 0:
        return 0.0
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def classify_http_status(status: int) -> FailureKind:
    if status == 429:
        return FailureKind.RATE_LIMITED
    if status in (401, 407):
        return FailureKind.AUTH
    if status == 403:
        return FailureKind.PERMISSION
    if status in (404, 410):
        return FailureKind.NOT_FOUND
    if status == 408 or status >= 500:
        return FailureKind.TRANSIENT
    return FailureKind.INVALID_CONTENT


def decide(
    kind: FailureKind,
    attempt: int,
    policy: RetryPolicy = RetryPolicy(),
    *,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> Decision:
    """Qué hacer tras el intento número `attempt` (1 = primer intento) fallido."""
    if kind in (FailureKind.TRANSIENT, FailureKind.RATE_LIMITED, FailureKind.UNKNOWN):
        if attempt >= policy.max_attempts:
            return Decision(Action.DEAD_LETTER)
        delay = backoff_delay(attempt, policy, rng)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return Decision(Action.RETRY, delay)
    if kind in (FailureKind.AUTH, FailureKind.PERMISSION):
        return Decision(Action.SUSPEND_SOURCE)
    if kind in (FailureKind.SCHEMA_CHANGED, FailureKind.INVALID_CONTENT):
        return Decision(Action.QUARANTINE)
    if kind is FailureKind.NOT_FOUND:
        # Un 404 no demuestra retiro: se registra la comprobación y se sigue (SRS §7.1).
        return Decision(Action.SKIP)
    return Decision(Action.DEAD_LETTER)
