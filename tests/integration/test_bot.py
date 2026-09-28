"""Bot de Telegram de punta a punta contra SQL real (DEC-16; SRS §11; T-21, T-22).

Recepción por sondeo → aceptación seudonimizada → cola → motor sin IA → respuesta, segmentos y
entregas registradas. Telegram y el índice vectorial se sustituyen por dobles en memoria.
"""

from __future__ import annotations

from datetime import datetime
from itertools import count

import pytest

from botcentro.domain.dates import BOGOTA
from botcentro.query.engine import AnswerEngine
from botcentro.telegram.bot import BotService
from botcentro.telegram.client import SentMessage
from botcentro.telegram.security import pseudonymize_user
from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings
from tests.integration.support import Db
from tests.integration.test_normalize_camara_pl import loaded  # noqa: F401 — fixture: fichas de Cámara cargadas

pytestmark = pytest.mark.db
BOT_ID = 777
KEY = b"k" * 32
ALLOWED, STRANGER = 1001, 2002


class FakeApi:
    bot_id = BOT_ID

    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.sent: list[tuple[int, str, bool]] = []
        self._ids = count(1)
        self._update_ids = count(1)

    def say(self, user_id: int, text: str, *, update_id: int | None = None) -> int:
        uid = update_id or next(self._update_ids) + 100
        self.inbox.append({"update_id": uid, "message": {"message_id": uid, "date": 0, "text": text,
                                                         "chat": {"id": user_id, "type": "private"},
                                                         "from": {"id": user_id, "is_bot": False}}})
        return uid

    def get_updates(self, offset, *, timeout=25):
        batch, self.inbox = self.inbox, []
        return batch

    def send_message(self, chat_id, text, *, html=True):
        self.sent.append((chat_id, text, html))
        return SentMessage(next(self._ids))

    def answer_callback(self, *a, **k) -> None: ...
    def delete_webhook(self) -> None: ...
    def set_commands(self, commands) -> None: ...


class FakeVectors:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, vector, *, limit=8, flt=None, with_payload=True):
        self.calls.append(flt)
        return self.hits[:limit]


@pytest.fixture(scope="module")
def bot(db: Db, loaded):  # noqa: F811
    admin, query = db.create_user("admin"), db.create_user("query_service")
    db.rpc(admin).call("admin_authorize_telegram", {
        "p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, ALLOWED), "p_chat_id": ALLOWED,
        "p_label": "piloto", "p_reason": "prueba de integración"})
    rpc = db.rpc(query)
    ficha = db.execute("select subject_ref, value_json from public.observations where predicate = 'project_profile' "
                       "and status = 'published' and value_json ->> 'camara_ref' is not null limit 1")[0]
    vectors = FakeVectors([{"score": 0.9, "payload": {
        "doc_kind": "ficha", "document_key": f"camara-ficha:{ficha['subject_ref']}",
        "title": ficha["value_json"]["short_name"], "text": ficha["value_json"]["title"], "year": 2026,
        "source_url": ficha["value_json"]["link"], "project_ids": []}}, {"score": 0.88, "payload": {
        "doc_kind": "gaceta", "document_key": "gaceta:senado:2026:1382", "title": "Gaceta 1382 de 2026 (senado)",
        "text": "El presente proyecto de ley tiene por objeto la seguridad hídrica.", "pdf_page_start": 3,
        "source_url": "https://svrpubindc.imprenta.gov.co/senado/index2.xhtml?ent=Senado&fec=24-9-2026&num=1382",
        "project_ids": []}}])
    engine = AnswerEngine(rpc, vectors, lambda text: [0.0] * 384,
                          clock=lambda: datetime(2026, 9, 28, 10, tzinfo=BOGOTA))
    api = FakeApi()
    intake = TelegramWebhook(WebhookSettings(BOT_ID, "no-se-usa", KEY), rpc)
    return BotService(api, intake, rpc, engine, worker_id="test-bot"), api, vectors


def _ask(service: BotService, api: FakeApi, text: str, user: int = ALLOWED) -> list[str]:
    before = len(api.sent)
    api.say(user, text)
    service.poll_once(None)
    service.drain()
    return [t for _, t, _ in api.sent[before:]]


def test_project_card_is_answered_and_recorded(db: Db, bot) -> None:
    service, api, _ = bot
    [reply] = _ask(service, api, "PL 396/2026 Cámara")
    assert "PL 396/2026 Cámara" in reply and "Estado" in reply and "Autores" in reply
    assert "Ficha en la Cámara" in reply and 'href="https://www.camara.gov.co/' in reply
    run = db.execute("select q.status, q.intent, a.support_status, d.state, d.provider_message_id "
                     "from public.query_runs q join public.answer_records a on a.query_id = q.id "
                     "join public.delivery_attempts d on d.answer_id = a.id order by q.created_at desc limit 1")[0]
    assert run == {"status": "answered", "intent": "hybrid", "support_status": "supported", "state": "sent",
                   "provider_message_id": run["provider_message_id"]} and run["provider_message_id"]


def test_unknown_project_says_absence_is_not_zero(bot) -> None:
    service, api, _ = bot
    [reply] = _ask(service, api, "¿En qué va el PL 9999/2019 Senado?")
    assert "No encontré" in reply and "PL 9999/2019 Senado" in reply


def test_unauthorized_user_gets_their_id_and_no_query(db: Db, bot) -> None:
    service, api, _ = bot
    before = db.execute("select count(*) n from public.query_runs")[0]["n"]
    [reply] = _ask(service, api, "hola", user=STRANGER)
    assert str(STRANGER) in reply and "piloto privado" in reply
    assert db.execute("select count(*) n from public.query_runs")[0]["n"] == before


def test_duplicate_update_is_answered_once(bot) -> None:
    service, api, _ = bot
    api.say(ALLOWED, "/help", update_id=5000)
    api.say(ALLOWED, "/help", update_id=5000)
    before = len(api.sent)
    service.poll_once(None)
    service.drain()
    assert len(api.sent) - before == 1


def test_person_query_lists_authored_projects(db: Db, bot) -> None:
    service, api, _ = bot
    name = db.execute("select pe.canonical_name from public.persons pe join public.project_participants pp "
                      "on pp.person_id = pe.id group by pe.canonical_name order by count(*) desc limit 1")[0]["canonical_name"]
    [reply] = _ask(service, api, f"proyectos de {name}")
    assert "Proyectos como autor" in reply and "PL " in reply


def test_agenda_without_data_reports_latest_available(bot) -> None:
    service, api, _ = bot
    [reply] = _ask(service, api, "agenda de esta semana")
    assert "No hay agenda publicada" in reply


def test_document_search_cites_sources(bot) -> None:
    service, api, vectors = bot
    [reply] = _ask(service, api, "proyectos sobre seguridad hídrica")
    assert "Proyectos relacionados" in reply and "Pasajes en gacetas" in reply and "p. 3" in reply
    assert vectors.calls[-1] is None  # sin proyecto citado no se filtra


def test_unsupported_question_is_declined(bot) -> None:
    service, api, _ = bot
    [reply] = _ask(service, api, "¿El PL 396/2026 Cámara se va a aprobar?")
    assert "no hago predicciones" in reply
