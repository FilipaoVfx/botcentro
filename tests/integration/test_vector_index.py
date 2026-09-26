"""Base vectorial e5-small: registro de modelos, integridad de dimensiones e índice HNSW (DEC-05/06)."""

from __future__ import annotations

import random

import psycopg
import pytest

from tests.integration.support import Db

pytestmark = pytest.mark.db
MODEL = "intfloat/multilingual-e5-small"
VERSION = "onnx-614241f"


def _vec(seed: int) -> list[float]:
    rng = random.Random(seed)
    v = [rng.uniform(-1, 1) for _ in range(384)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def _lit(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _chunks(db: Db, n: int) -> list:
    blob = db.execute("insert into public.blobs (content_hash, byte_size) values (repeat('f', 64), 1) "
                      "on conflict do nothing returning content_hash")
    doc = db.execute("insert into public.documents (document_key, document_type) values (gen_random_uuid()::text, "
                     "'ponencia') returning id")[0]["id"]
    rev = db.execute("insert into public.document_revisions (document_id, blob_hash, mime_type, byte_size) "
                     "values (%s, repeat('f', 64), 'application/pdf', 1) returning id", (doc,))[0]["id"]
    ext = db.execute("insert into public.extraction_runs (revision_id, extractor_version, config_hash, status, "
                     "quality_status) values (%s, 'x', 'c', 'succeeded', 'accepted') returning id", (rev,))[0]["id"]
    ids = []
    for i in range(n):
        ids.append(db.execute(
            "insert into public.chunks (id, extraction_id, ordinal, text, text_hash, chunker_version, token_count, "
            "pdf_page_start, pdf_page_end, char_start, char_end) values (gen_random_uuid(), %s, %s, %s, "
            "repeat('e', 64), 'chunker-v2-e5s', 10, 1, 1, 0, 10) returning id", (ext, i, f"texto {i}"))[0]["id"])
    return ids


def test_active_model_is_registered(db: Db) -> None:
    rows = db.execute("select model_id, dimensions, status, query_prefix, passage_prefix from public.embedding_models")
    assert rows == [{"model_id": MODEL, "dimensions": 384, "status": "active",
                     "query_prefix": "query: ", "passage_prefix": "passage: "}]


def test_wrong_dimensions_and_unknown_models_are_rejected(db: Db) -> None:
    chunk = _chunks(db, 1)[0]
    with pytest.raises(psycopg.Error, match="no coincide"):
        db.execute("insert into public.chunk_embeddings (chunk_id, model_id, model_version, dimensions, "
                   "index_namespace, embedding, state, indexed_at) values (%s, %s, %s, 3, 'x', '[1,0,0]', "
                   "'indexed', now())", (chunk, MODEL, VERSION))
    with pytest.raises(psycopg.Error, match="no registrado"):
        db.execute("insert into public.chunk_embeddings (chunk_id, model_id, model_version, dimensions, "
                   "index_namespace) values (%s, 'otro', '1', 384, 'x')", (chunk,))


def test_search_returns_nearest_and_uses_hnsw_index(db: Db) -> None:
    ids = _chunks(db, 40)
    ingest = db.create_user("ingest_service")
    for i, chunk in enumerate(ids):
        db.as_user(ingest, "insert into public.chunk_embeddings (chunk_id, model_id, model_version, dimensions, "
                           "index_namespace, embedding, state, indexed_at) values (%s, %s, %s, 384, 'e5-small-v1', "
                           "%s::vector, 'indexed', now())", (chunk, MODEL, VERSION, _lit(_vec(i))))
    query = db.rpc(db.create_user("query_service"))
    hits = query.call("search_chunks_e5_small", {"p_embedding": _vec(7), "p_limit": 5})
    assert hits[0]["chunk_id"] == str(ids[7]) and hits[0]["distance"] < 1e-5
    assert [h["distance"] for h in hits] == sorted(h["distance"] for h in hits)

    with db.admin() as conn:
        conn.execute("set enable_seqscan = off")
        plan = conn.execute(
            "explain select chunk_id from public.chunk_embeddings where model_id = %s and state = 'indexed' "
            "order by embedding::vector(384) <=> %s::vector(384) limit 5", (MODEL, _lit(_vec(1)))).fetchall()
    assert any("chunk_embeddings_e5_small_hnsw" in r["QUERY PLAN"] for r in plan)
