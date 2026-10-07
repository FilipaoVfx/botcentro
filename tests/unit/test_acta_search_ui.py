"""Búsqueda en actas: intenciones de texto y presentación de fragmentos."""

import pytest

from botcentro.telegram_ui.app import _snippet
from botcentro.telegram_ui.contracts import Intent
from botcentro.telegram_ui.intents import parse_text


@pytest.mark.parametrize("text,query", [
    ("actas reforma pensional", "reforma pensional"),
    ('buscar en actas "Paloma Valencia"', '"Paloma Valencia"'),
    ("Buscar en las actas: jurisdicción especial para la paz", "jurisdicción especial para la paz"),
    ("en las actas PL 178 de 2025", "PL 178 de 2025"),
])
def test_text_routes_to_acta_search_keeping_original_query(text: str, query: str) -> None:
    action = parse_text(text)
    assert action.intent is Intent.ACTA_SEARCH and action.parameters["query"] == query


def test_bare_word_opens_the_prompt_and_project_search_is_untouched() -> None:
    assert parse_text("actas").intent is Intent.ACTA_SEARCH_PROMPT
    assert parse_text("buscar salud mental").intent is Intent.PROJECTS_SEARCH


def test_snippet_is_clean_escaped_and_highlighted() -> None:
    raw = ("ilustración.\nV otando NO, se niega.\x0cPágina 32 Jueves, 27 de agosto de 2026 G aceta del conGreso  1151\n"
           "⟦VOTACIÓN⟧ ⟦NOMINAL⟧ <script> inter -\npretado")
    out = _snippet(raw)
    assert "Página 32" not in out and "\x0c" not in out and "&lt;script&gt;" in out
    assert "<b>VOTACIÓN</b> <b>NOMINAL</b>" in out and "interpretado" in out
