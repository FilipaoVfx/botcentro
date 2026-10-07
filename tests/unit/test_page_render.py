"""Páginas de gacetas como imagen: caché, PDF transitorio (DEC-11) y fallos explícitos."""

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from botcentro.documents.page_render import PageRenderer, PageUnavailable


def pdf_bytes(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=280)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class Source:
    def __init__(self, pages: int = 3, sha: str = "a" * 64) -> None:
        self.calls, self.pages, self.sha = 0, pages, sha

    def __call__(self, key: str, url: str) -> tuple[bytes, str]:
        self.calls += 1
        return pdf_bytes(self.pages), self.sha


def test_renders_page_and_reuses_cache_and_transient_pdf(tmp_path: Path) -> None:
    now = [1000.0]
    src = Source()
    r = PageRenderer(src, tmp_path, dpi=30, clock=lambda: now[0])
    first = r.render("gaceta:senado:2026:1", "u", 2)
    assert first.path.exists() and first.pages == 3 and not first.from_cache and src.calls == 1
    assert r.render("gaceta:senado:2026:1", "u", 2).from_cache  # misma página: sin descargar
    r.render("gaceta:senado:2026:1", "u", 3)
    assert src.calls == 1  # otra página con el PDF transitorio vigente: sin descargar


def test_pdf_is_deleted_after_ttl(tmp_path: Path) -> None:
    import os

    src = Source()
    r = PageRenderer(src, tmp_path, dpi=30, pdf_ttl=60)
    r.render("gaceta:camara:2026:9", "u", 1)
    pdf = next((tmp_path / "_pdf").glob("*.pdf"))
    os.utime(pdf, (0, 0))  # antigüedad mayor que el plazo
    r._prune_pdfs()  # noqa: SLF001
    assert not pdf.exists()  # DEC-11: el PDF no se conserva


def test_new_file_version_invalidates_cached_pages(tmp_path: Path) -> None:
    import os

    src = Source(sha="a" * 64)
    r = PageRenderer(src, tmp_path, dpi=30, pdf_ttl=60)
    old = r.render("gaceta:senado:2026:5", "u", 1)
    src.sha = "b" * 64
    os.utime(next((tmp_path / "_pdf").glob("*.pdf")), (0, 0))
    r._prune_pdfs()  # noqa: SLF001
    r.render("gaceta:senado:2026:5", "u", 2)
    assert not old.path.exists()  # páginas de la versión anterior descartadas


def test_out_of_range_and_download_failure_are_explicit(tmp_path: Path) -> None:
    r = PageRenderer(Source(pages=2), tmp_path, dpi=30)
    with pytest.raises(PageUnavailable, match="2 páginas"):
        r.render("gaceta:senado:2026:7", "u", 9)

    def broken(key: str, url: str) -> tuple[bytes, str]:
        raise TimeoutError

    with pytest.raises(PageUnavailable, match="descargar"):
        PageRenderer(broken, tmp_path / "b", dpi=30).render("gaceta:senado:2026:8", "u", 1)
