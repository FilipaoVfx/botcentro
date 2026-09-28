"""Texto por página de un PDF con OCR solo donde hace falta (SRS §8 pasos 5–6; DEC-06b).

1. Texto nativo con pypdf.
2. Si la página no parece español legible (vacía, escaneada o con fuentes sin mapa Unicode que
   salen como símbolos), se rasteriza con pdftoppm y se lee con Tesseract en español.
3. Si tampoco el OCR da texto legible, la página queda `review_required` y no se indexa.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from botcentro.domain.enums import QualityStatus

OCR_VERSION = "tesseract-5-spa-200dpi"
_WORD_RE = re.compile(r"[a-záéíóúñü]{2,}")
_STOPWORDS = frozenset(
    "de la que el en y a los del se las por un para con no una su al lo como más o pero sus le ya "
    "este esta ley artículo proyecto sobre entre cuando también fue ha son dicho será".split())


@dataclass(frozen=True)
class PageResult:
    pdf_page: int
    text: str
    method: str  # 'native' | 'ocr' | 'none'
    quality: QualityStatus


def legible(text: str) -> bool:
    """Español legible: suficientes palabras y proporción razonable de palabras vacías."""
    words = _WORD_RE.findall(text.lower())
    if len(words) < 25:
        return False
    stop = sum(1 for w in words if w in _STOPWORDS)
    symbols = sum(1 for ch in text if not (ch.isalnum() or ch.isspace() or ch in ".,;:()¿?¡!\"'-–—°º%$/"))
    return stop / len(words) >= 0.18 and symbols / max(len(text), 1) < 0.08


def _clean(text: str) -> str:
    text = text.replace("\f", "\n").replace("\x00", "")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def ocr_page(pdf_path: str, pdf_page: int, *, dpi: int = 200) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "p")
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(pdf_page), "-l", str(pdf_page),
                        "-singlefile", pdf_path, prefix], check=True, capture_output=True, timeout=120)
        out = subprocess.run(["tesseract", prefix + ".png", "stdout", "-l", "spa", "--psm", "1"],
                             check=True, capture_output=True, timeout=300,
                             env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        return out.stdout.decode("utf-8", errors="replace")


def extract_pages(content: bytes, *, ocr_workers: int = 2) -> list[PageResult]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    native: list[str] = []
    for page in reader.pages:
        try:
            native.append(_clean(page.extract_text() or ""))
        except Exception:  # noqa: BLE001 — una página corrupta no invalida el resto
            native.append("")
    pending = [i for i, text in enumerate(native) if not legible(text)]
    results = {i: PageResult(i + 1, text, "native", QualityStatus.ACCEPTED)
               for i, text in enumerate(native) if i not in pending}
    if pending:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(content)
            tmp.flush()
            with ThreadPoolExecutor(max_workers=ocr_workers) as pool:
                texts = list(pool.map(lambda i: _clean(ocr_page(tmp.name, i + 1)), pending))
        for i, text in zip(pending, texts):
            ok = legible(text)
            results[i] = PageResult(i + 1, text if ok else "", "ocr" if ok else "none",
                                    QualityStatus.ACCEPTED if ok else QualityStatus.REVIEW_REQUIRED)
    return [results[i] for i in range(len(native))]
