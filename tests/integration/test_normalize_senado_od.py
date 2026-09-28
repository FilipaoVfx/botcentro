"""Normalizador de SRC-01 sobre muestras reales cargadas por el runner (SRS-F07..F11; T-02, T-06, T-08)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from botcentro.connectors.senado_open_data import SenadoOpenDataConnector
from botcentro.domain.hashing import sha256_hex
from botcentro.http.fetcher import Fetched
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore
from tests.integration.support import Db

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "senado_open_data"
STEPS = ["catalog", "projects", "votes", "attendance", "agenda"]


class FixtureFetcher:
    """Sirve cada conjunto desde su muestra real, filtrado por la ventana pedida."""

    DATE_FIELD = {"events": "date", "votes": "created_at", "assistances": "plenary_created_at"}

    def fetch(self, url, *, etag=None, last_modified=None, accept_mimes=None):
        name = url.split("/v1/")[1].split("?")[0]
        rows = json.loads((FIXTURES / f"{name}.json").read_text())
        if name in self.DATE_FIELD:
            q = dict(p.split("=") for p in url.split("?")[1].split("&"))
            rows = [r for r in rows if q["start_at"] <= (r[self.DATE_FIELD[name]] or "") <= q["end_at"]]
        content = json.dumps(rows, ensure_ascii=False).encode()
        return Fetched(url, url, 200, content, sha256_hex(content), "application/json", "application/json", None,
                       None, datetime.now(timezone.utc))


@pytest.fixture(scope="module")
def loaded(db: Db, tmp_path_factory) -> UUID:
    from tests.integration.factories import activate_source

    admin, ingest = db.create_user("admin"), db.create_user("ingest_service")
    db.execute("insert into public.corporations (code, name) values ('senado', 'Senado'), ('camara', 'Cámara') "
               "on conflict do nothing")
    source = db.execute("insert into public.sources (code, name, authority, phase, base_url, allowed_domains, adapter, "
                        "adapter_version, owner) values ('SRC-01', 'Senado OD', 'primary', 'mvp', "
                        "'https://app.senado.gov.co/', '{app.senado.gov.co}', 'senado_open_data', '0.1.0', 'x') "
                        "returning id")[0]["id"]
    activate_source(db, source, admin)
    profile = UsageProfile(*[Permission.ALLOWED] * 5, Permission.UNKNOWN, reviewed_at=datetime.now(timezone.utc),
                           reviewer="r")
    connector = SenadoOpenDataConnector(FixtureFetcher(), today=lambda: date(2026, 9, 30))  # type: ignore[arg-type]
    runner = IngestionRunner(source_id=source, connector=connector, store=IngestStore(db.rpc(ingest)),
                             objects=LocalObjectStore(tmp_path_factory.mktemp("obj")), profile=profile)
    summary = runner.run(mode="backfill", scope={"from": "2026-09-01", "to": "2026-09-30", "window_days": 7})
    assert summary.status == "succeeded"
    rpc = db.rpc(ingest)
    for step in STEPS:
        rpc.call("normalize_senado_od", {"p_step": step})
    return ingest


def test_catalog(db: Db, loaded) -> None:
    counts = db.execute("""select (select count(*) from public.commissions) commissions,
                                  (select count(*) from public.person_terms) terms,
                                  (select count(*) from public.parties) parties,
                                  (select count(*) from public.person_identifiers) identifiers,
                                  (select count(*) from public.persons) persons""")[0]
    assert counts["commissions"] == 18 and counts["terms"] == 103 and counts["parties"] >= 10
    assert counts["persons"] == counts["identifiers"] >= 103  # senadores vigentes + los vistos en votos


def test_votes_are_nominal_and_linked_to_projects(db: Db, loaded) -> None:
    rows = db.execute("""select (select count(*) from public.vote_observations) votes,
                                (select count(*) from public.current_votes) current_votes,
                                (select count(*) from public.current_votes where conflict) conflicts,
                                (select count(*) from public.votings) votings,
                                (select count(*) from public.sessions) sessions""")[0]
    assert rows["votes"] == 400 and rows["current_votes"] == 400 and rows["conflicts"] == 0
    assert rows["votings"] >= 1 and rows["sessions"] >= 1
    # Cada votación de proyecto queda enlazada al expediente de su numeración oficial.
    unlinked = db.execute("""select count(*) n from public.votings v where v.subject_type = 'project'
                             and not exists (select 1 from public.voting_projects vp where vp.voting_id = v.id)""")
    assert unlinked[0]["n"] == 0


def test_explicit_senado_camara_numbers_share_one_project(db: Db, loaded) -> None:
    pairs = db.execute("""select p.id, array_agg(c.code order by c.code) corps from public.projects p
                          join public.project_identifiers pi on pi.project_id = p.id
                          join public.corporations c on c.id = pi.corporation_id
                          group by p.id having count(*) > 1""")
    for pair in pairs:
        assert pair["corps"] == ["camara", "senado"]


def test_attendance_and_agenda(db: Db, loaded) -> None:
    rows = db.execute("""select (select count(*) from public.attendance_observations) att,
                                (select count(*) from public.agenda_items) items,
                                (select count(*) from public.agenda_revisions) revisions,
                                (select count(*) from public.agenda_items where commission_id is not null) with_commission""")[0]
    assert rows["att"] == 927 and rows["items"] == 98 and rows["revisions"] == 98
    assert rows["with_commission"] > 0


def test_normalizer_is_idempotent(db: Db, loaded) -> None:
    rpc = db.rpc(loaded)
    results = [rpc.call("normalize_senado_od", {"p_step": step}) for step in STEPS]
    for result in results:
        assert all(v == 0 for k, v in result.items() if k != "step"), result


def test_only_ingest_service_can_normalize(db: Db, loaded) -> None:
    from botcentro.insforge.client import PermissionDenied

    with pytest.raises(PermissionDenied):
        db.rpc(db.create_user("query_service")).call("normalize_senado_od", {"p_step": "catalog"})
