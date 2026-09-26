"""Publicación de embeddings en chunk_embeddings (SRS §8 paso 9, SRS-N17).

Solo se embebe contenido autorizado y aceptado: el llamante filtra por perfil de uso
(`index_content`) y calidad antes de invocar. El coste local se registra como 0 en el libro de
consumo, bajo el proveedor `local`, para que el panel muestre el volumen procesado.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Protocol

from botcentro.costs.budget import BudgetGuard
from botcentro.documents.chunking import Chunk
from botcentro.domain.enums import QualityStatus
from botcentro.embeddings.e5 import DIMENSIONS, INDEX_NAMESPACE, MODEL_ID, MODEL_VERSION, PassageVector


class RecordsClient(Protocol):
    def insert(self, table: str, rows: Sequence[dict], *, returning: bool = True) -> list[dict]: ...


class PassageEmbedder(Protocol):
    def embed_passages(self, chunks: Sequence[Chunk]) -> list[PassageVector]: ...


class TruncatedPassage(ValueError):
    """Un pasaje excede el límite del modelo: se rehace el chunking, no se indexa truncado."""


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


class EmbeddingIndexer:
    def __init__(self, embedder: PassageEmbedder, records: RecordsClient, budget: BudgetGuard) -> None:
        self.embedder = embedder
        self.records = records
        self.budget = budget

    def index(self, chunks: Sequence[Chunk]) -> int:
        eligible = [c for c in chunks if c.quality_status is QualityStatus.ACCEPTED]
        if not eligible:
            return 0
        with self.budget.spend("local", "embedding_e5_small", "0", units=len(eligible)) as reservation:
            vectors = self.embedder.embed_passages(eligible)
            truncated = [v.chunk_id for v in vectors if v.truncated]
            if truncated:
                raise TruncatedPassage(f"{len(truncated)} pasajes exceden el límite del modelo")
            now = datetime.now(timezone.utc)
            rows = [{
                "chunk_id": v.chunk_id,
                "model_id": MODEL_ID,
                "model_version": MODEL_VERSION,
                "dimensions": DIMENSIONS,
                "index_namespace": INDEX_NAMESPACE,
                "embedding": vector_literal(v.vector),
                "state": "indexed",
                "indexed_at": now,
            } for v in vectors]
            self.records.insert("chunk_embeddings", rows, returning=False)
            reservation.settle(0)
        return len(rows)
