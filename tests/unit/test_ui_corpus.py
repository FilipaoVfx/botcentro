"""Corpus de evaluación de la interfaz (centrorequirement.md §29.1; UX-O03).

240 entradas en cuatro grupos de 60: comandos/alias/botones, preguntas con filtros,
seguimientos y ambiguas/adversariales. Cada entrada está marcada «ajuste» o «evaluacion»; las
de evaluación no deben usarse para afinar reglas. Las ambiguas no pueden disparar navegación
por una palabra suelta (integridad), así que ese grupo exige 100 %.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from botcentro.telegram_ui.intents import parse_text

CORPUS = [json.loads(line) for line in
          (Path(__file__).resolve().parents[1] / "corpus" / "ui_corpus.jsonl").read_text().splitlines()]


def test_corpus_shape() -> None:
    assert len(CORPUS) == 240
    assert Counter(r["group"] for r in CORPUS) == {"comandos": 60, "filtros": 60, "seguimiento": 60, "adversarial": 60}
    assert {r["split"] for r in CORPUS} == {"ajuste", "evaluacion"}


@pytest.mark.parametrize("split", ["ajuste", "evaluacion"])
def test_intent_accuracy(split) -> None:
    rows = [r for r in CORPUS if r["split"] == split]
    misses = [(r["id"], r["text"][:40], r["intent"], parse_text(r["text"]).intent.value)
              for r in rows if parse_text(r["text"]).intent.value != r["intent"]]
    accuracy = 1 - len(misses) / len(rows)
    assert accuracy >= 0.95, f"exactitud {accuracy:.1%}; fallos: {misses}"


def test_adversarial_entries_never_trigger_wrong_navigation() -> None:
    wrong = [(r["id"], r["text"][:40], parse_text(r["text"]).intent.value)
             for r in CORPUS if r["group"] == "adversarial" and parse_text(r["text"]).intent.value != r["intent"]]
    assert not wrong
