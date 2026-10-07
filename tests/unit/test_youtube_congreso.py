"""Videos oficiales del Congreso (SRC-26): títulos, temas y feed, con los feeds reales del 2026-10-06."""

from datetime import date
from pathlib import Path

import pytest

from botcentro.connectors.video_titles import clean_description, parse_title
from botcentro.connectors.youtube_congreso import CHANNELS, parse_feed

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "youtube"


@pytest.mark.parametrize("title,channel,expected", [
    ("PLENARIA DE LA CÁMARA DE REPRESENTANTES  - 1 DE OCTUBRE DE 2026", "congreso",
     ("sesion", "camara", "plenaria", date(2026, 10, 1), None)),
    ("COMISIÓN SÉPTIMA SENADO DE LA REPÚBLICA - 1 de octubre de 2026", "congreso",
     ("sesion", "senado", "comision_7", date(2026, 10, 1), None)),
    ("Plenaria Cámara de Representantes: Moción de Censura MinDefensa  - 22/09/2026", "camara",
     ("sesion", "camara", "plenaria", date(2026, 9, 22), "Moción de Censura MinDefensa")),
    ("COMISIONES ECONOMICAS CONJUNTAS: Presupuesto Gral. 2027 - 23/09/2026", "camara",
     ("sesion", "congreso", "comisiones_economicas", date(2026, 9, 23), "Presupuesto Gral. 2027")),
    ("AUDIENCIA PÚBLICA - COMISIÓN PRIMERA DE SENADO - 05 DE OCTUBRE DE 2026", "congreso",
     ("audiencia", "senado", "comision_1", date(2026, 10, 5), None)),
    ("Historias que tienen sabor a Colombia I Cartagena", "congreso", ("programa", None, None, None, None)),
    ("CANAL CONGRESO COLOMBIA SEÑAL EN VIVO", "congreso", ("senal", None, None, None, None)),
    ("PLENARIA  DEL SENADO DE LA REPÚBLICA - 30 DE SEPTIEMBRE DE 2026", "congreso",
     ("sesion", "senado", "plenaria", date(2026, 9, 30), None)),
])

def test_titles_identify_session(title: str, channel: str, expected: tuple) -> None:
    i = parse_title(title, channel)
    assert (i.kind, i.corporation, i.body_key, i.session_date, i.topic) == expected


def test_program_about_a_plenary_is_not_the_plenary() -> None:
    """«Antes de la plenaria» menciona cuerpo y fecha, pero no es la sesión: no se enlaza a las votaciones."""
    assert parse_title("ANTES DE LA PLENARIA - Septiembre  30 de 2026", "congreso").kind == "programa"


def test_program_with_date_is_not_a_session() -> None:
    """Una emisión fechada sin cuerpo legislativo no se enlaza a sesiones (evita enlaces falsos)."""
    assert parse_title("#NCR Emisión Jueves 1 de octubre del 2026", "camara").kind == "programa"


def test_description_drops_social_boilerplate() -> None:
    assert clean_description("¡Síguenos en nuestras redes sociales!\nX: https://x.com/ccongresocol_") is None
    assert clean_description("Debate sobre el proyecto\n¡Síguenos!") == "Debate sobre el proyecto"


def test_real_feeds_yield_sessions_with_dates_and_useful_topics() -> None:
    by_kind: dict[str, int] = {}
    topics = {}
    for (cid, name, kind), fixture in zip(CHANNELS, ("canal-congreso.xml", "camara.xml")):
        result = parse_feed((FIX / fixture).read_bytes(), cid, name, kind)
        assert not result.issues and len(result.candidates) == 15
        for c in result.candidates:
            v = c.value
            by_kind[v["kind"]] = by_kind.get(v["kind"], 0) + 1
            if v["kind"] in ("sesion", "audiencia"):
                assert v["session_date"] and v["url"].startswith("https://www.youtube.com/watch?v=")
                topics[(v["session_date"], v["body_key"])] = v["topic"]
    assert by_kind["sesion"] == 8 and by_kind["audiencia"] == 3
    assert topics[("2026-10-01", "comision_7")] is None  # la descripción solo repetía el título
    assert topics[("2026-10-05", "comision_1")].startswith("sobre acto legislativo")


def test_feed_with_dtd_is_rejected_without_parsing() -> None:
    result = parse_feed(b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed>&x;</feed>',
                        "c", "n", "congreso")
    assert result.issues[0].code == "XML_DTD" and not result.candidates
