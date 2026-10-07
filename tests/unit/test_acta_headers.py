"""Actas en las gacetas: la sesión que registra cada una (encabezados reales de 2026)."""

from datetime import date

from botcentro.documents.acta_headers import parse_acta_headers


def one(text: str, corp: str = "senado") -> dict:
    heads = parse_acta_headers([(3, text)], corp)
    assert len(heads) == 1, heads
    return heads[0].as_dict()


def test_body_before_heading() -> None:
    """Gaceta 1275/2026: el cuerpo precede al encabezado."""
    h = one("COMISIÓN SÉPTIMA CONSTITUCIONAL \nPERMANENTE DEL HONORABLE SENADO \nDE LA REPÚBLICA\n"
            "ACTA NÚMERO 03 DE 2026\n(agosto 19)\nLegislatura 2026-2027\nTercera Sesión Ordinaria")
    assert (h["session_date"], h["corporation"], h["body_key"], h["number"], h["pdf_page"]) == \
        ("2026-08-19", "senado", "comision_7", "3", 3)


def test_plenary_after_heading() -> None:
    h = one("ACTA DE PLENARIA NÚMERO 23 DE 2026\n(septiembre 30)\nSESIÓN PLENARIA ORDINARIA\nSENADO DE LA REPÚBLICA")
    assert (h["session_date"], h["body_key"]) == ("2026-09-30", "plenaria")


def test_cited_actas_are_not_sessions() -> None:
    """Las actas que la sesión aprueba o menciona no son la sesión de la gaceta."""
    text = ("III. Aprobación de actas: Acta número 23 del 30 de abril de 2024. "
            "Acta número 14 de 2024 (noviembre 18) aprobada por unanimidad.")
    assert parse_acta_headers([(1, text)], "camara") == []


def test_joint_committees_belong_to_congress() -> None:
    h = one("ACTA NÚMERO 03 DE 2025 (agosto 20 de 2025) COMISIONES ECONÓMICAS CONJUNTAS")
    assert (h["corporation"], h["body"], h["session_date"]) == ("congreso", "Comisiones económicas conjuntas", "2025-08-20")


def test_several_actas_in_one_gazette() -> None:
    pages = [(1, "PLENARIA DEL SENADO\nACTA NÚMERO 49 DE 2025\n(mayo 21)\nSESIÓN PLENARIA"),
             (30, "PLENARIA DEL SENADO\nACTA NÚMERO 50 DE 2025\n(mayo 27)\nSESIÓN PLENARIA")]
    heads = parse_acta_headers(pages, "senado")
    assert sorted(h.session_date for h in heads) == [date(2025, 5, 21), date(2025, 5, 27)]


def test_specific_body_wins_over_generic_mention() -> None:
    """Gaceta 1305/2026: «comisión» genérica antes del encabezado no tapa «COMISIÓN QUINTA» después."""
    h = one("… informe presentado a la comisión por el ponente.\nACTA NÚMERO 01 DE 2026\n(septiembre 8)\n"
            "COMISIÓN QUINTA CONSTITUCIONAL PERMANENTE\nSENADO DE LA REPÚBLICA")
    assert (h["body_key"], h["corporation"]) == ("comision_5", "senado")


def test_series_decides_when_neighbouring_text_is_ambiguous() -> None:
    """Gaceta 1271/2026 (Cámara): una mención al Senado en el texto vecino no cambia la corporación."""
    heads = parse_acta_headers([(1, "proyecto aprobado por el Senado y enviado a la Cámara de Representantes.\n"
                                    "COMISIÓN TERCERA CONSTITUCIONAL PERMANENTE\nACTA NÚMERO 03 DE 2026\n(agosto 25)")],
                               "camara")
    assert heads[0].corporation == "camara" and heads[0].body_key == "comision_3"


def test_legal_committee_name_and_masthead() -> None:
    """Gacetas 1305 y 1271 de 2026: nombre de la comisión legal en dos líneas; el membrete no fija la corporación."""
    paz = one("COMISIÓN LEGAL DE PAZ Y \nPOSCONFLICTO DEL SENADO DE LA \nREPÚBLICA\nACTA NÚMERO 01 DE 2026\n(septiembre 8)\n"
              "se reunieron en el Recinto de Sesiones de la Comisión Quinta Constitucional")
    assert (paz["body"], paz["body_key"], paz["corporation"]) == ("Comisión legal de paz y posconflicto", "comision_otra", "senado")
    heads = parse_acta_headers([(1, "SECRETARIO  GENERAL  DEL  SENADO \nwww.secretariasenado.gov.co \nCOMISIÓN TERCERA "
                                    "CONSTITUCIONAL \nPERMANENTE\nACTA NÚMERO 003 DE 2026\n(agosto 25)\nSesión Ordinaria\n"
                                    "los integrantes de la Comisión Tercera de la honorable Cámara de Representantes")], "camara")
    assert heads[0].corporation == "camara"
