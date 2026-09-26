"""Embeddings locales con multilingual-e5-small (DEC-06, docs/embeddings.md).

* e5 exige prefijos: «query: » para consultas y «passage: » para documentos.
* El modelo trunca a 512 tokens. La receta de chunks (E5_SMALL_RECIPE) se calibró con su
  tokenizador sobre texto legislativo real (1,44 tokens de e5 por token del chunker). Aun así,
  cada pasaje se mide y el truncado se informa, nunca se ignora en silencio.
* Corre en CPU con ONNX (FastEmbed), sin coste por token. El gasto se registra igualmente en el
  libro de consumo con costo 0 para mantener la trazabilidad (SRS-N17).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from botcentro.documents.chunking import Chunk, ChunkRecipe

MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_VERSION = "onnx-614241f"
DIMENSIONS = 384
MAX_INPUT_TOKENS = 512
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
INDEX_NAMESPACE = "e5-small-v1"

# 280 tokens del chunker ≈ 400 tokens de e5 de media; el texto denso (nombres, cifras) llega a ~1,9
# tokens por token, así que el margen evita truncar. Medido sobre SRC-01 (docs/embeddings.md).
E5_SMALL_RECIPE = ChunkRecipe(chunker_version="chunker-v2-e5s", min_tokens=180, max_tokens=280, overlap_tokens=40)


class TokenCounter(Protocol):
    def __call__(self, text: str) -> int: ...


@dataclass(frozen=True)
class PassageVector:
    chunk_id: str
    vector: list[float]
    input_tokens: int
    truncated: bool


def _cache_dir() -> str:
    return os.environ.get("BOTCENTRO_MODEL_CACHE", "var/models")


class E5SmallEmbedder:
    """Carga perezosa del modelo; `threads` debe ajustarse a las CPU del worker."""

    def __init__(self, *, threads: int | None = None, cache_dir: str | None = None, batch_size: int = 16) -> None:
        self._threads = threads
        self._cache_dir = cache_dir or _cache_dir()
        self._batch_size = batch_size
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        if not any(m["model"] == MODEL_ID for m in TextEmbedding.list_supported_models()):
            TextEmbedding.add_custom_model(
                model=MODEL_ID,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=MODEL_ID),
                dim=DIMENSIONS,
                model_file="onnx/model.onnx",
                additional_files=["onnx/sentencepiece.bpe.model"],
            )
        self._model = TextEmbedding(MODEL_ID, threads=self._threads, cache_dir=self._cache_dir)
        self._tokenizer = self._model.model.tokenizer  # tokenizador real del modelo

    def count_tokens(self, text: str) -> int:
        self._load()
        assert self._tokenizer is not None
        self._tokenizer.no_truncation()
        try:
            return len(self._tokenizer.encode(text).ids)
        finally:
            self._tokenizer.enable_truncation(max_length=MAX_INPUT_TOKENS)

    def passage_measure(self, text: str) -> int:
        """Tokens del pasaje tal como se embebe (con prefijo): úsese como `measure` del chunker."""
        return self.count_tokens(PASSAGE_PREFIX + text)

    def embed_query(self, text: str) -> list[float]:
        self._load()
        assert self._model is not None
        vector = next(iter(self._model.embed([QUERY_PREFIX + text])))
        return _normalize(vector)

    def embed_passages(self, chunks: Sequence[Chunk]) -> list[PassageVector]:
        self._load()
        assert self._model is not None
        inputs = [PASSAGE_PREFIX + c.embedding_input for c in chunks]
        vectors = self._model.embed(inputs, batch_size=self._batch_size)
        result = []
        for chunk, text, vector in zip(chunks, inputs, vectors):
            tokens = self.count_tokens(text)
            result.append(PassageVector(str(chunk.id), _normalize(vector), tokens, tokens > MAX_INPUT_TOKENS))
        return result


def _normalize(vector: Iterable[float]) -> list[float]:
    array = np.asarray(list(vector), dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        raise ValueError("vector nulo: el texto no produjo representación")
    if array.shape[0] != DIMENSIONS:
        raise ValueError(f"dimensión {array.shape[0]} distinta de {DIMENSIONS}")
    return (array / norm).astype(float).tolist()
