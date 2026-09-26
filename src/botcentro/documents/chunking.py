"""Segmentación en chunks con localizadores estables (SRS-F14, §8; T-14, T-15).

* El texto de una extracción es la concatenación de sus páginas separadas por '\\f'; cada
  chunk guarda el rango exacto de caracteres en ese texto y las páginas PDF (desde 1).
* Se prefieren cortes en artículos, capítulos y títulos; una sección mayor que el máximo se
  divide conservando su encabezado y etiqueta, con solapamiento dentro de la misma sección.
* Nunca se cruza el límite entre segmentos (expedientes distintos de una gaceta).
* La receta (tokenizador, parámetros, versión) forma parte de la identidad del chunk.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from botcentro.domain.enums import QualityStatus
from botcentro.domain.hashing import sha256_hex

PAGE_SEPARATOR = "\f"
CHUNK_NAMESPACE = uuid.UUID("5b6f2a54-6f0c-4d7e-9f7e-1c2a3b4c5d6e")

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_HEADER_RE = re.compile(
    r"^[ \t]*(?P<label>"
    r"ART[ÍI]CULO\s+(?:\d+[A-Z]?|[ÚU]NICO|NUEVO|TRANSITORIO)[°º.o]?"
    r"|CAP[ÍI]TULO\s+(?:[IVXLC]+|\d+)"
    r"|T[ÍI]TULO\s+(?:[IVXLC]+|\d+)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_QUALITY_ORDER = {QualityStatus.ACCEPTED: 0, QualityStatus.REVIEW_REQUIRED: 1, QualityStatus.FAILED: 2}


@dataclass(frozen=True)
class ChunkRecipe:
    chunker_version: str = "chunker-v1"
    tokenizer: str = "regex-words-v1"
    min_tokens: int = 600
    max_tokens: int = 900
    overlap_tokens: int = 120

    def __post_init__(self) -> None:
        if not 0 < self.min_tokens <= self.max_tokens or not 0 <= self.overlap_tokens < self.min_tokens:
            raise ValueError("receta de chunking inválida")


@dataclass(frozen=True)
class PageText:
    pdf_page: int
    text: str
    printed_label: str | None = None
    quality: QualityStatus = QualityStatus.ACCEPTED


@dataclass(frozen=True)
class SegmentSpec:
    page_start: int
    page_end: int
    segment_id: UUID | None = None
    label: str | None = None


@dataclass(frozen=True)
class Chunk:
    id: UUID
    ordinal: int
    text: str
    text_hash: str
    token_count: int
    pdf_page_start: int
    pdf_page_end: int
    char_start: int
    char_end: int
    section_label: str | None
    heading: str | None
    segment_id: UUID | None
    quality_status: QualityStatus
    chunker_version: str

    @property
    def embedding_input(self) -> str:
        """Texto para embeddings: el encabezado de la sección da contexto a los fragmentos."""
        if self.heading and not self.text.lstrip().startswith(self.heading):
            return f"{self.heading}\n{self.text}"
        return self.text


class ExtractedText:
    """Texto completo de una extracción con el mapa de offsets por página."""

    def __init__(self, pages: Sequence[PageText]) -> None:
        if not pages:
            raise ValueError("una extracción sin páginas no se segmenta")
        numbers = [p.pdf_page for p in pages]
        if numbers != list(range(1, len(pages) + 1)):
            raise ValueError("las páginas deben ser consecutivas desde 1")
        self.pages = list(pages)
        self.offsets: list[tuple[int, int]] = []
        parts: list[str] = []
        cursor = 0
        for i, page in enumerate(self.pages):
            if PAGE_SEPARATOR in page.text:
                raise ValueError(f"la página {page.pdf_page} contiene el separador de páginas")
            self.offsets.append((cursor, cursor + len(page.text)))
            parts.append(page.text)
            cursor += len(page.text) + (1 if i < len(self.pages) - 1 else 0)
        self.text = PAGE_SEPARATOR.join(parts)

    def page_at(self, offset: int) -> int:
        """Página que contiene el offset (un separador pertenece a la página anterior)."""
        for page, (_, end) in zip(self.pages, self.offsets):
            if offset <= end:
                return page.pdf_page
        return self.pages[-1].pdf_page

    def page_range(self, page_start: int, page_end: int) -> tuple[int, int]:
        return self.offsets[page_start - 1][0], self.offsets[page_end - 1][1]

    def quality_between(self, page_start: int, page_end: int) -> QualityStatus:
        worst = max((self.pages[i - 1].quality for i in range(page_start, page_end + 1)), key=_QUALITY_ORDER.__getitem__)
        return worst


@dataclass(frozen=True)
class _Section:
    start: int
    end: int
    label: str | None
    heading: str | None


def _segments(doc: ExtractedText, specs: Sequence[SegmentSpec]) -> list[SegmentSpec]:
    """Segmentos declarados más los huecos entre ellos, en orden y sin solapes."""
    last_page = doc.pages[-1].pdf_page
    ordered = sorted(specs, key=lambda s: s.page_start)
    result: list[SegmentSpec] = []
    next_page = 1
    for spec in ordered:
        if spec.page_start < next_page or spec.page_end < spec.page_start or spec.page_end > last_page:
            raise ValueError(f"segmento inválido o solapado: páginas {spec.page_start}-{spec.page_end}")
        if spec.page_start > next_page:
            result.append(SegmentSpec(next_page, spec.page_start - 1))
        result.append(spec)
        next_page = spec.page_end + 1
    if next_page <= last_page:
        result.append(SegmentSpec(next_page, last_page))
    return result


def _sections(text: str, start: int, end: int) -> list[_Section]:
    body = text[start:end]
    headers = list(_HEADER_RE.finditer(body))
    sections: list[_Section] = []
    first = headers[0].start() if headers else len(body)
    if body[:first].strip():
        sections.append(_Section(start, start + first, None, None))
    for i, match in enumerate(headers):
        sec_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        line_end = body.find("\n", match.start())
        heading = body[match.start(): line_end if 0 <= line_end < sec_end else sec_end].strip()
        label = " ".join(match["label"].split()).upper()
        sections.append(_Section(start + match.start(), start + sec_end, label, heading))
    return sections


def _tokens(text: str, start: int, end: int) -> list[tuple[int, int]]:
    return [(start + m.start(), start + m.end()) for m in _TOKEN_RE.finditer(text[start:end])]


def chunk_extraction(extraction_id: UUID, pages: Sequence[PageText], *, segments: Sequence[SegmentSpec] = (),
                     recipe: ChunkRecipe = ChunkRecipe()) -> list[Chunk]:
    doc = ExtractedText(pages)
    pieces: list[tuple[int, int, int, str | None, str | None, UUID | None]] = []

    for segment in _segments(doc, segments):
        seg_start, seg_end = doc.page_range(segment.page_start, segment.page_end)
        pending: list[tuple[int, int, int, _Section]] = []  # (start, end, tokens, first section)

        def flush() -> None:
            if pending:
                first = pending[0][3]
                pieces.append((pending[0][0], pending[-1][1], sum(p[2] for p in pending),
                               first.label, first.heading, segment.segment_id))
                pending.clear()

        for section in _sections(doc.text, seg_start, seg_end):
            tokens = _tokens(doc.text, section.start, section.end)
            if not tokens:
                continue
            count = len(tokens)
            if count > recipe.max_tokens:
                flush()
                for piece_start, piece_end, piece_count in _split(doc.text, tokens, recipe):
                    pieces.append((piece_start, piece_end, piece_count, section.label, section.heading,
                                   segment.segment_id))
                continue
            current = sum(p[2] for p in pending)
            if pending and current + count > recipe.max_tokens:
                flush()
            pending.append((tokens[0][0], tokens[-1][1], count, section))
        flush()

    chunks = []
    for ordinal, (start, end, count, label, heading, segment_id) in enumerate(pieces):
        text = doc.text[start:end]
        text_hash = sha256_hex(text.encode("utf-8"))
        page_start, page_end = doc.page_at(start), doc.page_at(end - 1)
        chunks.append(Chunk(
            id=uuid.uuid5(CHUNK_NAMESPACE, f"{extraction_id}:{recipe.chunker_version}:{ordinal}:{text_hash}"),
            ordinal=ordinal,
            text=text,
            text_hash=text_hash,
            token_count=count,
            pdf_page_start=page_start,
            pdf_page_end=page_end,
            char_start=start,
            char_end=end,
            section_label=label,
            heading=heading,
            segment_id=segment_id,
            quality_status=doc.quality_between(page_start, page_end),
            chunker_version=recipe.chunker_version,
        ))
    return chunks


def _split(text: str, tokens: list[tuple[int, int]], recipe: ChunkRecipe) -> list[tuple[int, int, int]]:
    """Ventanas de hasta max_tokens con solapamiento, prefiriendo cortar tras punto o párrafo."""
    pieces = []
    start = 0
    n = len(tokens)
    while start < n:
        end = min(start + recipe.max_tokens, n)
        if end < n:
            floor = start + recipe.min_tokens
            for i in range(end - 1, floor - 1, -1):
                token_text = text[tokens[i][0]:tokens[i][1]]
                following = text[tokens[i][1]:tokens[i + 1][0]]
                if token_text in {".", ";", ":"} or "\n" in following:
                    end = i + 1
                    break
        pieces.append((tokens[start][0], tokens[end - 1][1], end - start))
        if end >= n:
            break
        start = max(end - recipe.overlap_tokens, start + 1)
    return pieces
