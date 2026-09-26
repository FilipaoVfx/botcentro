"""Almacenamiento de originales direccionado por contenido (SRS-F06, F12, §8 pasos 2–3).

La clave de objeto deriva del SHA-256: los mismos bytes comparten objeto físico aunque
lleguen por URLs distintas (la procedencia separada vive en la base). Tras escribir se
relee y se verifica el hash; una discrepancia es un error de integridad, no un aviso.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from botcentro.domain.hashing import sha256_hex
from botcentro.errors import BotcentroError, FailureKind


class IntegrityError(BotcentroError):
    kind = FailureKind.POLICY


def object_key_for(content_hash: str) -> str:
    if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
        raise ValueError("se esperaba un SHA-256 hexadecimal en minúsculas")
    return f"sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"


class ObjectStore(Protocol):
    def put(self, content: bytes, content_hash: str, mime_type: str | None) -> str: ...

    def get(self, object_key: str) -> bytes: ...

    def exists(self, object_key: str) -> bool: ...


class LocalObjectStore:
    """Implementación en disco para desarrollo y pruebas."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _path(self, object_key: str) -> Path:
        path = (self._root / object_key).resolve()
        if self._root.resolve() not in path.parents:
            raise ValueError("clave de objeto fuera del almacén")
        return path

    def put(self, content: bytes, content_hash: str, mime_type: str | None = None) -> str:
        if sha256_hex(content) != content_hash:
            raise IntegrityError("HASH_MISMATCH", "el contenido no coincide con el hash declarado")
        key = object_key_for(content_hash)
        path = self._path(key)
        if path.exists():
            self._verify(path, content_hash)
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
        self._verify(path, content_hash)
        return key

    def get(self, object_key: str) -> bytes:
        return self._path(object_key).read_bytes()

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).exists()

    @staticmethod
    def _verify(path: Path, content_hash: str) -> None:
        if sha256_hex(path.read_bytes()) != content_hash:
            raise IntegrityError("STORED_HASH_MISMATCH", f"el objeto almacenado en {path.name} está corrupto")
