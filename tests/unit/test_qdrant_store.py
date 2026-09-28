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
    assert create["vectors"] == {"size": 384, "distance": "Cosine", "on_disk": True}
    assert create["quantization_config"]["scalar"]["type"] == "int8" and create["on_disk_payload"]
    indexed = {c[2]["field_name"] for c in calls if c[1].endswith("/index")}
    assert {"project_ids", "document_key", "year"} <= indexed


def test_wrong_dimensions_are_rejected_before_sending() -> None:
    calls: list = []
    with pytest.raises(QdrantError):
        _store(calls).upsert([{"id": "a", "vector": [0.1, 0.2], "payload": {}}])
    assert not calls


def test_payload_and_vector_parsing() -> None:
    assert parse_vector("[0.5,-1,2e-3]") == [0.5, -1.0, 0.002]
    payload = chunk_payload({"chunk_id": "c", "text": "t", "document_key": "gaceta:senado:2026:1382",
                             "published_on": "2026-09-24", "project_ids": ["p"]}, doc_kind="gaceta")
    assert payload["year"] == 2026 and payload["project_ids"] == ["p"] and payload["doc_kind"] == "gaceta"
