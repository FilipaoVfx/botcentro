"""Chunking con localizadores (SRS-F14; T-14, T-15) y calidad de extracción (SRS-F13; T-13)."""

import uuid

import pytest

from botcentro.documents.chunking import (
    PAGE_SEPARATOR,
    ChunkRecipe,
    PageText,
    SegmentSpec,
    chunk_extraction,
)
from botcentro.documents.quality import assess_document, assess_page_text
from botcentro.domain.enums import QualityStatus

EXTRACTION = uuid.UUID("11111111-1111-1111-1111-111111111111")
SMALL = ChunkRecipe(min_tokens=20, max_tokens=40, overlap_tokens=5)


def article(n: int, words: int) -> str:
    return f"ARTÍCULO {n}°. " + " ".join(f"palabra{n}_{i}" for i in range(words)) + "."


def test_offsets_and_pages_point_to_exact_text() -> None:
    pages = [PageText(1, "EXPOSICIÓN DE MOTIVOS\nTexto inicial."), PageText(2, article(1, 10)), PageText(3, article(2, 10))]
    chunks = chunk_extraction(EXTRACTION, pages, recipe=SMALL)
    full = PAGE_SEPARATOR.join(p.text for p in pages)
    for chunk in chunks:
        assert full[chunk.char_start:chunk.char_end] == chunk.text
        assert 1 <= chunk.pdf_page_start <= chunk.pdf_page_end <= 3
    assert chunks[-1].pdf_page_end == 3


def test_large_article_is_split_with_overlap_and_keeps_its_label() -> None:
    pages = [PageText(1, article(7, 100))]
    chunks = chunk_extraction(EXTRACTION, pages, recipe=SMALL)
    assert len(chunks) > 2
    assert all(c.section_label == "ARTÍCULO 7°" and c.token_count <= SMALL.max_tokens for c in chunks)
    assert chunks[0].char_end > chunks[1].char_start  # solapamiento
    assert chunks[1].embedding_input.startswith("ARTÍCULO 7°.")


def test_chunks_never_cross_gazette_segments() -> None:
    # T-14: una gaceta con dos expedientes; cada chunk pertenece a un único segmento.
    seg_a, seg_b = uuid.uuid4(), uuid.uuid4()
    pages = [PageText(i, article(i, 5)) for i in range(1, 7)]
    segments = [SegmentSpec(1, 3, seg_a, "PL 249/2024"), SegmentSpec(5, 6, seg_b, "PL 312/2024")]
    chunks = chunk_extraction(EXTRACTION, pages, segments=segments, recipe=SMALL)
    by_segment = {}
    for chunk in chunks:
        by_segment.setdefault(chunk.segment_id, set()).update(range(chunk.pdf_page_start, chunk.pdf_page_end + 1))
    assert by_segment[seg_a] <= {1, 2, 3}
    assert by_segment[seg_b] <= {5, 6}
    assert by_segment[None] == {4}  # hueco sin expediente asignado


def test_overlapping_segments_are_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_extraction(EXTRACTION, [PageText(1, "a"), PageText(2, "b")],
                         segments=[SegmentSpec(1, 2), SegmentSpec(2, 2)])


def test_chunk_ids_are_stable_per_recipe_and_change_with_it() -> None:
    # T-15: reextraer con otra receta produce derivados nuevos, no los mismos IDs.
    pages = [PageText(1, article(1, 30))]
    first = chunk_extraction(EXTRACTION, pages, recipe=SMALL)
    again = chunk_extraction(EXTRACTION, pages, recipe=SMALL)
    other = chunk_extraction(EXTRACTION, pages, recipe=ChunkRecipe("chunker-v2", min_tokens=20, max_tokens=40, overlap_tokens=5))
    assert [c.id for c in first] == [c.id for c in again]
    assert {c.id for c in first}.isdisjoint(c.id for c in other)


def test_chunk_quality_is_worst_of_its_pages() -> None:
    pages = [PageText(1, article(1, 5)), PageText(2, "texto dudoso", quality=QualityStatus.REVIEW_REQUIRED)]
    (chunk,) = chunk_extraction(EXTRACTION, pages, recipe=SMALL)
    assert chunk.quality_status is QualityStatus.REVIEW_REQUIRED


def test_quality_native_text_missing_needs_ocr() -> None:
    assessment = assess_page_text("  \n ")
    assert assessment.status is QualityStatus.FAILED and assessment.needs_ocr
    blank = assess_page_text("", after_ocr=True)
    assert blank.status is QualityStatus.ACCEPTED and blank.blank


def test_quality_detects_garbage() -> None:
    good = "El presente proyecto de ley tiene por objeto regular el transporte público en los municipios."
    assert assess_page_text(good).status is QualityStatus.ACCEPTED
    garbled = "��� #### 1234 5678 !!!! ???? %%%% &&&& ���� 9999 0000 ~~~~"
    assert assess_page_text(garbled).status is QualityStatus.FAILED
    ocr_noise = "E l p r e s e n t e p r o y e c t o d e l e y t i e n e p o r o b j e t o r e g u l a r"
    assert assess_page_text(ocr_noise, after_ocr=True).status is QualityStatus.REVIEW_REQUIRED


def test_document_quality_aggregation() -> None:
    ok = assess_page_text("Texto legible de una ponencia con suficientes palabras para evaluar.")
    bad = assess_page_text("�" * 50)
    assert assess_document([ok, ok]) is QualityStatus.ACCEPTED
    assert assess_document([ok, bad]) is QualityStatus.REVIEW_REQUIRED
    assert assess_document([bad]) is QualityStatus.FAILED
