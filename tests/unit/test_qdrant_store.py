"""Cliente de Qdrant (DEC-12): creación de colección, validación de dimensiones y carga útil."""

import json

import httpx
import pytest

from botcentro.vectors.qdrant import COLLECTION, QdrantError, QdrantStore, chunk_payload, parse_vector


def _store(calls: list) -> QdrantStore:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content or b"null")))
        assert request.headers["api-key"] == "k"
        if request.url.path == "/collections" and request.method == "GET":
            return httpx.Response(200, json={"result": {"collections": []}})
        return httpx.Response(200, json={"result": {"count": 1}})
    return QdrantStore("https://x.cloud.qdrant.io", "k", transport=httpx.MockTransport(handler))


def test_collection_is_quantized_on_disk_with_payload_indexes() -> None:
    calls: list = []
    assert _store(calls).ensure_collection()
    create = next(c for c in calls if c[0] == "PUT" and c[1] == f"/collections/{COLLECTION}")[2]
    assert create["vectors"] == {"size": 384, "distance": "Cosine", "on_disk": True, "datatype": "float16"}
    assert create["quantization_config"]["scalar"]["type"] == "int8" and create["on_disk_payload"]
    indexed = {c[2]["field_name"] for c in calls if c[1].endswith("/index")}
    assert {"project_ids", "document_key", "year", "segment_kind"} <= indexed


def test_wrong_dimensions_are_rejected_before_sending() -> None:
    calls: list = []
    with pytest.raises(QdrantError):
        _store(calls).upsert([{"id": "a", "vector": [0.1, 0.2], "payload": {}}])
    assert not calls


def test_payload_and_vector_parsing() -> None:
    assert parse_vector("[0.5,-1,2e-3]") == [0.5, -1.0, 0.002]
    payload = chunk_payload({"text": "t", "document_key": "gaceta:senado:2026:1382",
                             "published_on": "2026-09-24", "project_ids": ["p"]}, doc_kind="gaceta")
    assert payload["year"] == 2026 and payload["project_ids"] == ["p"] and payload["doc_kind"] == "gaceta"
    assert "chunk_id" not in payload and "heading" not in payload  # carga mínima (DEC-14)


def test_upsert_is_sent_in_batches() -> None:
    calls: list = []
    store = _store(calls)
    points = [{"id": str(i), "vector": [0.0] * 384, "payload": {}} for i in range(600)]
    assert store.upsert(points, batch=256) == 600
    assert [len(c[2]["points"]) for c in calls] == [256, 256, 88]


class MemoryStore:
    def __init__(self, points=()) -> None:
        self.points = {p["id"]: p for p in points}

    def scroll(self, *, offset=None, limit=256):
        ids = sorted(self.points)
        start = offset or 0
        page = [self.points[i] for i in ids[start:start + limit]]
        return page, (start + limit if start + limit < len(ids) else None)

    def upsert(self, points, batch=256):
        for p in points:
            self.points[p["id"]] = p
        return len(points)


def test_migration_trims_payload_and_skips_excluded_segments() -> None:
    from botcentro.vectors.sync import migrate_collection

    source = MemoryStore([
        {"id": f"{i:03d}", "vector": [0.1] * 384,
         "payload": {"text": "t", "chunk_id": "x", "heading": "h", "extractor_version": "v",
                     "segment_kind": "portada" if i % 3 == 0 else "ponencia"}}
        for i in range(300)])
    target = MemoryStore()
    totals = migrate_collection(source, target, skip_segment_kinds=["portada"])  # type: ignore[arg-type]
    assert totals == {"read": 300, "copied": 200, "skipped": 100}
    assert all(set(p["payload"]) == {"text", "segment_kind"} for p in target.points.values())
