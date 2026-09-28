"""Métricas de la interfaz en Redis (centrorequirement.md §28.1; DEC-18).

Contadores diarios por evento y una muestra de latencias recientes. No contienen texto de
preguntas ni identificadores de usuario (baja cardinalidad). El panel las lee para la etapa
«Bot»; mientras dure el cupo gratuito no se escriben en InsForge.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from botcentro.domain.dates import BOGOTA

LATENCY_SAMPLES = 500


def metrics_prefix(bot_id: int, namespace: str = "prod") -> str:
    return f"bc:{namespace}:{bot_id}:metrics"


def _day() -> str:
    return datetime.now(BOGOTA).date().isoformat()


class UiMetrics:
    def __init__(self, redis: Any, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix

    async def record(self, event: str, *, ms: int | None = None, entry: str | None = None) -> None:
        key = f"{self.prefix}:{_day()}"
        pipe = self.redis.pipeline()
        pipe.hincrby(key, event, 1)
        if entry:
            pipe.hincrby(key, f"{event}:{entry}", 1)
        pipe.expire(key, 60 * 60 * 24 * 35)
        if ms is not None:
            pipe.lpush(f"{self.prefix}:latency:{entry or 'all'}", ms)
            pipe.ltrim(f"{self.prefix}:latency:{entry or 'all'}", 0, LATENCY_SAMPLES - 1)
        await pipe.execute()


def summarize(redis: Any, prefix: str) -> dict[str, Any]:
    """Lectura síncrona para el panel: contadores de hoy y p95 de latencia por tipo de entrada."""
    raw = redis.hgetall(f"{prefix}:{_day()}") or {}
    counts = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}
    p95 = {}
    for entry in ("text", "button", "command"):
        values = sorted(int(v) for v in redis.lrange(f"{prefix}:latency:{entry}", 0, -1))
        if values:
            p95[entry] = values[min(len(values) - 1, int(len(values) * 0.95))]
    return {"counts": counts, "p95_ms": p95}
