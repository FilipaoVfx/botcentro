"""Etapas en vivo del panel: Qdrant, gacetas y bot (I5; DEC-12, DEC-15, DEC-18)."""

import json
from datetime import datetime, timedelta, timezone

from botcentro.panel.live_stats import LiveStats


def _stats(tmp_path, status=None):
    path = tmp_path / "status.json"
    if status is not None:
        path.write_text(json.dumps(status))
    return LiveStats(qdrant_url=None, qdrant_key=None, collection="c", gacetas_status=path, redis_url=None,
                     metrics_prefix=None)


def test_enrich_replaces_stale_index_and_adds_live_stages(tmp_path) -> None:
    overview = {"access": True, "stages": [{"stage": "documents"}, {"stage": "index", "total": 0}, {"stage": "queries"}]}
    stages = _stats(tmp_path).enrich(overview)["stages"]
    assert [s["stage"] for s in stages] == ["documents", "gacetas", "index", "queries", "bot"]
    index = next(s for s in stages if s["stage"] == "index")
    assert "total" not in index and index["unit"] == "Qdrant no configurado"  # nunca 0 si no se pudo medir


def test_gacetas_stage_reports_progress_and_detects_stall(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    live = _stats(tmp_path, {"state": "running", "listed": 8741, "done": 300, "failed": 2, "since": "2022-07-20",
                             "updated_at": now.isoformat()}).gacetas_stage()
    assert (live["total"], live["pending"], live["running"], live["failed"]) == (300, 8441, 1, 2)
    stalled = _stats(tmp_path, {"state": "running", "listed": 10, "done": 1, "failed": 0,
                                "updated_at": (now - timedelta(hours=1)).isoformat()}).gacetas_stage()
    assert stalled["running"] == 0 and "sin avance" in stalled["unit"]


def test_denied_overview_is_untouched(tmp_path) -> None:
    assert _stats(tmp_path).enrich({"access": False}) == {"access": False}
