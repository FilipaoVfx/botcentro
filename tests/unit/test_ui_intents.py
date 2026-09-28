"""Interpretación de entradas de la interfaz (centrorequirement.md §5; UI-T01–T03, UI-T13)."""

import pytest

from botcentro.telegram_ui.contracts import Intent
from botcentro.telegram_ui.intents import parse_text


@pytest.mark.parametrize(("text", "intent", "params"), [
    ("proyectos", Intent.PROJECTS_LIST, {}),
    ("/proyectos", Intent.PROJECTS_LIST, {}),
    ("Proyctos", Intent.PROJECTS_LIST, {}),                      # error leve en palabra de navegación
    ("senadohoy", Intent.DAY_OVERVIEW, {"corporation": "senado"}),
    ("Senado hoy", Intent.DAY_OVERVIEW, {"corporation": "senado"}),
    ("/camarahoy", Intent.DAY_OVERVIEW, {"corporation": "camara"}),
    ("discuiones", Intent.DISCUSSIONS, {}),
    ("/start", Intent.HOME, {}),
    ("menú", Intent.HOME, {}),
    ("cancelar", Intent.CANCEL, {}),
    ("el segundo", Intent.ORDINAL, {"position": 2}),
    ("abre el 3", Intent.ORDINAL, {"position": 3}),
    ("quiénes lo presentaron", Intent.PROJECT_PARTICIPANTS, {"contextual": True}),
])
def test_equivalent_entries(text, intent, params) -> None:
    action = parse_text(text)
    assert action.intent is intent
    assert {k: v for k, v in action.parameters.items() if k in params} == params


@pytest.mark.parametrize("text", [
    "no encuentro proyectos",       # frase de ayuda con palabra clave: no es el botón
    "el 249",                       # número de proyecto, no posición 249
    "PL 249",
    "¿cancelar la sesión afecta el proyecto?",  # contiene «cancelar» pero es una pregunta
    "Paloma Valencia",              # apellidos: nunca se corrigen por similitud
])
def test_keywords_inside_sentences_do_not_trigger_navigation(text) -> None:
    assert parse_text(text).intent is Intent.QUESTION


def test_search_keeps_original_text_and_never_corrects_numbers() -> None:
    action = parse_text("proyectos salud")
    assert action.intent is Intent.PROJECTS_SEARCH and action.parameters == {"query": "salud", "text": "proyectos salud"}
    assert parse_text("proyectos 2026").intent is Intent.QUESTION
