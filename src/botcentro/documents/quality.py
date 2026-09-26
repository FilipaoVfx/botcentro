"""Calidad de extracción por página y por documento (SRS-F13, §8; T-13).

Clasificación: accepted, review_required o failed. Los umbrales son valores iniciales para
calibrar con páginas etiquetadas; un valor de confianza de OCR no se trata como precisión.
Las señales se devuelven para poder recalibrar sin reprocesar.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from botcentro.domain.enums import QualityStatus


@dataclass(frozen=True)
class QualityThresholds:
    min_chars: int = 40
    review_replacement_ratio: float = 0.01
    fail_replacement_ratio: float = 0.10
    review_letter_ratio: float = 0.30
    fail_letter_ratio: float = 0.10
    min_avg_word_len: float = 2.0
    max_avg_word_len: float = 20.0
    review_control_ratio: float = 0.01


@dataclass(frozen=True)
class PageAssessment:
    status: QualityStatus
    needs_ocr: bool
    blank: bool
    signals: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def assess_page_text(text: str | None, *, after_ocr: bool = False,
                     thresholds: QualityThresholds = QualityThresholds()) -> PageAssessment:
    """Evalúa el texto de una página.

    Extracción nativa con muy poco texto → necesita OCR (queda `failed` hasta que se aplique).
    Tras OCR, una página casi vacía se considera en blanco: aceptada, sin contenido citable.
    """
    content = text or ""
    non_space = [ch for ch in content if not ch.isspace()]
    total = len(non_space)
    if total < thresholds.min_chars:
        if after_ocr:
            return PageAssessment(QualityStatus.ACCEPTED, needs_ocr=False, blank=True,
                                  signals={"chars": float(total)}, reasons=("página en blanco",))
        return PageAssessment(QualityStatus.FAILED, needs_ocr=True, blank=False,
                              signals={"chars": float(total)}, reasons=("texto nativo insuficiente",))

    replacement = sum(ch == "�" for ch in non_space) / total
    letters = sum(ch.isalpha() for ch in non_space) / total
    control = sum(unicodedata.category(ch) == "Cc" for ch in content if ch not in "\n\r\t\f") / max(len(content), 1)
    words = _WORD_RE.findall(content)
    avg_word = sum(map(len, words)) / len(words) if words else 0.0
    signals = {"chars": float(total), "replacement_ratio": replacement, "letter_ratio": letters,
               "control_ratio": control, "avg_word_len": avg_word}

    reasons: list[str] = []
    status = QualityStatus.ACCEPTED
    if replacement > thresholds.fail_replacement_ratio or letters < thresholds.fail_letter_ratio:
        status = QualityStatus.FAILED
        reasons.append("texto no interpretable")
    else:
        if replacement > thresholds.review_replacement_ratio:
            reasons.append("caracteres de reemplazo")
        if letters < thresholds.review_letter_ratio:
            reasons.append("proporción de letras baja")
        if not thresholds.min_avg_word_len <= avg_word <= thresholds.max_avg_word_len:
            reasons.append("longitud media de palabra anómala")
        if control > thresholds.review_control_ratio:
            reasons.append("caracteres de control")
        if reasons:
            status = QualityStatus.REVIEW_REQUIRED
    needs_ocr = status is QualityStatus.FAILED and not after_ocr
    return PageAssessment(status, needs_ocr=needs_ocr, blank=False, signals=signals, reasons=tuple(reasons))


def assess_document(pages: Sequence[PageAssessment]) -> QualityStatus:
    """Todas fallidas → failed; alguna fallida o en revisión → review_required."""
    if not pages:
        return QualityStatus.FAILED
    statuses = [p.status for p in pages if not p.blank]
    if not statuses or all(s is QualityStatus.FAILED for s in statuses):
        return QualityStatus.FAILED
    if any(s is not QualityStatus.ACCEPTED for s in statuses):
        return QualityStatus.REVIEW_REQUIRED
    return QualityStatus.ACCEPTED
