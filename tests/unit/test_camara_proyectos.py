"""Conector SRC-06 (proyectos de Cámara) contra muestras reales recortadas."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from botcentro.connectors.base import Cursor
from botcentro.connectors.camara_proyectos import CamaraProyectosConnector, parse_numbers, project_key
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FailureKind, FetchError
from botcentro.http.fetcher import Fetched

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "camara_proyectos"


def fetched(content: bytes, mime: str) -> Fetched:
    return Fetched("u", "u", 200, content, sha256_hex(content), mime, mime, None, None, datetime.now(timezone.utc))


class FakeCamara:
    def __init__(self, stale_first: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.stale_first = stale_first

    def fetch(self, url, *, method="GET", data=None, accept_mimes=None, **kw):
        self.calls.append((method, dict(data or {})))
        if method == "GET":
            return fetched(b'<script>window.PL_CFG = { PL_NONCE : "ab12cd34" };</script>', "text/html")
        if self.stale_first:
            self.stale_first = False
            raise FetchError("HTTP_403", "nonce", kind=FailureKind.PERMISSION, status=403)
        if data["action"] == "download_proyectos_ley_xlsx":
            return fetched((FIXTURES / "listado.xlsx").read_bytes(), "application/zip")
        return fetched((FIXTURES / "ajax_page1.json").read_bytes(), "application/json")


@pytest.mark.parametrize(("raw", "expected"), [
    ("396/2026C", [("396", 2026, "C")]),
    ("396/2026 C", [("396", 2026, "C")]),
    ("123/2025C ACUM 120/2025, 98/2025", [("123", 2025, "C"), ("120", 2025, None), ("98", 2025, None)]),
    ("045/19S", [("45", 2019, "S")]),
    (35, [("35", None, None)]),
    (None, []),
])
def test_parse_numbers(raw, expected) -> None:
    assert parse_numbers(raw) == expected


def test_project_key_without_year_falls_back_to_link() -> None:
    assert project_key("396/2026C", None) == "camara-pl:project:396-2026"
    assert project_key(35, "https://www.camara.gov.co/mi-proyecto") == "camara-pl:project:slug:mi-proyecto"


def test_discovery_xlsx_then_ajax_pages() -> None:
    fake = FakeCamara()
    connector = CamaraProyectosConnector(fake)  # type: ignore[arg-type]
    first = connector.discover(Cursor(connector.version, {}))
    assert [i.record_type for i in first.items] == ["listado_xlsx"] and first.has_more
    second = connector.discover(first.next_cursor)
    assert second.items[0].hints == {"page": 1} and len(second.items) == 20 and second.has_more


def test_xlsx_rows_become_profiles_with_links_and_quarantine() -> None:
    connector = CamaraProyectosConnector(FakeCamara())  # type: ignore[arg-type]
    item = connector.discover(Cursor(connector.version, {})).items[0]
    result = connector.parse(item, connector.fetch(item))
    profiles = [c for c in result.candidates if c.predicate == "project_profile"]
    assert len(profiles) == 45 and not result.schema_changed
    first = profiles[0].value
    assert first["camara_ref"] == "PL 396/2026 Cámara" and first["title"].startswith("Por medio de la cual")
    assert first["object"] and first["link"].startswith("https://www.camara.gov.co/")
    assert profiles[0].effective.iso() == "2026-09-23"
    linked = [p for p in profiles if p.value["explicit_link"]]
    assert all(p.value["senado_ref"].endswith("Senado") for p in linked)
    assert any(p.value["accumulated_refs"] for p in profiles)
    quarantined = [p for p in profiles if p.quarantine_reason]
    assert quarantined and all("sufijo de Cámara" in p.quarantine_reason for p in quarantined)
    acts = [p for p in profiles if p.value["initiative_type"] == "proyecto_acto_legislativo"]
    assert all(p.value["camara_ref"] is None or p.value["camara_ref"].startswith("PAL") for p in acts)


def test_ajax_page_yields_authors_with_ids() -> None:
    connector = CamaraProyectosConnector(FakeCamara())  # type: ignore[arg-type]
    item = connector.discover(connector.discover(Cursor(connector.version, {})).next_cursor).items[0]
    result = connector.parse(item, connector.fetch(item))
    authorships = [c for c in result.candidates if c.predicate == "authorship"]
    people = {c.subject_ref for c in result.candidates if c.predicate == "representative_seen"}
    assert authorships and people
    assert all(a.value["person_ref"] in people for a in authorships)
    assert authorships[0].value["project_ref"] == "camara-pl:project:396-2026"


def test_expired_nonce_is_refreshed_once() -> None:
    fake = FakeCamara(stale_first=True)
    connector = CamaraProyectosConnector(fake)  # type: ignore[arg-type]
    item = connector.discover(Cursor(connector.version, {})).items[0]
    assert connector.fetch(item).sniffed_mime == "application/zip"
    assert [m for m, _ in fake.calls] == ["GET", "POST", "GET", "POST"]
