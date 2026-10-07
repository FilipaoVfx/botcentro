"""Videos oficiales de sesiones (SRC-26) de punta a punta: feed real → observaciones → tabla → vistas del bot."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from botcentro.connectors.youtube_congreso import YoutubeCongresoConnector
from botcentro.domain.hashing import sha256_hex
from botcentro.http.fetcher import Fetched
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore
from botcentro.telegram_ui.app import UiApplication
from botcentro.telegram_ui.contracts import Intent, SessionContext, UiAction
from tests.integration.support import Db

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "youtube"
FEEDS = {"UCJfKMFp7dtO8mpHT56X0kJQ": "canal-congreso.xml", "UCzescUFTh2jiZkYIx9uZtqA": "camara.xml"}


class FeedFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url, *, etag=None, last_modified=None, accept_mimes=None, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        content = (FIX / FEEDS[url.rsplit("=", 1)[1]]).read_bytes()
        return Fetched(url, url, 200, content, sha256_hex(content), "application/xml", "application/xml", None, None,
                       datetime.now(timezone.utc))


@pytest.fixture(scope="module")
def loaded(db: Db, tmp_path_factory):
    db.execute("insert into public.corporations (code, name) values ('senado', 'Senado'), ('camara', 'Cámara') "
               "on conflict do nothing")
    db.execute((ROOT / "seeds" / "fuente_videos.sql").read_text())
    db.execute("update public.sources set shadow_mode = false where code = 'SRC-26'")
    ingest, query = db.create_user("ingest_service"), db.create_user("query_service")
    source = db.execute("select id from public.sources where code = 'SRC-26'")[0]["id"]
    fetcher = FeedFetcher()

    def run():  # noqa: ANN202
        runner = IngestionRunner(source_id=source, connector=YoutubeCongresoConnector(fetcher), store=IngestStore(db.rpc(ingest)),
                                 objects=LocalObjectStore(tmp_path_factory.mktemp("o")),
                                 profile=UsageProfile(Permission.ALLOWED, *[Permission.DENIED] * 4, Permission.ALLOWED,
                                                      reviewed_at=datetime.now(timezone.utc), reviewer="r"))
        summary = runner.run(mode="backfill", scope={})
        while db.rpc(ingest).call("normalize_session_videos", {"p_step": "session_videos", "p_limit": 200})["session_videos"]:
            pass
        return summary

    first = run()
    return {"db": db, "query": query, "first": first, "run": run, "fetcher": fetcher}


def test_feeds_load_once_and_rerun_is_idempotent(loaded) -> None:
    db = loaded["db"]
    assert loaded["first"].status == "succeeded" and loaded["fetcher"].calls == 2  # un feed por canal, no por ventana
    counts = db.execute("select kind, count(*) n from public.session_videos group by kind")
    assert {r["kind"]: r["n"] for r in counts} == {"sesion": 8, "audiencia": 3, "programa": 19}
    before = db.execute("select count(*) n from public.session_videos")[0]["n"]
    loaded["run"]()
    assert db.execute("select count(*) n from public.session_videos")[0]["n"] == before == 30


def _app(db: Db, user) -> UiApplication:  # noqa: ANN001
    engine = SimpleNamespace(rpc=db.rpc(user), gacetas_published=lambda corp, day: [])
    return UiApplication(engine)  # type: ignore[arg-type]


def test_camara_day_shows_the_plenary_with_its_topic(loaded) -> None:
    """La Cámara no tiene votaciones ni agenda en nuestras fuentes: el video confirma la plenaria y su tema."""
    view = _app(loaded["db"], loaded["query"]).handle(
        UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button", parameters={"corporation": "camara", "date": "2026-09-22"}),
        SessionContext())
    text = view.blocks[0]
    assert "Sesiones en video oficial" in text and "Plenaria (Cámara)" in text and "Moción de Censura MinDefensa" in text
    assert "Comisiones económicas conjuntas (Congreso)" in text  # misma fecha, comisiones conjuntas
    assert view.status == "ready" and any(b.url for row in view.rows for b in row)


def test_day_without_session_video_shows_no_empty_section(loaded) -> None:
    view = _app(loaded["db"], loaded["query"]).handle(
        UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button", parameters={"corporation": "senado", "date": "2026-09-28"}),
        SessionContext())
    assert "Sesiones en video oficial" not in view.blocks[0]


def test_senate_day_includes_its_committee_and_unattributed_hearings(loaded) -> None:
    rpc = loaded["db"].rpc(loaded["query"])
    videos = rpc.call("bot_session_videos", {"p_corporation": "senado", "p_from": "2026-10-01", "p_to": "2026-10-01"})
    bodies = {(v["kind"], v["body_key"], v["corporation"]) for v in videos}
    assert ("sesion", "comision_7", "senado") in bodies and ("audiencia", None, None) in bodies
    assert all(v["corporation"] != "camara" for v in videos)  # la plenaria de la Cámara no aparece en el Senado
    assert date.fromisoformat(videos[0]["session_date"]) == date(2026, 10, 1)
