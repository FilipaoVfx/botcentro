"""Sincroniza con Qdrant los chunks ya registrados en la base (DEC-12).

Recorre `ingest_export_chunks` por id y publica cada chunk con su vector. Si la base aún guarda
el vector (cargas previas a DEC-12) se reutiliza; si no, se calcula con e5-small. Es idempotente:
el id del punto es el id del chunk.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from botcentro.documents.chunking import Chunk
from botcentro.embeddings.e5 import PassageVector
from botcentro.insforge.client import RpcClient, as_rows
from botcentro.vectors.qdrant import QdrantStore, chunk_payload, parse_vector

_KIND_BY_PREFIX = {"camara-ficha:": "ficha", "gaceta:": "gaceta"}


def doc_kind(document_key: str) -> str:
    return next((kind for prefix, kind in _KIND_BY_PREFIX.items() if document_key.startswith(prefix)), "otro")


def sync_chunks(rpc: RpcClient, store: QdrantStore, *, batch: int = 500,
                embed_missing: Callable[[Sequence[dict[str, Any]]], list[PassageVector]] | None = None,
                progress: Callable[[dict[str, int]], None] | None = None) -> dict[str, int]:
    totals = {"exported": 0, "published": 0, "missing_vector": 0}
    after: str | None = None
    while True:
        rows = as_rows(rpc.call("ingest_export_chunks", {"p_after": after, "p_limit": batch}))
        if not rows:
            return totals
        after = str(rows[-1]["chunk_id"])
        vectors = {str(r["chunk_id"]): parse_vector(r["embedding"]) for r in rows if r.get("embedding")}
        missing = [r for r in rows if not r.get("embedding")]
        if missing and embed_missing:
            for v in embed_missing(missing):
                vectors[v.chunk_id] = v.vector
        points = [{"id": str(r["chunk_id"]), "vector": vectors[str(r["chunk_id"])],
                   "payload": chunk_payload(r, doc_kind=doc_kind(r["document_key"]))}
                  for r in rows if str(r["chunk_id"]) in vectors]
        totals["exported"] += len(rows)
        totals["published"] += store.upsert(points)
        totals["missing_vector"] += len(rows) - len(points)
        if progress:
            progress(totals)


def as_chunk(row: dict[str, Any]) -> Chunk:
    """Chunk mínimo para volver a embeber una fila exportada (solo se usan id, texto y encabezado)."""
    from uuid import UUID

    from botcentro.domain.enums import QualityStatus
    return Chunk(id=UUID(str(row["chunk_id"])), ordinal=0, text=row["text"], text_hash="", token_count=1,
                 pdf_page_start=1, pdf_page_end=1, char_start=0, char_end=len(row["text"]),
                 section_label=row.get("section_label"), heading=row.get("heading"), segment_id=None,
                 quality_status=QualityStatus.ACCEPTED, chunker_version="")


_DROPPED_FIELDS = ("chunk_id", "heading", "section_label", "extractor_version")


def migrate_collection(source: QdrantStore, target: QdrantStore, *, skip_segment_kinds: Sequence[str] = (),
                       progress: Callable[[dict[str, int]], None] | None = None) -> dict[str, int]:
    """Copia puntos entre colecciones (p. ej. a v2 en float16, DEC-14) recortando la carga útil y
    omitiendo los segmentos de gaceta excluidos. Idempotente: el id del punto se conserva."""
    skip = frozenset(skip_segment_kinds)
    totals = {"read": 0, "copied": 0, "skipped": 0}
    offset: Any = None
    while True:
        points, offset = source.scroll(offset=offset, limit=256)
        batch = []
        for point in points:
            payload = {k: v for k, v in point["payload"].items() if k not in _DROPPED_FIELDS}
            if payload.get("segment_kind") in skip:
                totals["skipped"] += 1
                continue
            batch.append({"id": point["id"], "vector": point["vector"], "payload": payload})
        totals["read"] += len(points)
        totals["copied"] += target.upsert(batch)
        if progress:
            progress(totals)
        if offset is None:
            return totals
