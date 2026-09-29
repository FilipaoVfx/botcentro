"""Carga documental asistida (investigaciones §11.2, EVI-04/05; DEC-11).

Un editor indica la URL oficial de una publicación (boletín, estado, providencia). El servidor la
descarga con `SafeFetcher` restringido a los dominios de la fuente, extrae el texto por página
(nativo u OCR) y registra documento, revisión y páginas. No se guarda el PDF: solo texto, hash
SHA-256 y URL oficial. Páginas con calidad dudosa quedan en cuarentena y no se pueden citar.
La misma URL con bytes nuevos crea una revisión nueva; los mismos bytes no duplican nada (T-15).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botcentro.documents.pdf_text import PageResult, extract_pages
from botcentro.insforge.client import RpcClient

# Fuentes con carga asistida y sus dominios (coinciden con seeds/fuentes_investigaciones.sql).
ASSISTED_SOURCES: dict[str, tuple[str, ...]] = {
    "SRC-22": ("cortesuprema.gov.co", "www.cortesuprema.gov.co"),
    "SRC-23": ("contraloria.gov.co", "www.contraloria.gov.co"),
    "SRC-24": ("fiscalia.gov.co", "www.fiscalia.gov.co"),
}
DOCUMENT_TYPES = frozenset({"boletin", "estado", "providencia", "comunicado", "auto", "sentencia", "otro"})
MAX_BYTES = 40 * 1024 * 1024
EXTRACTOR_VERSION = "pdf-text-1"


class AssistedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AssistedRequest:
    source_code: str
    url: str
    title: str
    document_type: str

    def validate(self) -> None:
        if self.source_code not in ASSISTED_SOURCES:
            raise AssistedError("SOURCE_NOT_ASSISTED", "Esa fuente no admite carga asistida.")
        if not self.url.startswith("https://"):
            raise AssistedError("URL_NOT_HTTPS", "La URL debe ser https.")
        if self.document_type not in DOCUMENT_TYPES:
            raise AssistedError("DOCUMENT_TYPE", "Tipo de documento no reconocido.")
        if not 3 <= len(self.title.strip()) <= 300:
            raise AssistedError("TITLE", "El título debe tener entre 3 y 300 caracteres.")


def register_document(rpc: RpcClient, fetcher: Any, request: AssistedRequest, *,
                      extract: Callable[[bytes], list[PageResult]] = extract_pages) -> dict[str, Any]:
    """Descarga, extrae y registra. `fetcher` debe estar restringido a los dominios de la fuente."""
    request.validate()
    fetched = fetcher.fetch(request.url)
    mime = (fetched.sniffed_mime or fetched.declared_mime or "").split(";")[0].strip()
    if mime != "application/pdf":
        raise AssistedError("NOT_PDF", f"La URL no entrega un PDF (tipo {mime or 'desconocido'}).")
    if fetched.byte_size > MAX_BYTES:
        raise AssistedError("TOO_LARGE", "El PDF supera el tamaño admitido.")
    pages = extract(fetched.content)
    result = rpc.call("editor_register_document", {
        "p_source_code": request.source_code, "p_url": request.url, "p_final_url": fetched.final_url,
        "p_title": request.title.strip(), "p_document_type": request.document_type,
        "p_content_hash": fetched.content_hash, "p_byte_size": fetched.byte_size, "p_mime": mime,
        "p_object_key": None,  # DEC-11: el original lo custodia la fuente oficial
        "p_pages": [{"page": p.pdf_page, "text": p.text, "method": p.method, "quality": p.quality.value} for p in pages],
        "p_extractor_version": EXTRACTOR_VERSION})
    quarantined = [p.pdf_page for p in pages if p.quality.value != "accepted"]
    return {**(result or {}), "pages": len(pages), "quarantined_pages": quarantined}
