"""Piloto de gacetas (DEC-11, DEC-12): mide si vale la pena indexar SRC-03.

Por cada gaceta reciente: descarga el PDF (en memoria, nunca se guarda), extrae texto con OCR
donde hace falta, segmenta por pieza, enlaza cada segmento solo a los proyectos de su propio
encabezado, trocea y embebe con e5-small y publica en Qdrant. Escribe una línea JSON de
métricas por gaceta para el informe del piloto.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from botcentro.connectors.gacetas_imprenta import GacetaListing, GacetaRef, download
from botcentro.costs.budget import BudgetGuard
from botcentro.documents.chunking import ExtractedText, PageText, chunk_extraction
from botcentro.documents.gaceta_segments import segment_at, segment_gaceta
from botcentro.documents.pdf_text import OCR_VERSION, extract_pages
from botcentro.domain.enums import QualityStatus
from botcentro.domain.project_ids import ProjectRef, format_ref
from botcentro.embeddings.e5 import E5_SMALL_RECIPE, MAX_INPUT_TOKENS, E5SmallEmbedder
from botcentro.http.fetcher import SafeFetcher
from botcentro.insforge.client import InsForgeClient
from botcentro.vectors.qdrant import QdrantStore, chunk_payload

EXTRACTOR_VERSION = f"gaceta-pypdf-{OCR_VERSION}-v1"
EXTRACTION_NAMESPACE = uuid.UUID("7d1e0f5a-3c2b-4e8d-9a41-6b5c2d7e8f90")


def load_identifiers(client: InsForgeClient) -> dict[tuple[str, str, str, int], str]:
    """(corporación, tipo, número, año) → proyecto, para enlazar referencias sin una RPC por cita."""
    corps = {r["id"]: r["code"] for r in client.select("corporations", {"select": "id,code"})}
    index: dict[tuple[str, str, str, int], str] = {}
    offset = 0
    while True:
        rows = client.select("project_identifiers", {
            "select": "project_id,corporation_id,initiative_type,number,filing_year",
            "numbering_scope": "eq.corporation_filing_year", "order": "id", "limit": "1000", "offset": str(offset)})
        for r in rows:
            if r["filing_year"]:
                index[(corps[r["corporation_id"]], r["initiative_type"], r["number"], r["filing_year"])] = r["project_id"]
        if len(rows) < 1000:
            return index
        offset += 1000


def _project_of(ref: ProjectRef, index: dict[tuple[str, str, str, int], str]) -> str | None:
    types = [ref.initiative_type.value] if ref.initiative_type else ["proyecto_ley", "proyecto_acto_legislativo"]
    for t in types:
        if project := index.get((ref.corporation.value, t, ref.number, ref.filing_year)):  # type: ignore[union-attr]
            return project
    return None


def recent(listing: GacetaListing, limit: int, pause: float) -> Iterator[GacetaRef]:
    first = 0
    while first < min(limit, listing.total):
        for ref in listing.page(first, 50)[: limit - first]:
            yield ref
        first += 50
        time.sleep(pause)


def process(ref: GacetaRef, *, fetcher: SafeFetcher, embedder: E5SmallEmbedder, store: QdrantStore,
            identifiers: dict[tuple[str, str, str, int], str]) -> dict[str, Any]:
    m: dict[str, Any] = {"document_key": ref.document_key, "url": ref.permalink}
    t0 = time.monotonic()
    fetched = download(fetcher, ref)
    m.update(bytes=fetched.byte_size, pdf_sha256=fetched.content_hash, seconds_download=round(time.monotonic() - t0, 1))

    t1 = time.monotonic()
    pages = extract_pages(fetched.content)
    del fetched  # DEC-11: el PDF no se conserva
    m.update(pages=len(pages), native_pages=sum(p.method == "native" for p in pages),
             ocr_pages=sum(p.method == "ocr" for p in pages), unreadable_pages=sum(p.method == "none" for p in pages),
             seconds_text=round(time.monotonic() - t1, 1))

    page_texts = [PageText(p.pdf_page, p.text, quality=p.quality) for p in pages]
    doc = ExtractedText(page_texts)
    segments = segment_gaceta(doc.text)
    refs = {r for s in segments for r in s.refs}
    linked = {r: _project_of(r, identifiers) for r in refs}
    m.update(segments=len(segments), segment_kinds=sorted({s.kind for s in segments}),
             segments_with_refs=sum(bool(s.refs) for s in segments), refs=len(refs),
             refs_linked=sum(v is not None for v in linked.values()),
             projects=sorted({v for v in linked.values() if v}),
             unlinked=sorted(format_ref(r) for r, v in linked.items() if v is None))

    t2 = time.monotonic()
    extraction_id = uuid.uuid5(EXTRACTION_NAMESPACE, f"{ref.document_key}:{m['pdf_sha256']}:{EXTRACTOR_VERSION}")
    chunks = [c for c in chunk_extraction(extraction_id, page_texts, recipe=E5_SMALL_RECIPE,
                                          measure=embedder.passage_measure, max_measure=MAX_INPUT_TOKENS)
              if c.quality_status is QualityStatus.ACCEPTED and c.text.strip()]
    vectors = {v.chunk_id: v.vector for v in embedder.embed_passages(chunks)} if chunks else {}
    points = []
    for c in chunks:
        segment = segment_at(segments, c.char_start)
        payload = chunk_payload({
            "chunk_id": c.id, "text": c.text, "heading": c.heading, "section_label": c.section_label,
            "document_key": ref.document_key, "document_type": "gaceta",
            "title": f"Gaceta {ref.number} de {ref.published_on.year} ({ref.corporation})",
            "published_on": ref.published_on.isoformat(), "source_url": ref.permalink, "source_code": "SRC-03",
            "project_ids": sorted({p for r in segment.refs if (p := linked.get(r))}),
        }, doc_kind="gaceta")
        payload.update(segment_kind=segment.kind, pdf_page_start=c.pdf_page_start, pdf_page_end=c.pdf_page_end,
                       pdf_sha256=m["pdf_sha256"], extractor_version=EXTRACTOR_VERSION)
        points.append({"id": str(c.id), "vector": vectors[str(c.id)], "payload": payload})
    store.upsert(points)
    m.update(chunks=len(points), chunks_linked=sum(bool(p["payload"]["project_ids"]) for p in points),
             text_chars=sum(len(p.text) for p in pages), seconds_embed=round(time.monotonic() - t2, 1),
             seconds_total=round(time.monotonic() - t0, 1))
    return m


def run_pilot(*, limit: int, out: Path, fetcher: SafeFetcher, client: InsForgeClient, store: QdrantStore,
              embedder: E5SmallEmbedder, budget: BudgetGuard, pause: float = 3.0) -> dict[str, int]:
    done = set()
    if out.exists():
        done = {json.loads(line)["document_key"] for line in out.read_text().splitlines() if line.strip()}
    identifiers = load_identifiers(client)
    listing = GacetaListing(fetcher)
    totals = {"processed": 0, "skipped": 0, "failed": 0}
    with out.open("a") as sink:
        for ref in recent(listing, limit, pause):
            if ref.document_key in done:
                totals["skipped"] += 1
                continue
            try:
                with budget.spend("local", "gaceta_ocr_embedding", "0", units=1) as reservation:
                    metrics = process(ref, fetcher=fetcher, embedder=embedder, store=store, identifiers=identifiers)
                    reservation.settle(0)
                totals["processed"] += 1
            except Exception as exc:  # noqa: BLE001 — el piloto registra el fallo y sigue
                metrics = {"document_key": ref.document_key, "url": ref.permalink, "error": f"{type(exc).__name__}: {exc}"[:300]}
                totals["failed"] += 1
            sink.write(json.dumps(metrics, ensure_ascii=False) + "\n")
            sink.flush()
            print(json.dumps({**totals, "last": ref.document_key}), flush=True)
            time.sleep(pause)
    return totals
