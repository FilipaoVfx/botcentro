"""Huellas de contenido y claves idempotentes (SRS-F04, F12, N11)."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Any, BinaryIO

_CHUNK = 1024 * 1024


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO | Iterable[bytes]) -> tuple[str, int]:
    """SHA-256 y tamaño de un flujo, sin cargarlo completo en memoria."""
    digest = hashlib.sha256()
    size = 0
    chunks: Iterable[bytes] = iter(lambda: stream.read(_CHUNK), b"") if hasattr(stream, "read") else stream  # type: ignore[union-attr, arg-type]
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_normalize(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    """JSON determinista: claves ordenadas, sin espacios y cadenas en NFC."""
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def idempotency_key(*parts: Any) -> str:
    """Clave estable para trabajos y registros derivados a partir de sus componentes."""
    return sha256_hex(canonical_json(list(parts)).encode("utf-8"))
