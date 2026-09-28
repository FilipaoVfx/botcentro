"""Votaciones en la interfaz con datos reales de muestra del Senado (centrorequirement.md CU-09, CU-11)."""

from __future__ import annotations

import os

import pytest

from botcentro.telegram_ui.runtime import keyboard_texts
from tests.integration.bot_harness import ALLOWED, REDIS_URL, FakeVectors, build_harness
from tests.integration.support import Db
from tests.integration.test_normalize_senado_od import loaded  # noqa: F401 — fixture: SRC-01 normalizado

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def bot(db: Db, loaded):  # noqa: F811
    if not REDIS_URL:
        pytest.skip("BOTCENTRO_TEST_REDIS_URL no configurada")
    harness, redis = build_harness(db, FakeVectors([]), namespace=f"votes-{os.getpid()}")
    yield harness
    harness.run(redis.aclose())
    harness.loop.close()


def _open_first(bot, message) -> dict:
    bot.click(message, next(t for t in keyboard_texts(message["keyboard"]) if t.startswith("1 · ")))
    return bot.transport.edits[-1]


def test_votings_explorer_lists_acts_with_computed_totals(bot) -> None:
    [lst] = bot.say("/votaciones")
    bot.click(lst, "Todo")
    view = bot.transport.edits[-1]
    assert "Totales calculados a partir del registro nominal" in view["html"] and "Sí " in view["html"]
    detail = _open_first(bot, {"id": lst["id"], "chat": ALLOWED, "keyboard": view["keyboard"]})
    assert "Resultado calculado del registro nominal" in detail["html"]
    assert "falta de dato" in detail["html"]  # sin fila ≠ ausencia ni abstención (UI-T28)
    assert "Senado · datos abiertos" in detail["html"]


def test_person_votes_show_each_act_and_their_vote(db: Db, bot) -> None:
    name = db.execute("select pe.canonical_name n from public.persons pe join public.current_votes cv "
                      "on cv.person_id = pe.id group by pe.canonical_name order by count(*) desc limit 1")[0]["n"]
    [lst] = bot.say("/votaciones")
    bot.click(lst, "👤 Persona")
    [view] = bot.say(name)  # respuesta a la captura
    assert f"Votaciones de" in view["html"] and ("Votó Sí" in view["html"] or "Votó No" in view["html"])
    assert "✖️ Quitar persona" in keyboard_texts(view["keyboard"])


def test_sources_of_this_answer_differ_from_catalog(bot) -> None:
    """UI-T32: tras una respuesta, «fuentes» muestra su evidencia; sin respuesta, el catálogo."""
    [lst] = bot.say("/votaciones")
    bot.click(lst, "Todo")
    _open_first(bot, {"id": lst["id"], "chat": ALLOWED, "keyboard": bot.transport.edits[-1]["keyboard"]})
    [ev] = bot.say("fuentes")
    assert "Fuentes de esta respuesta" in ev["html"] and "app.senado.gov.co" in ev["html"]
    bot.run(bot.runtime.state.clear(ALLOWED, ALLOWED))
    [catalog] = bot.say("fuentes")
    assert "Fuentes consultadas" in catalog["html"] and "Fuentes de esta respuesta" not in catalog["html"]


def test_ordinal_on_votings_list_opens_that_act(bot) -> None:
    [lst] = bot.say("/votaciones")
    bot.click(lst, "Todo")
    [detail] = bot.say("el primero")
    assert "Resultado calculado del registro nominal" in detail["html"]
