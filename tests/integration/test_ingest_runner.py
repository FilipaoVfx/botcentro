"""Ingesta de punta a punta contra las RPC reales (T-02, T-04, T-12; OBJ-05)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from botcentro.connectors.base import (
    Cursor,
    DiscoveredItem,
    DiscoverPage,
    Issue,
    IssueSeverity,
    NormalizedCandidate,
    ParseResult,
)
from botcentro.domain.dates import parse_spanish_date
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FailureKind, FetchError
from botcentro.http.fetcher import Fetched
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore
from tests.integration.factories import active_source
from tests.integration.support import Db

pytestmark = pytest.mark.db

ALLOWED = UsageProfile(Permission.ALLOWED, Permission.ALLOWED, Permission.ALLOWED, Permission.ALLOWED,
                       Permission.ALLOWED, Permission.UNKNOWN, reviewed_at=datetime.now(timezone.utc),
                       reviewer="revisor")


@dataclass
class FakeConnector:
    """Fuente simulada: páginas de fichas JSON con dos proyectos por página."""

    records: dict[str, dict[str, Any]]
    pages: list[list[str]]
    adapter: str = "fake"
    version: str = "1.0.0"
    parser_version: str = "fake-parser-1"
    failures: dict[str, FetchError] = field(default_factory=dict)
    schema_broken: bool = False

    def validate_source(self, config: Mapping[str, Any]):  # pragma: no cover - no usado
        raise NotImplementedError

    def discover(self, cursor: Cursor) -> DiscoverPage:
        index = int(cursor.position.get("page", 0))
        keys = self.pages[index]
        items = [DiscoveredItem("project", f"ficha:{k}", f"https://datos.example.gov.co/p/{k}", external_id=k)
                 for k in keys]
        return DiscoverPage(items, cursor.advance(page=index + 1), has_more=index + 1 < len(self.pages))

    def fetch(self, item: DiscoveredItem, *, etag: str | None = None, last_modified: str | None = None) -> Fetched:
        if item.external_id in self.failures:
            raise self.failures[item.external_id]
        content = json.dumps(self.records[item.external_id], sort_keys=True).encode()
        return Fetched(item.url, item.url, 200, content, sha256_hex(content), "application/json",
                       "application/json", None, None, datetime.now(timezone.utc))

    def parse(self, item: DiscoveredItem, snapshot: Fetched) -> ParseResult:
        data = json.loads(snapshot.content)
        if self.schema_broken:
            return ParseResult(issues=[Issue("MISSING_FIELD", "falta 'titulo'", IssueSeverity.SCHEMA_CHANGE)])
        ref = item.external_id or ""
        return ParseResult(candidates=[
            NormalizedCandidate("project", ref, "title", {"text": data["titulo"]}, value_raw=data["titulo"],
                                record_pointer="/titulo"),
            NormalizedCandidate("project", ref, "status", {"raw": data["estado"]}, value_raw=data["estado"],
                                effective=parse_spanish_date(data["fecha_estado"]), record_pointer="/estado"),
        ])


def _records() -> dict[str, dict[str, Any]]:
    return {
        "249-2024-S": {"titulo": "Por medio de la cual se regula X", "estado": "Radicado", "fecha_estado": "2024-08-01"},
        "250-2024-S": {"titulo": "Por medio de la cual se crea Y", "estado": "Primer debate", "fecha_estado": "marzo de 2025"},
        "12-2024-C": {"titulo": "Reforma Z", "estado": "Archivado", "fecha_estado": "2025"},
    }


def _counts(db: Db, source_id: UUID) -> dict[str, int]:
    return db.execute(
        """
        select
          (select count(*) from public.source_records where source_id = %(s)s) as records,
          (select count(*) from public.source_snapshots sn join public.source_records r on r.id = sn.record_id
            where r.source_id = %(s)s) as snapshots,
          (select count(*) from public.observations where source_id = %(s)s) as observations,
          (select count(*) from public.evidence e join public.source_snapshots sn on sn.id = e.snapshot_id
             join public.source_records r on r.id = sn.record_id where r.source_id = %(s)s) as evidence
        """,
        {"s": source_id},
    )[0]


@pytest.fixture()
def setup(db: Db, tmp_path: Path):
    admin, ingest = db.create_user("admin"), db.create_user("ingest_service")
    source_id = active_source(db, admin)
    store = IngestStore(db.rpc(ingest))
    objects = LocalObjectStore(tmp_path / "objects")
    return source_id, store, objects


def _runner(source_id: UUID, store: IngestStore, objects: LocalObjectStore, connector: FakeConnector) -> IngestionRunner:
    return IngestionRunner(source_id=source_id, connector=connector, store=store, objects=objects, profile=ALLOWED)


def test_repeated_identical_loads_create_no_duplicates(db: Db, setup) -> None:
    source_id, store, objects = setup
    connector = FakeConnector(_records(), [["249-2024-S", "250-2024-S"], ["12-2024-C"]])
    first = _runner(source_id, store, objects, connector).run(mode="incremental", scope={"corporation": "senado"})
    assert (first.status, first.pages, first.new_snapshots, first.observations_created) == ("succeeded", 2, 3, 6)
    baseline = _counts(db, source_id)

    for _ in range(2):
        again = _runner(source_id, store, objects, connector).run(mode="incremental", scope={"corporation": "senado"})
        assert (again.new_snapshots, again.unchanged, again.observations_created) == (0, 3, 0)
    assert _counts(db, source_id) == baseline == {"records": 3, "snapshots": 3, "observations": 6, "evidence": 6}

    runs = db.execute("select status, cursor_after from public.ingestion_runs where source_id = %s", (source_id,))
    assert len(runs) == 3 and all(r["status"] == "succeeded" for r in runs)
    assert all(r["cursor_after"]["position"] == {"page": 2} for r in runs)
    published = db.execute("select count(*) as n from public.observations where source_id = %s and status = 'published'",
                           (source_id,))
    assert published[0]["n"] == 6


def test_changed_bytes_keep_history_and_revert_is_detected(db: Db, setup) -> None:
    source_id, store, objects = setup
    records = _records()
    connector = FakeConnector(records, [["249-2024-S"]])
    _runner(source_id, store, objects, connector).run(mode="incremental", scope={})

    records["249-2024-S"] = {**records["249-2024-S"], "estado": "Aprobado en primer debate", "fecha_estado": "2025-04-10"}
    changed = _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    assert changed.new_snapshots == 1 and changed.observations_created == 1 and changed.observations_seen == 1

    statuses = db.execute(
        "select value_json->>'raw' as raw from public.observations where source_id = %s and predicate = 'status' "
        "order by effective_date", (source_id,))
    assert [s["raw"] for s in statuses] == ["Radicado", "Aprobado en primer debate"]

    records["249-2024-S"] = _records()["249-2024-S"]
    _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    checks = db.execute(
        "select c.result from public.source_checks c join public.source_records r on r.id = c.record_id "
        "where r.source_id = %s order by c.seq", (source_id,))
    assert [c["result"] for c in checks] == ["new_snapshot", "new_snapshot", "reverted"]
    assert _counts(db, source_id)["snapshots"] == 2


def test_schema_change_quarantines_and_opens_single_review_case(db: Db, setup) -> None:
    source_id, store, objects = setup
    connector = FakeConnector(_records(), [["249-2024-S", "250-2024-S"]], schema_broken=True)
    summary = _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    assert summary.status == "partial" and summary.quarantined == 2
    cases = db.execute("select case_type, state from public.review_cases where dedupe_key like %s",
                       (f"schema:{source_id}:%",))
    assert cases == [{"case_type": "schema", "state": "open"}]
    run = db.execute("select items_quarantined, status from public.ingestion_runs where source_id = %s", (source_id,))[0]
    assert run == {"items_quarantined": 2, "status": "partial"}


def test_auth_failure_suspends_source_and_is_audited(db: Db, setup) -> None:
    source_id, store, objects = setup
    connector = FakeConnector(_records(), [["249-2024-S", "250-2024-S"]], failures={
        "250-2024-S": FetchError("HTTP_401", "credencial revocada", kind=FailureKind.AUTH, status=401),
    })
    summary = _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    assert summary.status == "failed"
    source = db.execute("select state, state_reason from public.sources where id = %s", (source_id,))[0]
    assert source["state"] == "suspended" and "HTTP_401" in source["state_reason"]
    audit = db.execute("select action from public.audit_log where target_id = %s order by id desc limit 1", (str(source_id),))
    assert audit[0]["action"] == "source.auto_suspended"
    run = db.execute("select status, error_code, cursor_after from public.ingestion_runs where source_id = %s",
                     (source_id,))[0]
    assert run == {"status": "failed", "error_code": "SOURCE_SUSPENDED", "cursor_after": None}


def test_not_found_is_not_a_withdrawal(db: Db, setup) -> None:
    source_id, store, objects = setup
    records = _records()
    connector = FakeConnector(records, [["249-2024-S"]])
    _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    connector.failures["249-2024-S"] = FetchError("HTTP_404", "no encontrado", kind=FailureKind.NOT_FOUND, status=404)
    _runner(source_id, store, objects, connector).run(mode="incremental", scope={})
    record = db.execute("select withdrawn_at from public.source_records where source_id = %s", (source_id,))[0]
    assert record["withdrawn_at"] is None
    last = db.execute(
        "select c.result from public.source_checks c join public.source_records r on r.id = c.record_id "
        "where r.source_id = %s order by c.seq desc limit 1", (source_id,))[0]
    assert last["result"] == "not_found"


def test_stored_originals_are_content_addressed(db: Db, setup) -> None:
    source_id, store, objects = setup
    _runner(source_id, store, objects, FakeConnector(_records(), [["249-2024-S"]])).run(mode="incremental", scope={})
    snapshot = db.execute(
        "select sn.object_key, sn.content_hash from public.source_snapshots sn "
        "join public.source_records r on r.id = sn.record_id where r.source_id = %s", (source_id,))[0]
    assert snapshot["object_key"].endswith(snapshot["content_hash"])
    assert sha256_hex(objects.get(snapshot["object_key"])) == snapshot["content_hash"]
