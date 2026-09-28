"""Normalizador de SRC-06 sobre muestras reales (SRS-F07, F10; T-06): proyectos, vínculos, estado y autoría."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from botcentro.connectors.camara_proyectos import CamaraProyectosConnector
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore
from tests.integration.support import Db
from tests.unit.test_camara_proyectos import FakeCamara

pytestmark = pytest.mark.db
STEPS = ["projects", "status", "authors"]


class OnePage(FakeCamara):
    """Una sola página AJAX para la prueba (total_pages = 1)."""

    def fetch(self, url, *, method="GET", data=None, accept_mimes=None, **kw):
        result = super().fetch(url, method=method, data=data, accept_mimes=accept_mimes)
        if data and data.get("action") == "get_proyectos_ley_page":
            import json
            body = json.loads(result.content)
            body["data"]["total_pages"] = 1
            from tests.unit.test_camara_proyectos import fetched
            return fetched(json.dumps(body).encode(), "application/json")
        return result


def _normalize(rpc) -> None:
    for step in STEPS:
        while True:
            counts = rpc.call("normalize_camara_pl", {"p_step": step, "p_limit": 20})
            if not any(v for k, v in counts.items() if k != "step"):
                break


@pytest.fixture(scope="module")
def loaded(db: Db, tmp_path_factory):
    from tests.integration.factories import activate_source

    admin, ingest = db.create_user("admin"), db.create_user("ingest_service")
    db.execute("insert into public.corporations (code, name) values ('senado', 'Senado'), ('camara', 'Cámara') "
               "on conflict do nothing")
    source = db.execute("insert into public.sources (code, name, authority, phase, base_url, allowed_domains, adapter, "
                        "adapter_version, owner) values ('SRC-06', 'Cámara PL', 'primary', 'mvp', "
                        "'https://www.camara.gov.co/', '{www.camara.gov.co}', 'camara_proyectos', '0.1.0', 'x') "
                        "returning id")[0]["id"]
    activate_source(db, source, admin)
    profile = UsageProfile(*[Permission.ALLOWED] * 6, reviewed_at=datetime.now(timezone.utc), reviewer="r")
    runner = IngestionRunner(source_id=source, connector=CamaraProyectosConnector(OnePage()),  # type: ignore[arg-type]
                             store=IngestStore(db.rpc(ingest)), objects=LocalObjectStore(tmp_path_factory.mktemp("o")),
                             profile=profile)
    summary = runner.run(mode="backfill", scope={})
    assert summary.status == "partial"  # hay filas en cuarentena (sufijo contradictorio)
    _normalize(db.rpc(ingest))
    return ingest


def test_projects_titles_and_explicit_links(db: Db, loaded) -> None:
    rows = db.execute("""select (select count(*) from public.projects) projects,
                                (select count(*) from public.projects where canonical_title is not null) titled,
                                (select count(*) from public.project_identifiers) identifiers""")[0]
    assert rows["projects"] >= 30 and rows["titled"] == rows["projects"]
    pairs = db.execute("""select array_agg(c.code order by c.code) corps from public.project_identifiers pi
                          join public.corporations c on c.id = pi.corporation_id group by pi.project_id having count(*) > 1""")
    assert pairs and all(p["corps"] == ["camara", "senado"] for p in pairs)


def test_status_keeps_raw_label_and_projection(db: Db, loaded) -> None:
    rows = db.execute("""select s.status_raw, s.status_normalized from public.project_status_projection p
                         join public.project_status_observations s on s.id = p.selected_status_id""")
    assert rows and all(r["status_raw"] for r in rows)
    assert {"in_committee"} <= {r["status_normalized"] for r in rows}


def test_authors_are_identified_representatives(db: Db, loaded) -> None:
    rows = db.execute("""select count(*) n, count(distinct pp.person_id) people from public.project_participants pp
                         where pp.role = 'autor' and pp.person_id is not null""")[0]
    assert rows["n"] > 0 and rows["people"] > 0
    ids = db.execute("select count(*) n from public.person_identifiers pi join public.sources s on s.id = pi.source_id "
                     "where s.code = 'SRC-06'")[0]["n"]
    assert ids == rows["people"] or ids >= rows["people"]


def test_normalizer_is_idempotent(db: Db, loaded) -> None:
    rpc = db.rpc(loaded)
    for step in STEPS:
        counts = rpc.call("normalize_camara_pl", {"p_step": step, "p_limit": 20})
        assert not any(v for k, v in counts.items() if k != "step"), counts


def test_existing_senado_number_receives_camara_number(db: Db, loaded) -> None:
    source = db.execute("select id from public.sources where code = 'SRC-06'")[0]["id"]
    pre = db.execute("insert into public.projects (initiative_type) values ('proyecto_ley') returning id")[0]["id"]
    db.execute("insert into public.project_identifiers (project_id, corporation_id, initiative_type, number, number_raw, "
               "filing_year, source_id) select %s, id, 'proyecto_ley', '901', '901', 2025, %s from public.corporations "
               "where code = 'senado'", (pre, source))
    obs = db.execute("select id from public.observations limit 1")[0]["id"]
    project = db.execute("select public.link_project_refs(array['PL 902/2025 Cámara', 'PL 901/2025 Senado'], true, %s, %s, 'x') p",
                         (obs, source))[0]["p"]
    assert project == pre
    corps = db.execute("select count(*) n from public.project_identifiers where project_id = %s", (pre,))[0]["n"]
    assert corps == 2


class FakeEmbedder:
    def passage_measure(self, text: str) -> int:
        return len(text.split()) * 2

    def embed_passages(self, chunks):
        from botcentro.embeddings.e5 import PassageVector
        vec = [1.0] + [0.0] * 383
        return [PassageVector(str(c.id), vec, 10, False) for c in chunks]


def test_fichas_become_indexed_documents_linked_to_projects(db: Db, loaded) -> None:
    from botcentro.costs.budget import BudgetGuard
    from botcentro.documents.fichas import FichaIndexer

    db.execute("insert into public.budgets (name, provider, period, limit_amount) values ('local-e5', 'local', "
               "'daily', 0) on conflict do nothing")
    rpc = db.rpc(loaded)
    indexer = FichaIndexer(rpc, FakeEmbedder(), BudgetGuard(rpc), batch=20)
    totals = {"created": 0, "chunks": 0, "embeddings": 0}
    while (counts := indexer.run_batch()) is not None:
        for k in totals:
            totals[k] += counts[k]
    published = db.execute("select count(distinct subject_ref) n from public.observations "
                           "where predicate = 'project_profile' and status = 'published'")[0]["n"]
    assert totals["created"] == published and totals["chunks"] == totals["embeddings"] >= published
    linked = db.execute("""select count(distinct c.id) n from public.chunks c
                           join public.chunk_project_links l on l.chunk_id = c.id""")[0]["n"]
    assert linked == totals["chunks"]
    sample = db.execute("select text from public.document_pages limit 1")[0]["text"]
    assert sample.startswith("Proyecto:") and "Objeto:" in sample
    # Idempotente: nada pendiente y un reenvío del mismo contenido se omite.
    assert indexer.run_batch() is None
