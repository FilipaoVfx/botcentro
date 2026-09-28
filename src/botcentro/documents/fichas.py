"""Fichas de proyecto de SRC-06 como documentos de texto indexados (SRS §8, SRS-F14; PRD-05).

Cada ficha publicada del listado de la Cámara (título, objeto, observaciones, tipo, comisiones,
legislatura, autores) se convierte en un documento `otro` con una sola página y sus chunks con la
receta de e5-small. El registro es atómico por lote en la base (`ingest_register_text_documents`);
los vectores se publican después en Qdrant (DEC-12) con el id del chunk, de forma idempotente.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from botcentro.costs.budget import BudgetGuard
from botcentro.documents.chunking import Chunk, PageText, chunk_extraction
from botcentro.domain.hashing import sha256_hex
from botcentro.embeddings.e5 import (DIMENSIONS, E5_SMALL_RECIPE, INDEX_NAMESPACE, MAX_INPUT_TOKENS, MODEL_ID,
                                     MODEL_VERSION, PassageVector)
from botcentro.embeddings.indexer import TruncatedPassage
from botcentro.insforge.client import RpcClient, as_rows
from botcentro.vectors.qdrant import QdrantStore, chunk_payload

EXTRACTOR_VERSION = "ficha-text-v1"
EXTRACTION_NAMESPACE = uuid.UUID("0c3f6a1e-2b7d-4f55-9a61-5e0d2c7b8a94")
_FIELDS = (
    ("Proyecto", "short_name"),
    ("Título", "title"),
    ("Objeto", "object"),
    ("Observaciones", "observations"),
    ("Tipo", "law_type"),
    ("Origen", "origin"),
    ("Comisiones", "commissions"),
    ("Legislatura", "legislature"),
    ("Radicación en Cámara", "camara_raw"),
    ("Radicación en Senado", "senado_raw"),
    ("Autores", "authors_raw"),
)
CONFIG_HASH = sha256_hex(json.dumps([EXTRACTOR_VERSION, [k for _, k in _FIELDS]]).encode())


class Embedder(Protocol):
    def passage_measure(self, text: str) -> int: ...
    def embed_passages(self, chunks: Sequence[Chunk]) -> list[PassageVector]: ...


def ficha_text(value: Mapping[str, Any]) -> str:
    """Texto legible de la ficha; se omiten campos vacíos y no se inventa nada."""
    lines = []
    for label, key in _FIELDS:
        text = " ".join(str(value.get(key) or "").split())
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def build_item(row: Mapping[str, Any], embedder: Embedder) -> tuple[dict[str, Any], list[Chunk]] | None:
    value = row["value_json"]
    text = ficha_text(value)
    if not text:
        return None
    body = text.encode()
    content_hash = sha256_hex(body)
    document_key = f"camara-ficha:{row['subject_ref']}"
    extraction_id = uuid.uuid5(EXTRACTION_NAMESPACE, f"{document_key}:{content_hash}:{EXTRACTOR_VERSION}:{CONFIG_HASH}")
    chunks = chunk_extraction(extraction_id, [PageText(1, text)], recipe=E5_SMALL_RECIPE,
                              measure=embedder.passage_measure, max_measure=MAX_INPUT_TOKENS)
    item = {
        "document_key": document_key,
        "document_type": "otro",
        "title": (value.get("short_name") or value.get("title") or row["subject_ref"])[:500],
        "content_hash": content_hash,
        "byte_size": len(body),
        "published_on": value.get("filed_camara") or value.get("filed_senado") or row.get("effective_date"),
        "snapshot_id": str(row["snapshot_id"]),
        "source_id": str(row["source_id"]),
        "source_url": value.get("link") or "",
        "observation_id": str(row["observation_id"]),
        "project_subject_ref": row["subject_ref"],
        "extraction_id": str(extraction_id),
        "extractor_version": EXTRACTOR_VERSION,
        "config_hash": CONFIG_HASH,
        "text": text,
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "dimensions": DIMENSIONS,
        "index_namespace": INDEX_NAMESPACE,
        "chunks": [{
            "id": str(c.id), "ordinal": c.ordinal, "text": c.text, "text_hash": c.text_hash,
            "chunker_version": c.chunker_version, "token_count": c.token_count, "char_start": c.char_start,
            "char_end": c.char_end, "section_label": c.section_label, "heading": c.heading,
        } for c in chunks],
    }
    return item, chunks


class FichaIndexer:
    def __init__(self, rpc: RpcClient, embedder: Embedder, budget: BudgetGuard, vectors: QdrantStore, *,
                 batch: int = 100) -> None:
        self.rpc = rpc
        self.embedder = embedder
        self.budget = budget
        self.vectors = vectors
        self.batch = batch

    def run_batch(self) -> dict[str, int] | None:
        """Procesa un lote de fichas pendientes; devuelve None cuando no queda ninguna."""
        rows = self.rpc.call("ingest_pending_camara_fichas", {"p_limit": self.batch})
        if not rows:
            return None
        built = [b for b in (build_item(r, self.embedder) for r in rows) if b]
        chunks = [c for _, cs in built for c in cs]
        with self.budget.spend("local", "embedding_e5_small", "0", units=len(chunks)) as reservation:
            vectors = {v.chunk_id: v for v in self.embedder.embed_passages(chunks)} if chunks else {}
            if any(v.truncated for v in vectors.values()):
                raise TruncatedPassage("hay pasajes de ficha que exceden el límite del modelo")
            result = self.rpc.call("ingest_register_text_documents", {"p_items": [i for i, _ in built]})
            reservation.settle(0)
        counts = dict(as_rows(result)[0])
        ids = list(vectors)
        projects = {str(r["chunk_id"]): r["project_ids"] or []
                    for r in as_rows(self.rpc.call("ingest_chunk_projects", {"p_chunk_ids": ids}))} if ids else {}
        points = []
        for item, _ in built:
            for c in item["chunks"]:
                points.append({"id": c["id"], "vector": vectors[c["id"]].vector, "payload": chunk_payload({
                    "chunk_id": c["id"], "text": c["text"], "heading": c["heading"],
                    "section_label": c["section_label"], "document_key": item["document_key"],
                    "document_type": item["document_type"], "title": item["title"],
                    "published_on": item["published_on"], "source_url": item["source_url"], "source_code": "SRC-06",
                    "project_ids": projects.get(c["id"], []),
                }, doc_kind="ficha")})
        counts["vectors"] = self.vectors.upsert(points)
        return {**counts, "without_text": len(rows) - len(built)}
