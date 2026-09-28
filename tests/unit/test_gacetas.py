"""SRC-03: listado de la Imprenta, segmentación multiexpediente (T-14) y detección de texto ilegible."""

from datetime import date

from botcentro.connectors.gacetas_imprenta import GacetaRef, parse_rows
from botcentro.documents.gaceta_segments import segment_at, segment_gaceta
from botcentro.documents.pdf_text import legible

ROWS = ('<tr data-ri="500" class="x"><td><label id="formResumen:dataTableResumen:500:j_idt13" class="l">856</label></td>'
        '<td><label id="formResumen:dataTableResumen:500:j_idt18" class="l">Cámara de Representantes</label></td>'
        '<td><label id="formResumen:dataTableResumen:500:j_idt20" class="l">21/07/2026</label></td></tr>')

SPANISH = ("El presente proyecto de ley tiene por objeto establecer los lineamientos de la política pública "
           "para la seguridad hídrica y el riego en el desarrollo agropecuario, con el fin de que las "
           "comunidades rurales cuenten con acceso al agua y se dictan otras disposiciones para su "
           "cumplimiento por parte de las entidades del orden nacional y territorial en el país. ")


def test_rows_and_permalink_uses_latin1_entity() -> None:
    [ref] = parse_rows(ROWS)
    assert ref == GacetaRef("camara", date(2026, 7, 21), "856")
    assert ref.permalink.endswith("index2.xhtml?ent=C%E1mara&fec=21-7-2026&num=856")
    assert ref.document_key == "gaceta:camara:2026:856"


def test_legible_rejects_font_garbage_and_accepts_spanish() -> None:
    assert legible(SPANISH * 2)
    assert not legible("-!-27= 1.2#)0= ==  -*-0&=+-0= %% $ 01#+2= *-=='= .9&#= " * 20)
    assert not legible("")


def test_segments_only_link_their_own_heading_and_skip_lowercase_index() -> None:
    text = ("CONTENIDO\nPonencia para primer debate al Proyecto de ley número 5 de 2026 Senado ..... 1\n"
            "INFORME DE PONENCIA PARA PRIMER DEBATE AL PROYECTO DE LEY NÚMERO 113 DE 2026 SENADO\n"
            + SPANISH * 3 +
            "\nTEXTO DEFINITIVO APROBADO EN PLENARIA DEL PROYECTO DE LEY NÚMERO 245 DE 2025 CÁMARA\n" + SPANISH * 3)
    segments = segment_gaceta(text)
    assert [s.kind for s in segments] == ["portada", "ponencia", "texto_aprobado"]
    assert segments[0].refs == ()
    assert [r.number for r in segments[1].refs] == ["113"] and [r.number for r in segments[2].refs] == ["245"]
    assert segment_at(segments, len(text) - 10).kind == "texto_aprobado"
