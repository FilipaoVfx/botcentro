"""Índice vectorial de documentos en Qdrant Cloud (DEC-12).

La base de InsForge conserva documentos, chunks citables y enlaces a proyectos; Qdrant guarda el
vector de cada chunk con una carga mínima para filtrar y citar sin volver a la base. El id del
punto es el id del chunk (uuid5 determinista), así que publicar dos veces es idempotente.

El plan gratuito (1 GB de RAM, 4 GB de disco) obliga a: vectores originales en disco, copia
cuantizada int8 en RAM y carga útil en disco. Con 384 dimensiones cabe ~1 M de chunks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from botcentro.embeddings.e5 import DIMENSIONS, INDEX_NAMESPACE
from botcentro.errors import BotcentroError

COLLECTION = f"botcentro-docs-{INDEX_NAMESPACE}"
_KEYWORD_FIELDS = ("document_key", "document_type", "source_code", "project_ids", "doc_kind", "segment_kind")
_INTEGER_FIELDS = ("year",)


class QdrantError(BotcentroError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__("QDRANT_ERROR", message)
        self.status = status


class QdrantStore:
    def __init__(self, url: str, api_key: str, *, collection: str = COLLECTION, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        base = url.rstrip("/")
        if base.count(":") < 2:  # sin puerto explícito: la API REST de Qdrant Cloud usa 6333
            base += ":6333"
        self.collection = collection
        self._http = httpx.Client(base_url=base, headers={"api-key": api_key}, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        response = self._http.request(method, path, json=body)
        if response.status_code >= 400:
            raise QdrantError(f"{method} {path}: {response.text[:300]}", status=response.status_code)
        return response.json().get("result")

    def ensure_collection(self) -> bool:
        """Crea la colección si no existe y asegura sus índices de carga útil (idempotente).
        Devuelve True si la creó."""
        existing = self._request("GET", "/collections")
        created = not any(c["name"] == self.collection for c in existing["collections"])
        if created:
            self._request("PUT", f"/collections/{self.collection}", {
                "vectors": {"size": DIMENSIONS, "distance": "Cosine", "on_disk": True},
                "quantization_config": {"scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}},
                "on_disk_payload": True,
                "hnsw_config": {"m": 16, "ef_construct": 100},
            })
        schema = self.info().get("payload_schema", {}) if not created else {}
        for field in (f for f in _KEYWORD_FIELDS if f not in schema):
            self._request("PUT", f"/collections/{self.collection}/index?wait=true",
                          {"field_name": field, "field_schema": "keyword"})
        for field in (f for f in _INTEGER_FIELDS if f not in schema):
            self._request("PUT", f"/collections/{self.collection}/index?wait=true",
                          {"field_name": field, "field_schema": "integer"})
        return created

    def upsert(self, points: Sequence[Mapping[str, Any]]) -> int:
        """Publica puntos {id, vector, payload}; espera confirmación para no perder lotes."""
        if not points:
            return 0
        for point in points:
            if len(point["vector"]) != DIMENSIONS:
                raise QdrantError(f"el vector de {point['id']} no tiene {DIMENSIONS} dimensiones")
        self._request("PUT", f"/collections/{self.collection}/points?wait=true", {"points": list(points)})
        return len(points)

    def count(self, flt: Mapping[str, Any] | None = None) -> int:
        body: dict[str, Any] = {"exact": True}
        if flt:
            body["filter"] = flt
        return int(self._request("POST", f"/collections/{self.collection}/points/count", body)["count"])

    def search(self, vector: Sequence[float], *, limit: int = 8, flt: Mapping[str, Any] | None = None,
               with_payload: bool | Sequence[str] = True) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": list(vector), "limit": limit, "with_payload": with_payload,
                                "params": {"quantization": {"rescore": True}}}
        if flt:
            body["filter"] = flt
        return list(self._request("POST", f"/collections/{self.collection}/points/query", body)["points"])

    def info(self) -> dict[str, Any]:
        return dict(self._request("GET", f"/collections/{self.collection}"))


def parse_vector(literal: str) -> list[float]:
    """Vector en texto de pgvector ('[0.1,0.2,...]') → lista de floats."""
    return [float(x) for x in literal.strip("[]").split(",")]


def chunk_payload(row: Mapping[str, Any], *, doc_kind: str) -> dict[str, Any]:
    published = row.get("published_on")
    return {
        "chunk_id": str(row["chunk_id"]),
        "text": row["text"],
        "heading": row.get("heading"),
        "section_label": row.get("section_label"),
        "document_key": row["document_key"],
        "document_type": row.get("document_type"),
        "doc_kind": doc_kind,
        "title": row.get("title"),
        "published_on": str(published) if published else None,
        "year": int(str(published)[:4]) if published else None,
        "source_url": row.get("source_url"),
        "source_code": row.get("source_code"),
        "project_ids": [str(p) for p in row.get("project_ids") or []],
    }
