"""Bot de Telegram de punta a punta contra SQL y Redis reales (DEC-16, DEC-18; centrorequirement.md §29).

Texto → aceptación seudonimizada → cola → vista → respuesta, segmentos y entregas registradas.
Clic → token opaco → dueño → autorización → edición del mensaje. Telegram se sustituye por un
transporte en memoria; el índice vectorial, por un doble.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from itertools import count

import pytest

from botcentro.domain.dates import BOGOTA
from botcentro.query.engine import AnswerEngine
from botcentro.telegram.security import pseudonymize_user
from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings
from botcentro.telegram_ui.app import UiApplication
from botcentro.telegram_ui.runtime import UiRuntime, keyboard_texts
from botcentro.telegram_ui.state import UiState
from tests.integration.support import Db
from tests.integration.test_normalize_camara_pl import loaded  # noqa: F401 — fixture: fichas de Cámara cargadas

pytestmark = pytest.mark.db
BOT_ID = 777
KEY = b"k" * 32
ALLOWED, STRANGER, OTHER_ALLOWED = 1001, 2002, 3003
REDIS_URL = os.environ.get("BOTCENTRO_TEST_REDIS_URL")


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.answers: list[dict] = []
        self._ids = count(1)

    async def send(self, chat_id, html, keyboard):
        message_id = next(self._ids)
        self.sent.append({"chat": chat_id, "html": html, "keyboard": keyboard, "id": message_id})
        return message_id

    async def edit(self, chat_id, message_id, html, keyboard):
        self.edits.append({"chat": chat_id, "id": message_id, "html": html, "keyboard": keyboard})
        return True

    async def answer_callback(self, callback_id, text=None, alert=False):
        self.answers.append({"id": callback_id, "text": text, "alert": alert})


class FakeVectors:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, vector, *, limit=8, flt=None, with_payload=True):
        self.calls.append(flt)
        return self.hits[:limit]

    def scroll(self, *, offset=None, limit=256, flt=None, with_payload=True, with_vector=True):
        return [h for h in self.hits if h["payload"]["doc_kind"] == "gaceta"], None


class Harness:
    def __init__(self, runtime: UiRuntime, transport: FakeTransport, vectors: FakeVectors) -> None:
        self.runtime, self.transport, self.vectors = runtime, transport, vectors
        self._updates = count(10_000)
        self._callbacks = count(1)
        self.loop = asyncio.new_event_loop()

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def say(self, text: str, user: int = ALLOWED, update_id: int | None = None) -> list[dict]:
        before = len(self.transport.sent)
        uid = update_id or next(self._updates)
        self.run(self.runtime.on_update({"update_id": uid, "message": {
            "message_id": uid, "date": 0, "text": text, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False}}}))
        return self.transport.sent[before:]

    def click(self, message: dict, label: str, user: int = ALLOWED, callback_id: str | None = None) -> str:
        data = next(b["callback_data"] for row in message["keyboard"] for b in row if b["text"] == label)
        cid = callback_id or f"cb{next(self._callbacks)}"
        self.run(self.runtime.on_update({"update_id": next(self._updates), "callback_query": {
            "id": cid, "data": data, "from": {"id": user, "is_bot": False},
            "message": {"message_id": message["id"], "date": 0, "chat": {"id": message["chat"], "type": "private"}}}}))
        return cid


@pytest.fixture(scope="module")
def bot(db: Db, loaded):  # noqa: F811
    if not REDIS_URL:
        pytest.skip("BOTCENTRO_TEST_REDIS_URL no configurada")
    from redis.asyncio import Redis

    admin, query = db.create_user("admin"), db.create_user("query_service")
    for user in (ALLOWED, OTHER_ALLOWED):
        db.rpc(admin).call("admin_authorize_telegram", {
            "p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, user), "p_chat_id": user,
            "p_label": "piloto", "p_reason": "prueba de integración"})
    rpc = db.rpc(query)
    ficha = db.execute("select subject_ref, value_json from public.observations where predicate = 'project_profile' "
                       "and status = 'published' and value_json ->> 'camara_ref' is not null limit 1")[0]
    vectors = FakeVectors([{"score": 0.9, "payload": {
        "doc_kind": "ficha", "document_key": f"camara-ficha:{ficha['subject_ref']}",
        "title": ficha["value_json"]["short_name"], "text": "Título: " + ficha["value_json"]["title"], "year": 2026,
        "source_url": ficha["value_json"]["link"], "project_ids": []}}, {"score": 0.88, "payload": {
        "doc_kind": "gaceta", "document_key": "gaceta:senado:2026:1382", "title": "Gaceta 1382 de 2026 (senado)",
        "text": "El presente proyecto de ley tiene por objeto la seguridad hídrica.", "pdf_page_start": 3,
        "source_url": "https://svrpubindc.imprenta.gov.co/senado/index2.xhtml?ent=Senado&fec=24-9-2026&num=1382",
        "project_ids": []}}])
    engine = AnswerEngine(rpc, vectors, lambda text: [0.0] * 384,
                          clock=lambda: datetime(2026, 9, 28, 10, tzinfo=BOGOTA))
    transport = FakeTransport()
    harness_loop = asyncio.new_event_loop()
    redis = Redis.from_url(REDIS_URL)
    state = UiState(redis, bot_id=BOT_ID, namespace=f"test-{os.getpid()}")
    runtime = UiRuntime(transport=transport, state=state, app=UiApplication(engine, today=lambda: date(2026, 9, 28)),
                        intake=TelegramWebhook(WebhookSettings(BOT_ID, "no-se-usa", KEY, rate_limit_per_minute=500), rpc), rpc=rpc,
                        bot_id=BOT_ID, pseudonym_key=KEY, worker_id="test-bot")
    harness = Harness(runtime, transport, vectors)
    harness.loop = harness_loop
    yield harness
    harness.run(redis.aclose())
    harness_loop.close()


def test_project_card_is_answered_and_recorded(db: Db, bot) -> None:
    [reply] = bot.say("PL 396/2026 Cámara")
    assert "PL 396/2026 Cámara" in reply["html"] and "Estado" in reply["html"] and "Autores" in reply["html"]
    assert 'href="https://www.camara.gov.co/' in reply["html"]
    assert "🏠 Inicio" in keyboard_texts(reply["keyboard"])
    run = db.execute("select q.status, a.support_status, d.state, d.provider_message_id "
                     "from public.query_runs q join public.answer_records a on a.query_id = q.id "
                     "join public.delivery_attempts d on d.answer_id = a.id order by q.created_at desc limit 1")[0]
    assert run["status"] == "answered" and run["state"] == "sent" and run["provider_message_id"] == reply["id"]


def test_unknown_project_says_absence_is_not_zero(bot) -> None:
    [reply] = bot.say("¿En qué va el PL 9999/2019 Senado?")
    assert "No encontré" in reply["html"] and "PL 9999/2019 Senado" in reply["html"]


def test_unauthorized_user_gets_their_id_and_no_query(db: Db, bot) -> None:
    before = db.execute("select count(*) n from public.query_runs")[0]["n"]
    [reply] = bot.say("hola", user=STRANGER)
    assert str(STRANGER) in reply["html"] and "piloto privado" in reply["html"]
    assert db.execute("select count(*) n from public.query_runs")[0]["n"] == before


def test_duplicate_update_is_answered_once(bot) -> None:
    first = bot.say("/help", update_id=5000)
    again = bot.say("/help", update_id=5000)
    assert len(first) == 1 and again == []


def test_person_query_lists_authored_projects(db: Db, bot) -> None:
    name = db.execute("select pe.canonical_name from public.persons pe join public.project_participants pp "
                      "on pp.person_id = pe.id group by pe.canonical_name order by count(*) desc limit 1")[0]["canonical_name"]
    [reply] = bot.say(f"proyectos de {name}")
    assert "Proyectos como autor" in reply["html"] and "PL " in reply["html"]


def test_document_search_cites_sources(bot) -> None:
    [reply] = bot.say("¿qué propuestas hay sobre seguridad hídrica?")
    assert "Proyectos relacionados" in reply["html"] and "Pasajes en gacetas" in reply["html"]
    assert "p. 3" in reply["html"] and bot.vectors.calls[-1] is None


def test_unsupported_question_is_declined(bot) -> None:
    [reply] = bot.say("¿El PL 396/2026 Cámara se va a aprobar?")
    assert "no hago predicciones" in reply["html"]


# -- interfaz conversacional (centrorequirement.md) -------------------------------------------

def test_start_opens_home_with_only_available_sections(bot) -> None:
    """UI-T01/UI-F05: el menú solo ofrece lo que responde con datos; lo pendiente se explica por texto."""
    [home] = bot.say("/start")
    labels = keyboard_texts(home["keyboard"])
    assert labels == ["📚 Proyectos", "🏛 Senado hoy", "🗓 Agenda", "💬 Debates", "🔗 Fuentes", "❓ Ayuda"]
    [pending] = bot.say("/votaciones")
    assert "Todavía no tengo el explorador general de votaciones" in pending["html"]


def test_button_edits_the_same_message_and_back_returns(bot) -> None:
    [home] = bot.say("inicio")
    bot.click(home, "🔗 Fuentes")
    edit = bot.transport.edits[-1]
    assert edit["id"] == home["id"] and "Fuentes consultadas" in edit["html"]
    assert bot.transport.answers[-1] == {"id": bot.transport.answers[-1]["id"], "text": None, "alert": False}
    bot.click({"id": home["id"], "chat": ALLOWED, "keyboard": edit["keyboard"]}, "⬅️ Volver")
    assert "Tu explorador legislativo" in bot.transport.edits[-1]["html"]


def test_callback_payload_is_short_and_opaque(bot) -> None:
    """UI-T37: token opaco ≤ 64 bytes; no lleva filtros, textos ni permisos."""
    [home] = bot.say("inicio")
    for row in home["keyboard"]:
        for button in row:
            data = button["callback_data"]
            assert data.startswith("v1:") and len(data.encode()) <= 64 and "{" not in data


def test_double_click_has_one_effect(bot) -> None:
    """UI-T38: el mismo callback entregado dos veces produce una sola edición."""
    [home] = bot.say("inicio")
    before = len(bot.transport.edits)
    bot.click(home, "❓ Ayuda", callback_id="doble")
    bot.click(home, "❓ Ayuda", callback_id="doble")
    assert len(bot.transport.edits) - before == 1


def test_foreign_and_forged_callbacks_are_rejected(bot) -> None:
    """UI-T36: un menú ajeno no se ejecuta; un token falso o vencido reabre el inicio sin exponer datos."""
    [home] = bot.say("inicio")
    edits = len(bot.transport.edits)
    bot.click(home, "🔗 Fuentes", user=OTHER_ALLOWED)
    assert bot.transport.answers[-1]["alert"] and "otra persona" in bot.transport.answers[-1]["text"]
    assert len(bot.transport.edits) == edits
    forged = {"id": home["id"], "chat": ALLOWED, "keyboard": [[{"text": "x", "callback_data": "v1:falsificado"}]]}
    bot.click(forged, "x")
    assert "venció" in bot.transport.answers[-1]["text"]
    assert "Tu explorador legislativo" in (bot.transport.edits[-1]["html"] + bot.transport.sent[-1]["html"])


def test_repeated_start_keeps_navigation_bounded(bot) -> None:
    """UI-T06: repetir /start no acumula marcos de retorno."""
    for _ in range(5):
        bot.say("/start")
    ctx = bot.run(bot.runtime.state.load(ALLOWED, ALLOWED))
    assert ctx.navigation_stack == [] and ctx.current.intent.value == "home.open"


def test_stale_render_does_not_overwrite_newer_view(bot) -> None:
    """UI-T41/UI-F28: si otra interacción guarda la sesión mientras se arma una vista, la vista
    vieja no edita el mensaje ni pisa el contexto."""
    runtime = bot.runtime
    [home] = bot.say("inicio")
    original = runtime.app.handle

    def concurrent_handle(action, ctx, result_set=None):
        view = original(action, ctx, result_set)
        other = asyncio.run_coroutine_threadsafe(runtime.state.load(ALLOWED, ALLOWED), bot.loop).result()
        assert asyncio.run_coroutine_threadsafe(runtime.state.save(ALLOWED, ALLOWED, other), bot.loop).result()
        return view

    runtime.app.handle = concurrent_handle
    try:
        edits = len(bot.transport.edits)
        bot.click(home, "❓ Ayuda")
        assert len(bot.transport.edits) == edits  # la vista vieja se descarta
    finally:
        runtime.app.handle = original


def test_session_compare_and_swap(bot) -> None:
    state = bot.runtime.state
    first = bot.run(state.load(ALLOWED, ALLOWED))
    second = bot.run(state.load(ALLOWED, ALLOWED))
    assert bot.run(state.save(ALLOWED, ALLOWED, first)) is True
    assert bot.run(state.save(ALLOWED, ALLOWED, second)) is False


# -- I2: proyectos (CU-01/02/03) ---------------------------------------------------------------

def _texts(message) -> list[str]:
    return keyboard_texts(message["keyboard"])


def _last_view(bot) -> dict:
    return bot.transport.edits[-1]


def test_projects_list_orders_by_legislative_activity_and_opens_card(bot) -> None:
    [lst] = bot.say("proyectos")
    assert "Actividad reciente" in lst["html"] and "Orden: última actividad legislativa" in lst["html"]
    first = next(t for t in _texts(lst) if t.startswith("1 · "))
    bot.click(lst, first)
    card = _last_view(bot)
    assert card["id"] == lst["id"] and "Estado" in card["html"]
    assert {"👥 Autores", "🗳 Votaciones", "📄 Documentos", "⬅️ Resultados"} <= set(keyboard_texts(card["keyboard"]))


def test_contextual_question_uses_open_project(bot) -> None:
    """UI-F11: «quiénes lo presentaron» tras abrir una ficha responde por ese expediente."""
    [lst] = bot.say("proyectos")
    bot.click(lst, next(t for t in _texts(lst) if t.startswith("1 · ")))
    card = _last_view(bot)
    label = card["html"].split("\n", 1)[0]
    [authors] = bot.say("quiénes lo presentaron")
    assert "👥 Autores" in authors["html"] and label.replace("<b>", "").replace("</b>", "")[:12] in authors["html"]


def test_ordinal_opens_item_of_the_page_shown(bot) -> None:
    """UI-T13: «el segundo» abre el ID del segundo resultado mostrado."""
    [lst] = bot.say("proyectos")
    second_label = next(t for t in _texts(lst) if t.startswith("2 · "))[4:].rstrip("…")
    [card] = bot.say("el segundo")
    assert second_label[:15] in card["html"] or "Estado" in card["html"]
    [again] = bot.say("el octavo")
    assert "elige un número entre 1 y" in again["html"]


def test_ordinal_without_list_asks_instead_of_guessing(bot) -> None:
    """UI-T14."""
    bot.run(bot.runtime.state.clear(OTHER_ALLOWED, OTHER_ALLOWED))
    [reply] = bot.say("el segundo", user=OTHER_ALLOWED)
    assert "No tengo una lista vigente" in reply["html"]


def test_filters_apply_immediately_and_pagination_keeps_snapshot(bot) -> None:
    [lst] = bot.say("proyectos")
    bot.click(lst, "🎛 Filtros")
    filters = _last_view(bot)
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": filters["keyboard"]}, "Todo")
    all_view = _last_view(bot)
    assert "Todo" in all_view["html"] and "Página 1 de" in all_view["html"]
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": all_view["keyboard"]}, "Siguiente ➡️")
    page2 = _last_view(bot)
    assert "Página 2 de" in page2["html"] and "1 · " in keyboard_texts(page2["keyboard"])[0]
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": page2["keyboard"]}, "⬅️ Anterior")
    assert _last_view(bot)["html"] == all_view["html"]


def test_search_by_title_words_and_fallback_to_meaning(db: Db, bot) -> None:
    title = db.execute("select value_json ->> 'short_name' t from public.observations "
                       "where predicate = 'project_profile' and value_json ->> 'short_name' ~ '^[A-ZÁÉÍÓÚÑ ]{6,}$' limit 1")[0]["t"]
    word = max(title.split(), key=len).lower()
    [found] = bot.say(f"proyectos {word}")
    assert "Búsqueda «" in found["html"] and "1 · " in " ".join(_texts(found))
    [fallback] = bot.say("proyectos zzzinexistente")
    assert "Proyectos relacionados" in fallback["html"] or "No encontré" in fallback["html"]


def test_project_documents_and_votings_views(bot) -> None:
    [lst] = bot.say("proyectos")
    bot.click(lst, next(t for t in _texts(lst) if t.startswith("1 · ")))
    card = _last_view(bot)
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": card["keyboard"]}, "📄 Documentos")
    docs = _last_view(bot)
    assert "📄 Documentos" in docs["html"] and "Gaceta 1382 de 2026" in docs["html"]
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": docs["keyboard"]}, "📌 Ficha")
    bot.click({"id": lst["id"], "chat": ALLOWED, "keyboard": _last_view(bot)["keyboard"]}, "🗳 Votaciones")
    assert "🗳 Votaciones" in _last_view(bot)["html"]


# -- I3: jornada, agenda, cronología y debates (CU-04/05/06/07) ----------------------------------

def test_day_overview_separates_confirmed_scheduled_and_published(bot) -> None:
    """UI-T19: hechos, programación y publicaciones en secciones distintas; cobertura explícita."""
    [day] = bot.say("cámara el 23 de septiembre de 2026")
    html = day["html"]
    assert "Cámara · miércoles 23 de septiembre de 2026" in html
    assert html.index("Confirmado") < html.index("Programado") < html.index("Publicado")
    assert "Radicado: PL 396/2026 Cámara" in html
    assert "La agenda de la Cámara no está en nuestras fuentes; no significa que no haya sesiones" in html


def test_day_navigation_uses_explicit_dates_and_future_has_no_facts(bot) -> None:
    """UI-T20/UI-F15: los botones llevan fecha explícita; una fecha futura no tiene hechos."""
    [day] = bot.say("senado el 27 de septiembre de 2026")
    bot.click(day, "Día siguiente ➡️")
    assert "domingo 27" not in bot.transport.edits[-1]["html"] and "lunes 28 de septiembre" in bot.transport.edits[-1]["html"]
    [future] = bot.say("senado el 30 de octubre de 2026")
    assert "No hay hechos para fechas futuras" in future["html"]


def test_timeline_lists_dated_facts_without_inventing_order(bot) -> None:
    """UI-F13/UI-T18."""
    [card] = bot.say("PL 396/2026 Cámara")
    bot.click(card, "🗓 Trámite")
    timeline = bot.transport.edits[-1]["html"]
    assert "🗓 Trámite" in timeline and "Radicado en la Cámara" in timeline
    assert "no tienen orden horario" in timeline


def test_agenda_has_week_navigation_and_explains_absence(bot) -> None:
    [agenda] = bot.say("agenda")
    assert "No encontré agenda publicada" in agenda["html"] and "hora" not in agenda["html"].split("\n")[0]
    assert {"⬅️ Anterior", "Siguiente ➡️"} <= set(keyboard_texts(agenda["keyboard"]))


def test_debates_have_limited_coverage_and_search_only_actas(bot) -> None:
    """UI-T23/T24/T25: sin actas estructuradas se explica el límite; la búsqueda filtra actas."""
    [intro] = bot.say("debates")
    assert "Cobertura limitada" in intro["html"] and "conversación pública" in intro["html"]
    [found] = bot.say("salud")  # respuesta a la captura abierta por «Debates»
    assert "Búsqueda en actas" in found["html"] and "no es la transcripción completa" in found["html"]
    assert {"key": "segment_kind", "match": {"value": "acta"}} in bot.vectors.calls[-1]["must"]
