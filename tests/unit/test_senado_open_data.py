"""Conector SRC-01 contra muestras reales de la API del Senado (tests/fixtures/senado_open_data)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from botcentro.connectors.base import Cursor, IssueSeverity
from botcentro.connectors.senado_open_data import SenadoOpenDataConnector
from botcentro.domain.hashing import sha256_hex
from botcentro.http.fetcher import Fetched

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "senado_open_data"
SCOPE = {"from": "2026-09-01", "to": "2026-09-30", "window_days": 7}


class NoFetch:
    def fetch(self, *a, **k):  # pragma: no cover - el parser no descarga
        raise AssertionError("no debe descargar")


@pytest.fixture()
def connector() -> SenadoOpenDataConnector:
    return SenadoOpenDataConnector(NoFetch(), today=lambda: date(2026, 10, 15))  # type: ignore[arg-type]


def snapshot(content: bytes) -> Fetched:
    return Fetched("u", "u", 200, content, sha256_hex(content), "application/json", "application/json", None, None,
                   datetime.now(timezone.utc))


def parse_fixture(connector, record_type: str):
    page = connector.discover(Cursor(connector.version, SCOPE))
    item = next((i for i in page.items if i.record_type == record_type), None)
    if item is None:
        item = next(i for i in connector.discover(Cursor(connector.version, SCOPE, {"page": 1})).items
                    if i.record_type == record_type)
    return connector.parse(item, snapshot((FIXTURES / f"{record_type}.json").read_bytes()))


def test_discovery_never_requests_without_dates(connector) -> None:
    cursor = Cursor(connector.version, SCOPE)
    first = connector.discover(cursor)
    assert [i.record_type for i in first.items] == ["senators", "commissions"] and first.has_more
    windows, cursor = [], first.next_cursor
    while True:
        page = connector.discover(cursor)
        windows.append(page.items[0].hints)
        assert all("start_at=" in i.url and "end_at=" in i.url for i in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert windows[0] == {"from": "2026-09-01", "to": "2026-09-07"}
    assert windows[-1] == {"from": "2026-09-29", "to": "2026-09-30"}
    assert len(windows) == 5


def test_senators_drop_contact_data(connector) -> None:
    result = parse_fixture(connector, "senators")
    assert len(result.candidates) == 103 and not result.issues
    dumped = json.dumps([c.value for c in result.candidates], ensure_ascii=False)
    assert "@senado.gov.co" not in dumped and "phone" not in dumped


def test_votes_are_nominal_with_one_voting_act_and_explicit_links(connector) -> None:
    result = parse_fixture(connector, "votes")
    votes = [c for c in result.candidates if c.predicate == "nominal_vote"]
    seen = {c.subject_ref: c.value["name"] for c in result.candidates if c.predicate == "senator_seen"}
    assert seen and all(ref.startswith("senado-od:senator:") for ref in seen)
    votings = [c for c in result.candidates if c.predicate == "voting_subject"]
    assert len(votes) == 400 and not result.issues
    assert {c.value["vote"] for c in votes} <= {"yes", "no"}
    assert len(votings) == len({c.subject_ref for c in votings})  # un acto por (plenaria, proyecto)
    assert all(c.effective.value == date(2026, 9, 1) or c.effective.value >= date(2026, 9, 1) for c in votes)
    linked = [c for c in votings if c.value["explicit_link"]]
    for c in linked:
        assert any("Senado" in r for r in c.value["project_refs"]) and any("Cámara" in r for r in c.value["project_refs"])


def test_attendance_no_is_explicit_absence(connector) -> None:
    result = parse_fixture(connector, "assistances")
    statuses = {c.value["status_raw"]: c.value["status"] for c in result.candidates if c.predicate == "attendance"}
    assert statuses == {"Si": "present", "No": "absent"}


def test_events_are_scheduled_with_bogota_time(connector) -> None:
    result = parse_fixture(connector, "events")
    first = result.candidates[0]
    assert first.value["status"] == "scheduled"
    assert first.effective_at is not None and first.effective_at.utcoffset().total_seconds() == -5 * 3600


def test_unknown_vote_label_is_quarantined_and_missing_fields_flag_schema_change(connector) -> None:
    rows = json.loads((FIXTURES / "votes.json").read_text())[:2]
    rows[0]["vote"] = "Abstención"
    del rows[1]["vote"]
    page = connector.discover(Cursor(connector.version, SCOPE, {"page": 1}))
    item = next(i for i in page.items if i.record_type == "votes")
    result = connector.parse(item, snapshot(json.dumps(rows).encode()))
    quarantined = [c for c in result.candidates if c.quarantine_reason]
    assert len(quarantined) == 1 and "Abstención" in quarantined[0].quarantine_reason
    assert result.schema_changed and result.issues[0].severity is IssueSeverity.SCHEMA_CHANGE


def _by_name(page) -> dict:  # noqa: ANN001
    return {item.record_type: item.hints for item in page.items}


def test_votes_never_ask_future_dates_but_agenda_looks_ahead() -> None:
    """Verificado 2026-10-06: votos y asistencias con fecha futura dan 400; la agenda acepta un final futuro."""
    connector = SenadoOpenDataConnector(NoFetch(), today=lambda: date(2026, 9, 28))  # type: ignore[arg-type]
    cursor, last = connector.discover(Cursor(connector.version, SCOPE)).next_cursor, None
    while True:
        page = connector.discover(cursor)
        last = _by_name(page)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert last["votes"] == last["assistances"] == {"from": "2026-09-22", "to": "2026-09-28"}
    assert last["events"] == {"from": "2026-09-22", "to": "2026-10-19"}  # 21 días de agenda por delante


def test_window_starting_today_is_moved_back_one_day() -> None:
    """La API exige start_at anterior a hoy: una ventana de solo hoy se pide desde ayer."""
    connector = SenadoOpenDataConnector(NoFetch(), today=lambda: date(2026, 9, 28))  # type: ignore[arg-type]
    scope = {"from": "2026-09-14", "to": "2026-09-28", "window_days": 7}
    cursor = connector.discover(Cursor(connector.version, scope)).next_cursor
    windows = []
    while True:
        page = connector.discover(cursor)
        windows.append(_by_name(page)["votes"])
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert windows[-1] == {"from": "2026-09-27", "to": "2026-09-28"}
    assert all(w["from"] < "2026-09-28" for w in windows)


def test_empty_week_is_valid_not_a_schema_change(connector) -> None:
    page = connector.discover(Cursor(connector.version, SCOPE, {"page": 1}))
    result = connector.parse(page.items[0], snapshot(b"[]"))
    assert result.empty_source and not result.is_empty_mapping and not result.schema_changed


def test_empty_range_400_is_a_valid_empty_capture() -> None:
    from botcentro.errors import FailureKind, FetchError

    body = '{"error":"No existen asistencias relacionadas en el rango de fechas seleccionados"}'.encode()

    class EmptyRange:
        def fetch(self, url, **kwargs):
            raise FetchError("HTTP_400", "400", kind=FailureKind.INVALID_CONTENT, status=400, body=body)

    connector = SenadoOpenDataConnector(EmptyRange(), today=lambda: date(2026, 10, 1))  # type: ignore[arg-type]
    item = connector.discover(Cursor(connector.version, SCOPE, {"page": 1})).items[2]
    fetched = connector.fetch(item)
    assert fetched.status == 400 and fetched.content == body
    assert connector.parse(item, fetched).empty_source


def test_other_400_errors_still_fail() -> None:
    from botcentro.errors import FailureKind, FetchError

    class Broken:
        def fetch(self, url, **kwargs):
            raise FetchError("HTTP_400", "400", kind=FailureKind.INVALID_CONTENT, status=400, body=b'{"error":"fecha invalida"}')

    connector = SenadoOpenDataConnector(Broken(), today=lambda: date(2026, 10, 1))  # type: ignore[arg-type]
    item = connector.discover(Cursor(connector.version, SCOPE, {"page": 1})).items[0]
    with pytest.raises(FetchError):
        connector.fetch(item)
