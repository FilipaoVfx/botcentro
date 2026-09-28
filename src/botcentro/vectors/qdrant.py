"""Índice vectorial de documentos en Qdrant (DEC-12; autoalojado en el servidor del bot, DEC-15).

La base de InsForge conserva documentos, chunks citables y enlaces a proyectos; Qdrant guarda el
vector de cada chunk con una carga mínima para filtrar y citar sin volver a la base. El id del
punto es el id del chunk (uuid5 determinista), así que publicar dos veces es idempotente.

Configuración pensada para poca memoria: vectores originales en disco en float16, copia
cuantizada int8 en RAM y carga útil en disco y mínima (~2,25 KB de vectores más carga útil por
punto, DEC-14). El mismo cliente sirve para Qdrant Cloud y para la instancia local.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from botcentro.embeddings.e5 import DIMENSIONS, INDEX_NAMESPACE
from botcentro.errors import BotcentroError

COLLECTION = f"botcentro-docs-{INDEX_NAMESPACE}-v2"
LEGACY_COLLECTION = f"botcentro-docs-{INDEX_NAMESPACE}"
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
                "vectors": {"size": DIMENSIONS, "distance": "Cosine", "on_disk": True, "datatype": "float16"},
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

    def upsert(self, points: Sequence[Mapping[str, Any]], *, batch: int = 256) -> int:
        """Publica puntos {id, vector, payload} por lotes; espera confirmación para no perder datos."""
        for point in points:
            if len(point["vector"]) != DIMENSIONS:
                raise QdrantError(f"el vector de {point['id']} no tiene {DIMENSIONS} dimensiones")
        for start in range(0, len(points), batch):
            self._request("PUT", f"/collections/{self.collection}/points?wait=true",
                          {"points": list(points[start:start + batch])})
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

    def scroll(self, *, offset: Any = None, limit: int = 256) -> tuple[list[dict[str, Any]], Any]:
        """Página de puntos con vector y carga útil; devuelve (puntos, siguiente desplazamiento)."""
        body: dict[str, Any] = {"limit": limit, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        result = self._request("POST", f"/collections/{self.collection}/points/scroll", body)
        return list(result["points"]), result.get("next_page_offset")

    def stored_bytes(self) -> int:
        """Bytes de vectores y carga útil de la colección según la telemetría (sin índices)."""
        telemetry = self._request("GET", "/telemetry?details_level=3")
        for collection in telemetry.get("collections", {}).get("collections", []):
            if isinstance(collection, dict) and collection.get("id") == self.collection:
                return sum((shard.get("local") or {}).get("vectors_size_bytes", 0)
                           + (shard.get("local") or {}).get("payloads_size_bytes", 0)
                           for shard in collection.get("shards", []))
        return 0


def parse_vector(literal: str) -> list[float]:
    """Vector en texto de pgvector ('[0.1,0.2,...]') → lista de floats."""
    return [float(x) for x in literal.strip("[]").split(",")]


def chunk_payload(row: Mapping[str, Any], *, doc_kind: str) -> dict[str, Any]:
    """Carga útil mínima: el id del punto ya es el del chunk y el encabezado solo sirve al embeber."""
    published = row.get("published_on")
    return {
        "text": row["text"],
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
