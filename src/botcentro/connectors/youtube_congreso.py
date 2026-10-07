"""Videos oficiales de las sesiones del Congreso (SRC-26): feeds RSS oficiales de YouTube.

Solo metadatos y enlace: no se descarga video ni audio, y no se usan cookies, proxies ni clientes
alternativos (YouTube exige iniciar sesión para el detalle de cada video desde un servidor; eso no
se elude). El feed oficial trae los últimos 15 videos de cada canal, así que se lee varias veces al
día (temporizador `botcentro-videos`); un video que sale del feed no se borra.

Cada entrada se convierte en una observación `session_video` con lo que permite enlazarla a datos
del producto: corporación, cuerpo (plenaria, comisión), fecha de la sesión (del título, no de la
publicación) y tema. Los canales: Canal Congreso (enlazado desde senado.gov.co y camara.gov.co) y la
Cámara de Representantes (@CamaraColombia, enlazado desde camara.gov.co).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from botcentro.connectors.base import (Cursor, DiscoveredItem, DiscoverPage, Issue, IssueSeverity, NormalizedCandidate,
                                       ParseResult, ValidationReport)
from botcentro.connectors.video_titles import MONTHS, clean_description, fold, parse_title
from botcentro.domain.dates import PartialDate
from botcentro.http.fetcher import Fetched, NotModified, SafeFetcher

CHANNELS = (("UCJfKMFp7dtO8mpHT56X0kJQ", "Canal Congreso", "congreso"),
            ("UCzescUFTh2jiZkYIx9uZtqA", "Cámara de Representantes", "camara"))
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
XML = frozenset({"application/xml", "application/atom+xml", "text/xml"})
NS = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/",
      "yt": "http://www.youtube.com/xml/schemas/2015"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _beyond_title(title: str, description: str) -> str:
    """La parte de la descripción que no repite el título (palabra por palabra, sin tildes ni mayúsculas)."""
    words = description.split()
    title_words = [w for w in fold(re.sub(r"[^\w\s]", " ", title)).split()]
    i = j = 0
    while i < len(words) and j < len(title_words):
        w = fold(re.sub(r"[^\w]", "", words[i]))
        if not w:
            i += 1
        elif w == title_words[j]:
            i += 1
            j += 1
        else:
            break
    return " ".join(words[i:]).strip(" -:·") if j >= 3 else description


_STOP = {"de", "la", "el", "los", "las", "del", "y", "en", "a", "por", "para", "con", "que", "se", "al", "un", "una",
         "constitucional", "republica", "comision", "senado", "camara", "representantes", "sesion", "plenaria"}


def _novel_words(text: str, title: str) -> set[str]:
    words = lambda t: {w for w in fold(re.sub(r"[^\w\s]", " ", t)).split() if len(w) > 2 and not w.isdigit()}  # noqa: E731
    return words(text) - words(title) - _STOP - set(MONTHS)


def _topic(info_topic: str | None, title: str, description: str | None) -> str | None:
    """Tema de la sesión: del título si lo trae; si no, de la descripción solo cuando aporta algo
    (al menos tres palabras con contenido que el título no tiene); nunca una repetición del título."""
    if info_topic:
        return info_topic
    rest = _beyond_title(title, description or "")
    return rest[:300] if len(_novel_words(rest, title)) >= 3 else None


def parse_feed(content: bytes, channel_id: str, channel_name: str, channel_kind: str) -> ParseResult:
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        return ParseResult(issues=[Issue("XML_DTD", "el feed declara DTD o entidades: se rechaza sin interpretarlo",
                                         IssueSeverity.QUARANTINE)])
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        return ParseResult(issues=[Issue("XML_INVALID", f"XML inválido: {exc}", IssueSeverity.SCHEMA_CHANGE)])
    if root.tag != f"{{{NS['a']}}}feed":
        return ParseResult(issues=[Issue("NOT_ATOM", "la respuesta no es un feed Atom", IssueSeverity.SCHEMA_CHANGE)])
    candidates, issues = [], []
    for entry in root.findall("a:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS) or ""
        title = (entry.findtext("a:title", namespaces=NS) or "").strip()
        if not _VIDEO_ID.match(video_id) or not title:
            issues.append(Issue("ENTRY_INCOMPLETE", "entrada sin identificador de video o título", pointer=video_id or None))
            continue
        published = entry.findtext("a:published", namespaces=NS)
        description = clean_description(entry.findtext("media:group/media:description", namespaces=NS))
        info = parse_title(title, channel_kind)
        published_at = datetime.fromisoformat(published) if published else None
        value = {
            "video_id": video_id, "channel_id": channel_id, "channel_name": channel_name, "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}", "published_at": published,
            "description": description, "kind": info.kind, "corporation": info.corporation, "body": info.body,
            "body_key": info.body_key, "session_date": info.session_date.isoformat() if info.session_date else None,
            "topic": _topic(info.topic, title, description),
        }
        candidates.append(NormalizedCandidate(
            "video", f"youtube:{video_id}", "session_video", value,
            # El hecho es la sesión (fecha del título); la publicación en YouTube es otra fecha (FE-SEM-06).
            effective=PartialDate.of_day(info.session_date) if info.session_date else PartialDate.unknown(),
            published_on=PartialDate.of_day(published_at.date()) if published_at else PartialDate.unknown(),
            record_pointer=video_id))
    return ParseResult(candidates=candidates, issues=issues, empty_source=not candidates and not issues)


class YoutubeCongresoConnector:
    adapter = "youtube_congreso"
    version = "0.1.0"
    parser_version = "youtube-congreso-parser-1"

    def __init__(self, fetcher: SafeFetcher) -> None:
        self._fetcher = fetcher

    def validate_source(self, config: Mapping[str, Any]) -> ValidationReport:
        return ValidationReport(capabilities=["discovery", "metadata"], diagnostics=[])

    def discover(self, cursor: Cursor) -> DiscoverPage:
        if cursor.position.get("done"):
            return DiscoverPage([], cursor, has_more=False)
        items = [DiscoveredItem("channel_feed", f"youtube-feed:{cid}", FEED.format(cid), external_id=cid,
                                accept_mimes=XML, hints={"channel_id": cid, "name": name, "kind": kind})
                 for cid, name, kind in CHANNELS]
        return DiscoverPage(items, cursor.advance(done=True), has_more=True)

    def fetch(self, item: DiscoveredItem, *, etag: str | None = None, last_modified: str | None = None) -> Fetched | NotModified:
        return self._fetcher.fetch(item.url, etag=etag, last_modified=last_modified, accept_mimes=XML)

    def parse(self, item: DiscoveredItem, snapshot: Fetched) -> ParseResult:
        return parse_feed(snapshot.content, item.hints["channel_id"], item.hints["name"], item.hints["kind"])
