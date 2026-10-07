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
    assert "Sesiones de este día" in text and "Plenaria (Cámara)" in text and "Moción de Censura MinDefensa" in text
    assert "Comisiones económicas conjuntas (Congreso)" in text  # misma fecha, comisiones conjuntas
    assert view.status == "ready" and any(b.url for row in view.rows for b in row)


def test_day_without_session_video_shows_no_empty_section(loaded) -> None:
    view = _app(loaded["db"], loaded["query"]).handle(
        UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button", parameters={"corporation": "senado", "date": "2026-09-28"}),
        SessionContext())
    assert "Sesiones de este día" not in view.blocks[0]


def test_senate_day_includes_its_committee_and_unattributed_hearings(loaded) -> None:
    rpc = loaded["db"].rpc(loaded["query"])
    videos = rpc.call("bot_session_videos", {"p_corporation": "senado", "p_from": "2026-10-01", "p_to": "2026-10-01"})
    bodies = {(v["kind"], v["body_key"], v["corporation"]) for v in videos}
    assert ("sesion", "comision_7", "senado") in bodies and ("audiencia", None, None) in bodies
    assert all(v["corporation"] != "camara" for v in videos)  # la plenaria de la Cámara no aparece en el Senado
    assert date.fromisoformat(videos[0]["session_date"]) == date(2026, 10, 1)


def test_today_view_says_last_and_next_session(loaded) -> None:
    """La Cámara no sesiona todos los días: «hoy» dice cuál fue la última sesión (con su video y tema) y que
    su agenda no está en nuestras fuentes, en vez de un día vacío sin contexto."""
    engine = SimpleNamespace(rpc=loaded["db"].rpc(loaded["query"]), gacetas_published=lambda corp, day: [])
    app = UiApplication(engine, today=lambda: date(2026, 10, 6))  # type: ignore[arg-type]
    view = app.handle(UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button", parameters={"corporation": "camara"}),
                      SessionContext())
    text = view.blocks[0]
    assert "📅 Sesiones" in text and "Última sesión con video oficial" in text
    assert "jueves 1 de octubre de 2026" in text and "Sesión Especial de la Oposición" in text
    assert "no publica su agenda en nuestras fuentes" in text
    last = [b for row in view.rows for b in row if b.label == "📅 Última sesión"]
    assert last and last[0].params == {"corporation": "camara", "date": "2026-10-01"}



def test_video_and_acta_of_the_same_session_are_one_line(loaded) -> None:
    """El video (SRC-26) y el acta en la Gaceta (SRC-03) de la misma sesión se muestran juntos; una fecha antigua
    sin video muestra solo el acta. Un acta de otro cuerpo el mismo día no se mezcla."""
    db = loaded["db"]
    ingest = db.create_user("ingest_service")
    rows = [{"document_key": "gaceta:camara:2026:1400", "gaceta_url": "https://svrpubindc.imprenta.gov.co/senado/x?num=1400",
             "number": "41", "year": 2026, "session_date": "2026-09-22", "corporation": "camara", "body": "Plenaria",
             "body_key": "plenaria", "pdf_page": 2, "published_on": "2026-10-20"},
            {"document_key": "gaceta:camara:2026:1401", "gaceta_url": "https://svrpubindc.imprenta.gov.co/senado/x?num=1401",
             "number": "9", "year": 2026, "session_date": "2026-09-22", "corporation": "camara", "body": "Comisión Tercera",
             "body_key": "comision_3", "pdf_page": 1, "published_on": "2026-10-21"},
            {"document_key": "gaceta:camara:2025:600", "gaceta_url": "https://svrpubindc.imprenta.gov.co/senado/x?num=600",
             "number": "3", "year": 2025, "session_date": "2025-05-21", "corporation": "camara", "body": "Plenaria",
             "body_key": "plenaria", "pdf_page": 1, "published_on": "2025-06-10"}]
    assert db.rpc(ingest).call("ingest_session_actas", {"p_rows": rows}) == 3
    app = _app(db, loaded["query"])
    text = app.handle(UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button",
                               parameters={"corporation": "camara", "date": "2026-09-22"}), SessionContext()).blocks[0]
    plenary = text.split("Plenaria (Cámara)")[1].split("•")[0]
    assert "▶️ video oficial" in plenary and "Acta 41/2026 · Gaceta 1400/2026, p. 2" in plenary
    assert "Comisión Tercera (Cámara)" in text and "Acta 9/2026" in text.split("Comisión Tercera (Cámara)")[1]
    old = app.handle(UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button",
                              parameters={"corporation": "camara", "date": "2025-05-21"}), SessionContext()).blocks[0]
    assert "Acta 3/2025" in old and "▶️ video oficial" not in old


def test_acta_search_phrase_scope_and_empty(loaded) -> None:
    """Búsqueda en el texto de las actas: frase exacta primero, acotada a una acta, y «sin resultados» distinto de error."""
    db = loaded["db"]
    ingest = db.create_user("ingest_service")
    rpc = db.rpc(ingest)
    rpc.call("ingest_session_actas", {"p_rows": [
        {"document_key": "gaceta:senado:2026:900", "gaceta_url": "https://svrpubindc.imprenta.gov.co/senado/x?num=900",
         "number": "7", "year": 2026, "session_date": "2026-05-05", "corporation": "senado", "body": "Comisión Sexta",
         "body_key": "comision_6", "pdf_page": 1, "published_on": "2026-06-01"},
        {"document_key": "gaceta:senado:2026:900", "gaceta_url": "https://svrpubindc.imprenta.gov.co/senado/x?num=900",
         "number": "8", "year": 2026, "session_date": "2026-05-12", "corporation": "senado", "body": "Comisión Sexta",
         "body_key": "comision_6", "pdf_page": 20, "published_on": "2026-06-01"}]})
    rpc.call("ingest_acta_passages", {"p_rows": [
        {"id": "00000000-0000-4000-8000-000000000001", "document_key": "gaceta:senado:2026:900", "pdf_page_start": 5,
         "pdf_page_end": 5, "text": "Intervino la senadora Paloma Valencia sobre la reforma pensional y la votación nominal."},
        {"id": "00000000-0000-4000-8000-000000000002", "document_key": "gaceta:senado:2026:900", "pdf_page_start": 22,
         "pdf_page_end": 22, "text": "La paloma de la paz y la Comunidad Valenciana fueron mencionadas en otra sesión."}]})
    q = db.rpc(loaded["query"])
    phrase = q.call("bot_acta_search", {"p_query": "Paloma Valencia"})
    assert phrase["mode"] == "frase" and phrase["known_total"] == 1  # no mezcla «paloma» y «valenciana» sueltas
    hit = phrase["items"][0]
    assert hit["pdf_page"] == 5 and hit["session"]["acta_number"] == "7" and "⟦Paloma⟧" in hit["snippet"]
    second = q.call("bot_acta_search", {"p_query": "paloma", "p_document_key": "gaceta:senado:2026:900", "p_page_from": 20})
    assert [i["session"]["acta_number"] for i in second["items"]] == ["8"]  # la página fija el acta del fragmento
    assert q.call("bot_acta_search", {"p_query": "votacion"})["known_total"] == 1  # sin tilde encuentra «votación»

    app = _app(db, loaded["query"])
    view = app.handle(UiAction(intent=Intent.ACTA_SEARCH, entry_point="text", parameters={"query": "xyzzy"}), SessionContext())
    assert view.status == "empty" and "No aparece «xyzzy»" in view.blocks[0] and "no prueba que no se dijo" in view.blocks[0]
    day = app.handle(UiAction(intent=Intent.DAY_OVERVIEW, entry_point="button",
                              parameters={"corporation": "senado", "date": "2026-05-05"}), SessionContext())
    buttons = [b for row in day.rows for b in row if b.intent is Intent.ACTA_SEARCH_PROMPT]
    assert buttons and buttons[0].params["acta_number"] == "7"
