"""Vistas de investigaciones y resumen de seguimientos, con RPC simulado (investigaciones §13–§14)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from botcentro.domain.dates import BOGOTA
from botcentro.telegram_ui.app import UiApplication
from botcentro.telegram_ui.contracts import Intent, SessionContext, UiAction
from botcentro.telegram_ui.digest import DigestPolicy, DigestWorker, delivery_error, render_digest
from botcentro.telegram_ui.intents import parse_text
from botcentro.telegram_ui.investigations import InvestigationViews

CASE = {"case_id": "c1", "title": "Caso sintético", "summary": "Resumen neutral.", "revision_no": 2,
        "reviewed_at": "2026-09-01T00:00:00Z", "coverage_note": "Piloto", "territories": [], "featured": False,
        "branches": [{"proceeding_id": "p1", "authority": "Autoridad A", "radicado": "A-1", "status": "archivado",
                      "finality": "en_firme", "freshness": "ultimo_estado_conocido", "last_verified_at": "2026-06-01"},
                     {"proceeding_id": "p2", "authority": "Autoridad B", "radicado": "B-2", "status": "investigacion",
                      "finality": "recurrida", "freshness": "verificado", "last_verified_at": "2026-09-20"}],
        "evidence": [{"url": "https://cortesuprema.gov.co/x.pdf", "pages": [3, 3], "excerpt": "texto de soporte",
                      "captured_at": "2026-09-01"}]}


class FakeRpc:
    def __init__(self, flags: dict[str, bool] | None = None, **responses) -> None:  # noqa: ANN003
        self.flags = {"FEATURE_CASES": True, "FEATURE_SUBSCRIPTIONS": True} if flags is None else flags
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, params: dict):  # noqa: ANN201
        self.calls.append((name, params))
        if name == "bot_feature_flags":
            return self.flags
        value = self.responses.get(name)
        return value(params) if callable(value) else value


def make_app(rpc: FakeRpc) -> UiApplication:
    return UiApplication(SimpleNamespace(rpc=rpc), investigations=InvestigationViews(rpc, bot_id=1))  # type: ignore[arg-type]


def act(intent: Intent, **params) -> UiAction:  # noqa: ANN003
    return UiAction(intent=intent, entry_point="button", parameters=params)


def labels(view) -> list[str]:  # noqa: ANN001
    return [b.label for row in view.rows for b in row]


def test_flags_off_hide_menu_and_explain() -> None:
    rpc = FakeRpc(flags={})
    app = make_app(rpc)
    home = app.handle(act(Intent.HOME), SessionContext())
    assert not any("casos" in label.lower() or "seguimientos" in label.lower() for label in labels(home))
    view = app.handle(parse_text("grandes casos"), SessionContext())
    assert view.status == "unavailable" and "revisión editorial" in view.blocks[0]
    assert not any(name == "public_cases_list" for name, _ in rpc.calls)


def test_flags_on_show_menu() -> None:
    home = make_app(FakeRpc()).handle(act(Intent.HOME), SessionContext())
    assert {"📁 Grandes casos", "⚖️ Investigaciones", "🔔 Mis seguimientos"} <= set(labels(home))


def test_case_card_separates_branches_and_states_freshness() -> None:
    """F-04 / T-24 / T-25 / BOT-03."""
    view = make_app(FakeRpc(public_case=CASE)).handle(act(Intent.CASE_OPEN, case_id="c1"), SessionContext())
    text = view.blocks[0]
    assert "archivado" in text and "investigación" in text and "último estado conocido" in text
    assert "ninguno determina el estado del caso" in text and "no equivale a culpabilidad" in text
    assert {"🕒 Cronología", "📂 Expedientes", "👥 Actores y relaciones", "📑 Contratos", "🔔 Seguir"} <= set(labels(view))
    assert view.evidence and view.evidence[0]["url"].startswith("https://cortesuprema.gov.co/")


def test_unpublished_case_is_not_shown() -> None:
    view = make_app(FakeRpc(public_case=None)).handle(act(Intent.CASE_OPEN, case_id="zz"), SessionContext())
    assert view.status == "unavailable"


def test_homonym_municipalities_ask_for_department() -> None:
    """F-02 / T-36: «alcaldía San Pedro» no elige un municipio en silencio."""
    found = [{"territory_id": "t1", "name": "SAN PEDRO", "department": "SUCRE"},
             {"territory_id": "t2", "name": "SAN PEDRO", "department": "VALLE DEL CAUCA"}]
    rpc = FakeRpc(public_territory_search=found)
    view = make_app(rpc).handle(parse_text("investigaciones alcaldía San Pedro"), SessionContext())
    assert view.status == "partial" and {"San Pedro · Sucre", "San Pedro · Valle Del Cauca"} <= set(labels(view))


def test_territory_prompt_then_text_resolves() -> None:
    rpc = FakeRpc(public_territory_search=[], public_territory=None)
    app, ctx = make_app(rpc), SessionContext()
    app.handle(act(Intent.TERRITORIES), ctx)
    view = app.handle(parse_text("Florencia"), ctx)
    assert view.view_type == "territory_search" and ("public_territory_search", {"p_text": "Florencia"}) in rpc.calls


def test_actor_relation_by_affiliation_is_labelled() -> None:
    """F-03 / T-21."""
    actor = {"actor_id": "a1", "name": "Partido Sintético", "actor_type": "partido", "positions": [], "participations": [],
             "cases": [{"case_id": "c1", "title": "Caso", "relation": "por_afiliacion_de_Persona X"}]}
    view = make_app(FakeRpc(public_actor=actor)).handle(act(Intent.ACTOR_OPEN, actor_id="a1"), SessionContext())
    assert "por afiliación de Persona X (no es sujeto procesal)" in view.blocks[0]


def test_follow_requires_confirmation_and_uses_principal() -> None:
    """SUB-01 / BOT-09: tocar Seguir muestra alcance y frecuencia; solo la confirmación crea el seguimiento
    y con la identidad de quien actúa."""
    rpc = FakeRpc(public_case=CASE, bot_subscribe=[{"subscription_id": "s1", "created": True, "title": "Caso sintético"}])
    app, ctx = make_app(rpc), SessionContext()
    app.handle(act(Intent.CASE_OPEN, case_id="c1"), ctx, user_hash="u-real")
    prompt = app.handle(act(Intent.SUBSCRIBE, object_type="case", object_id="c1"), ctx, user_hash="u-real")
    assert "18:00" in prompt.blocks[0] and not any(n == "bot_subscribe" for n, _ in rpc.calls)
    done = app.handle(act(Intent.SUBSCRIBE_CONFIRM, object_type="case", object_id="c1", user_hash="u-falso"), ctx,
                      user_hash="u-real")
    sub = [p for n, p in rpc.calls if n == "bot_subscribe"]
    assert sub == [{"p_bot_id": 1, "p_user_hash": "u-real", "p_object_type": "case", "p_object_id": "c1",
                    "p_consent_version": "seguimientos-v1"}]
    assert "ahora sigues" in done.blocks[0]


def test_text_follow_uses_current_object() -> None:
    rpc = FakeRpc(public_case=CASE)
    app, ctx = make_app(rpc), SessionContext()
    app.handle(act(Intent.CASE_OPEN, case_id="c1"), ctx, user_hash="u")
    view = app.handle(parse_text("seguir"), ctx, user_hash="u")
    assert view.view_type == "subscribe_prompt"
    assert any(b.params == {"object_type": "case", "object_id": "c1"} for row in view.rows for b in row)


def test_unsubscribe_all_asks_first() -> None:
    rpc = FakeRpc(bot_unsubscribe_all=3)
    app = make_app(rpc)
    first = app.handle(act(Intent.UNSUBSCRIBE_ALL), SessionContext(), user_hash="u")
    assert first.view_type == "confirm" and not any(n == "bot_unsubscribe_all" for n, _ in rpc.calls)
    done = app.handle(act(Intent.UNSUBSCRIBE_ALL, confirm=True), SessionContext(), user_hash="u")
    assert "3 seguimiento" in done.blocks[0]


def test_sensitive_question_is_not_answered_with_a_label() -> None:
    """F-08: «¿X es corrupto?» no produce calificación."""
    from botcentro.query.intent import plan_query
    plan = plan_query("¿Juan Pérez es corrupto?", datetime(2026, 9, 29, tzinfo=BOGOTA))
    assert "no califico" in (plan.reason or "").lower()


# -- resumen diario ----------------------------------------------------------------------------

def at(hour: int, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, 5, tzinfo=BOGOTA)


def test_digest_policy_quiet_hours_and_daily() -> None:
    p = DigestPolicy()
    assert p.quiet(at(22)) and p.quiet(at(7)) and not p.quiet(at(8))
    assert not p.digest_due(at(17), None) and p.digest_due(at(18), None)
    assert not p.digest_due(at(19), date(2026, 9, 29)) and not p.digest_due(at(21), None)


def test_delivery_error_classification() -> None:
    class TelegramForbiddenError(Exception):
        pass
    assert delivery_error(TelegramForbiddenError("bot was blocked by the user")) == ("failed", "BOT_BLOCKED")
    assert delivery_error(asyncio.TimeoutError()) == ("unknown_delivery", "TIMEOUT")


def test_render_digest_puts_corrections_first() -> None:
    text = render_digest([{"event_type": "claim_published", "summary": "Nueva actuación"},
                          {"event_type": "correction", "summary": "Identidad corregida"}])
    assert text.index("Corrección") < text.index("Novedades") and "/seguimientos" in text


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):  # noqa: ANN201
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value


class Sender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.error = error

    async def send(self, chat_id: int, html: str, keyboard) -> int:  # noqa: ANN001
        if self.error:
            raise self.error
        self.sent.append((chat_id, html))
        return 77


def _batch(params: dict) -> list[dict]:
    items = [{"delivery_id": "d2", "event_type": "correction", "summary": "Corrección X"}]
    if params["p_include_digest"]:
        items.append({"delivery_id": "d1", "event_type": "claim_published", "summary": "Novedad"})
    return [{"telegram_identity_id": "i", "chat_id": 10, "items": items}]


@pytest.mark.parametrize("hour,expect_digest", [(10, False), (18, True)])
def test_worker_sends_corrections_anytime_and_digest_at_six(hour: int, expect_digest: bool) -> None:
    rpc, sender, store = FakeRpc(digest_batch=_batch, digest_prepare=1), Sender(), MemoryStore()
    worker = DigestWorker(rpc, sender, store, prefix="bc:t:1", now=lambda: at(hour))  # type: ignore[arg-type]
    assert asyncio.run(worker.tick())["sent"] == 1
    assert ("Novedad" in sender.sent[0][1]) is expect_digest
    assert any(n == "digest_prepare" for n, _ in rpc.calls) is expect_digest
    marks = [p["p_state"] for n, p in rpc.calls if n == "digest_mark"]
    assert marks == ["sending", "sent"]
    if expect_digest:  # un solo resumen por día
        asyncio.run(worker.tick())
        assert [p["p_include_digest"] for n, p in rpc.calls if n == "digest_batch"] == [True, False]


def test_worker_is_silent_at_night_and_marks_ambiguous_delivery() -> None:
    rpc = FakeRpc(digest_batch=_batch)
    asyncio.run(DigestWorker(rpc, Sender(), MemoryStore(), prefix="p", now=lambda: at(23)).tick())  # type: ignore[arg-type]
    assert rpc.calls == []
    rpc = FakeRpc(digest_batch=_batch)
    stats = asyncio.run(DigestWorker(rpc, Sender(asyncio.TimeoutError()), MemoryStore(), prefix="p",  # type: ignore[arg-type]
                                     now=lambda: at(10)).tick())
    assert stats["unknown"] == 1
    assert [p["p_state"] for n, p in rpc.calls if n == "digest_mark"] == ["sending", "unknown_delivery"]


def test_contract_list_names_companies_but_not_people() -> None:
    """Decisión 2026-09-30: personas naturales solo en la ficha del contrato, no en listas."""
    items = [{"id": "c1", "native_id": "CO1.1", "value_current": "100", "status_original": "Aprobado", "signed_on": "2026-09-01",
              "entity": "MUNICIPIO", "contractor": "PERSONA SINTETICA", "contractor_type": "persona", "object": "x"},
             {"id": "c2", "native_id": "CO1.2", "value_current": "200", "status_original": "Aprobado", "signed_on": "2026-09-02",
              "entity": "MUNICIPIO", "contractor": "EMPRESA SINTETICA SAS", "contractor_type": "organizacion_privada", "object": "y"},
             {"id": "c3", "native_id": "CO1.3", "value_current": "300", "status_original": "Aprobado", "signed_on": "2026-09-03",
              "entity": "MUNICIPIO", "contractor": "SIN DOCUMENTO", "contractor_type": None, "object": "z"}]
    rpc = FakeRpc(public_contracts={"known_total": 3, "items": items},
                  public_contract={"native_id": "CO1.1", "entity": {"name": "MUNICIPIO"},
                                   "contractor": {"name": "PERSONA SINTETICA", "identified": True, "type": "persona"}})
    views = InvestigationViews(rpc, bot_id=1)
    text = views.contracts({}, "de prueba").blocks[0]
    assert "EMPRESA SINTETICA SAS" in text and "PERSONA SINTETICA" not in text and "SIN DOCUMENTO" not in text
    assert text.count("persona natural") == 1 and text.count("nombre en la ficha") == 1
    assert "PERSONA SINTETICA" in views.contract("c1").blocks[0]
