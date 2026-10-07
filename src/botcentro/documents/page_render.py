"""Páginas de las gacetas como imagen, para verificar una cita sin salir del chat («📄 Ver página»).

* El PDF se descarga de la Imprenta Nacional solo cuando alguien pide una página y se conserva unos minutos
  para que «◀ ▶» no lo vuelvan a descargar; después se borra (DEC-11: los PDF no se conservan).
* La página se convierte a PNG con `pdftoppm` y se guarda en caché por gaceta, huella del PDF y página: la
  segunda consulta de la misma página no descarga nada. Si la Imprenta cambia el archivo (otra huella), la
  caché anterior deja de usarse.
* Una sola descarga por gaceta a la vez y como máximo dos conversiones simultáneas: el bot no satura la
  Imprenta ni el servidor aunque varias personas pidan páginas a la vez.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PDF_TTL_SECONDS = 15 * 60
MAX_PDFS = 4
DPI = 110  # ~1.290 px de alto en A4: lo que Telegram muestra como foto sin recomprimir más


class PageUnavailable(Exception):
    """La página no se puede mostrar (fuente caída, página inexistente, PDF ilegible)."""


@dataclass(frozen=True)
class RenderedPage:
    path: Path
    page: int
    pages: int
    pdf_sha256: str
    from_cache: bool


class PageRenderer:
    def __init__(self, download: Callable[[str, str], tuple[bytes, str]], cache_dir: Path, *,
                 dpi: int = DPI, pdf_ttl: float = PDF_TTL_SECONDS, max_pdfs: int = MAX_PDFS,
                 clock: Callable[[], float] = time.time) -> None:
        """`download(document_key, url)` devuelve (bytes del PDF, sha256)."""
        self._download = download
        self.cache = cache_dir
        self.pdfs = cache_dir / "_pdf"
        self.dpi = dpi
        self.pdf_ttl = pdf_ttl
        self.max_pdfs = max_pdfs
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._convert = threading.BoundedSemaphore(2)

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    @staticmethod
    def _slug(document_key: str) -> str:
        return document_key.replace(":", "_")

    def _meta_path(self, document_key: str) -> Path:
        return self.cache / self._slug(document_key) / "meta.json"

    def _prune_pdfs(self) -> None:
        if not self.pdfs.exists():
            return
        files = sorted(self.pdfs.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
        now = self.clock()
        for i, f in enumerate(files):
            if now - f.stat().st_mtime > self.pdf_ttl or i < len(files) - self.max_pdfs:
                f.unlink(missing_ok=True)

    def _pdf(self, document_key: str, url: str) -> tuple[Path, dict]:
        """PDF transitorio de la gaceta (descargado si no está o venció) y sus metadatos."""
        self._prune_pdfs()
        pdf = self.pdfs / f"{self._slug(document_key)}.pdf"
        meta_path = self._meta_path(document_key)
        if pdf.exists() and meta_path.exists() and self.clock() - pdf.stat().st_mtime <= self.pdf_ttl:
            return pdf, json.loads(meta_path.read_text())
        try:
            content, sha = self._download(document_key, url)
        except Exception as exc:  # noqa: BLE001 — se informa como página no disponible, nunca como vacía
            raise PageUnavailable(f"no se pudo descargar la gaceta: {type(exc).__name__}") from exc
        from io import BytesIO

        from pypdf import PdfReader

        try:
            pages = len(PdfReader(BytesIO(content)).pages)
        except Exception as exc:  # noqa: BLE001
            raise PageUnavailable("el PDF de la gaceta no se puede leer") from exc
        self.pdfs.mkdir(parents=True, exist_ok=True)
        tmp = pdf.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.replace(pdf)
        meta = {"pdf_sha256": sha, "pages": pages}
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        old = json.loads(meta_path.read_text()) if meta_path.exists() else None
        if old and old.get("pdf_sha256") != sha:  # la Imprenta cambió el archivo: la caché vieja no sirve
            for png in meta_path.parent.glob("*.png"):
                png.unlink(missing_ok=True)
        meta_path.write_text(json.dumps(meta))
        return pdf, meta

    def render(self, document_key: str, url: str, page: int) -> RenderedPage:
        if page < 1:
            raise PageUnavailable("número de página inválido")
        png = self.cache / self._slug(document_key) / f"p{page:04d}-{self.dpi}.png"
        meta_path = self._meta_path(document_key)
        if png.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            return RenderedPage(png, page, meta["pages"], meta["pdf_sha256"], True)
        with self._lock(document_key):
            if png.exists():  # otra petición la generó mientras esperaba
                meta = json.loads(meta_path.read_text())
                return RenderedPage(png, page, meta["pages"], meta["pdf_sha256"], True)
            pdf, meta = self._pdf(document_key, url)
            if page > meta["pages"]:
                raise PageUnavailable(f"la gaceta tiene {meta['pages']} páginas")
            with self._convert:
                prefix = png.with_suffix("")
                try:
                    subprocess.run(["pdftoppm", "-r", str(self.dpi), "-png", "-f", str(page), "-l", str(page),
                                    "-singlefile", str(pdf), str(prefix)], check=True, capture_output=True, timeout=90)
                except (subprocess.SubprocessError, OSError) as exc:
                    raise PageUnavailable("no se pudo convertir la página") from exc
            return RenderedPage(png, page, meta["pages"], meta["pdf_sha256"], False)

    def purge_pdfs(self) -> None:
        """Borra todos los PDF transitorios (al apagar o desde operación)."""
        shutil.rmtree(self.pdfs, ignore_errors=True)
