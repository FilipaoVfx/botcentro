"""Etapas que no viven en InsForge (DEC-12, DEC-15, DEC-18), añadidas por el BFF del panel.

* «Índice»: puntos en Qdrant por tipo de documento. Antes se contaba `chunk_embeddings` en
  InsForge, vacía desde DEC-12, y el panel mostraba 0 aunque el índice existiera.
* «Gacetas»: avance de la carga desde su archivo de estado.
* «Bot»: métricas de la interfaz en Redis (hoy).

Si una dependencia no responde, la etapa lo dice («no disponible»): nunca muestra 0.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class LiveStats:
    def __init__(self, *, qdrant_url: str | None, qdrant_key: str | None, collection: str,
                 gacetas_status: Path | None, redis_url: str | None, metrics_prefix: str | None) -> None:
        self.qdrant_url = qdrant_url.rstrip("/") if qdrant_url else None
        self.qdrant_key = qdrant_key
        self.collection = collection
        self.gacetas_status = gacetas_status
        self.redis_url = redis_url
        self.metrics_prefix = metrics_prefix
        self._redis = None

    @classmethod
    def from_env(cls) -> LiveStats:
        from botcentro.telegram_ui.metrics import metrics_prefix
        from botcentro.vectors.qdrant import COLLECTION

        token = os.environ.get("BOTCENTRO_TELEGRAM_BOT_TOKEN", "")
        bot_id = int(token.split(":", 1)[0]) if ":" in token else None
        status = os.environ.get("BOTCENTRO_GACETAS_STATUS", "var/gacetas-manifest-local.status.json")
        return cls(qdrant_url=os.environ.get("BOTCENTRO_QDRANT_URL"), qdrant_key=os.environ.get("BOTCENTRO_QDRANT_API_KEY"),
                   collection=COLLECTION, gacetas_status=Path(status), redis_url=os.environ.get("BOTCENTRO_REDIS_URL"),
                   metrics_prefix=metrics_prefix(bot_id) if bot_id else None)

    # -- etapas --------------------------------------------------------------------------------

    def _qdrant_count(self, client: httpx.Client, kind: str | None) -> int:
        body: dict[str, Any] = {"exact": True}
        if kind:
            body["filter"] = {"must": [{"key": "doc_kind", "match": {"value": kind}}]}
        r = client.post(f"/collections/{self.collection}/points/count", json=body)
        r.raise_for_status()
        return int(r.json()["result"]["count"])

    def index_stage(self) -> dict[str, Any]:
        stage: dict[str, Any] = {"stage": "index", "label": "Índice", "access": True, "unit": "vectores en Qdrant"}
        if not self.qdrant_url:
            return {**stage, "unit": "Qdrant no configurado"}
        try:
            with httpx.Client(base_url=self.qdrant_url, headers={"api-key": self.qdrant_key or ""}, timeout=3) as c:
                total = self._qdrant_count(c, None)
                gacetas, fichas = self._qdrant_count(c, "gaceta"), self._qdrant_count(c, "ficha")
            status = self._read_status()
            return {**stage, "total": total, "pending": None, "running": None, "failed": 0,
                    "last_movement": status.get("updated_at") if status else None,
                    "unit": f"vectores en Qdrant · {fichas:,} de fichas · {gacetas:,} de gacetas".replace(",", ".")}
        except Exception as exc:  # noqa: BLE001 — la etapa informa, no rompe el panel
            log.warning("Qdrant no disponible para el panel: %s", type(exc).__name__)
            return {**stage, "unit": "Qdrant no disponible"}

    def _read_status(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.gacetas_status.read_text()) if self.gacetas_status else None
        except (OSError, ValueError):
            return None

    def gacetas_stage(self) -> dict[str, Any]:
        stage: dict[str, Any] = {"stage": "gacetas", "label": "Gacetas", "access": True, "unit": "gacetas"}
        status = self._read_status()
        if not status:
            return {**stage, "unit": "sin carga en curso"}
        updated = status.get("updated_at")
        stale = False
        if updated:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds()
            stale = status.get("state") == "running" and age > 900  # sin avance en 15 min: proceso detenido
        running = 1 if status.get("state") == "running" and not stale else 0
        done, listed = int(status.get("done", 0)), int(status.get("listed", 0))
        state = {"finished": "terminada", "stopped": "detenida por tope"}.get(status.get("state"), "")
        if stale:
            state = "sin avance hace más de 15 min"
        since = status.get("since") or ""
        return {**stage, "total": done, "pending": max(listed - done, 0), "running": running,
                "failed": int(status.get("failed", 0)), "last_movement": updated,
                "unit": f"procesadas de {listed:,} desde {since}".replace(",", ".") + (f" · {state}" if state else "")}

    def bot_stage(self) -> dict[str, Any]:
        stage: dict[str, Any] = {"stage": "bot", "label": "Bot", "access": True, "unit": "interacciones hoy"}
        if not (self.redis_url and self.metrics_prefix):
            return {**stage, "unit": "métricas no configuradas"}
        try:
            import redis

            from botcentro.telegram_ui.metrics import summarize
            if self._redis is None:
                self._redis = redis.Redis.from_url(self.redis_url, socket_timeout=2)
            data = summarize(self._redis, self.metrics_prefix)
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis no disponible para el panel: %s", type(exc).__name__)
            return {**stage, "unit": "métricas no disponibles"}
        counts, p95 = data["counts"], data["p95_ms"]
        views = counts.get("ui.view_rendered", 0)
        rejected = counts.get("ui.callback_rejected", 0)
        latency = " · ".join(f"p95 {k} {v} ms" for k, v in p95.items())
        return {**stage, "total": views, "pending": None, "running": None, "failed": rejected,
                "unit": f"vistas hoy · {counts.get('ui.context_conflict', 0)} conflictos de contexto"
                        + (f" · {latency}" if latency else "")}

    def enrich(self, overview: dict[str, Any]) -> dict[str, Any]:
        if not overview.get("access") or "stages" not in overview:
            return overview
        stages = [s for s in overview["stages"] if s.get("stage") != "index"]
        position = next((i + 1 for i, s in enumerate(stages) if s.get("stage") == "documents"), len(stages))
        stages[position:position] = [self.gacetas_stage(), self.index_stage()]
        stages.append(self.bot_stage())
        return {**overview, "stages": stages}
