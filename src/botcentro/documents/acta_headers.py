"""Actas en las gacetas: qué sesión registra cada una (para enlazar acta ↔ sesión ↔ video).

Una acta se abre con un encabezado en mayúsculas que fija su sesión:
«ACTA NÚMERO 005 DE 2025 (marzo 5)» o «ACTA DE PLENARIA NÚMERO 23 DE 2026 (septiembre 30)», seguido del
cuerpo («SESIÓN PLENARIA», «COMISIÓN PRIMERA CONSTITUCIONAL PERMANENTE»). Las actas que una sesión
solo cita («Aprobación de actas: Acta número 23 del 30 de abril de 2024») no llevan paréntesis ni van en
mayúsculas: no se confunden con la sesión de la gaceta (medido sobre 453 gacetas el 2026-10-06).

Un encabezado sin fecha legible o sin cuerpo identificable se registra igual, con esos campos nulos: se
enlaza solo cuando corporación, cuerpo y fecha coinciden.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from botcentro.connectors.video_titles import MONTHS, ORDINALS

_MONTH = "|".join(sorted((m for m in MONTHS if len(m) > 3), key=len, reverse=True))
# Sobre texto plegado de igual longitud (minúsculas, sin tildes, mismas posiciones que el original).
_HEAD = re.compile(
    rf"acta\s+(?:de\s+(?:plenaria|comision)\s+|conjunta\s+)?(?:numero|no\.?|n[°o])\s*(\d{{1,4}})\s+de\s+(\d{{4}})\s*"
    rf"\(\s*({_MONTH})\s+(\d{{1,2}})(?:\s+de\s+(\d{{4}}))?\s*\)")


@dataclass(frozen=True)
class ActaHeader:
    number: str
    year: int
    session_date: date | None
    corporation: str | None
    body: str | None
    body_key: str | None
    pdf_page: int

    def as_dict(self) -> dict:
        return {"number": self.number, "year": self.year,
                "session_date": self.session_date.isoformat() if self.session_date else None,
                "corporation": self.corporation, "body": self.body, "body_key": self.body_key, "pdf_page": self.pdf_page}


def _fold_same_length(text: str) -> str:
    """Minúsculas sin tildes conservando posiciones (cada carácter se pliega a uno solo)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in base if not unicodedata.combining(c)) or ch
        out.append(stripped[0].lower() if len(stripped) >= 1 else ch)
    return "".join(out)


def _mostly_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) >= 0.8


def _body(window: str) -> tuple[str | None, str | None]:
    if m := re.search(r"comisiones\s+(\w+)\s+(?:constitucionales\s+)?(?:permanentes\s+)?conjuntas", window):
        names = {"economicas": "económicas", "primeras": "primeras", "segundas": "segundas", "terceras": "terceras",
                 "cuartas": "cuartas", "quintas": "quintas", "sextas": "sextas", "septimas": "séptimas"}
        return f"Comisiones {names.get(m[1], m[1])} conjuntas", "comisiones_conjuntas"
    if m := re.search(r"comision\s+(primera|segunda|tercera|cuarta|quinta|sexta|septima)", window):
        name = m[1].capitalize().replace("Septima", "Séptima")
        return f"Comisión {name}", f"comision_{ORDINALS[m[1]]}"
    if re.search(r"sesion\s+plenaria|\bplenaria\b", window):
        return "Plenaria", "plenaria"
    if m := re.search(r"comision\s+(legal|especial|accidental)\s+((?:de|para|del|sobre)\s+[a-z ,\s]{3,60}?)(?=\s+(?:del\s+|de\s+la\s+)?"
                      r"(?:honorable\s+)?(?:senado|camara)\b|\s+acta\b|$)", window):
        name = " ".join(f"{m[1]} {m[2]}".split())
        return f"Comisión {name}"[:90], "comision_otra"
    if re.search(r"\bcomision\b", window):
        return "Otra comisión", "comision_otra"
    return None, None


def parse_acta_headers(pages: Iterable[tuple[int, str]], corporation: str | None) -> list[ActaHeader]:
    """`pages`: (página del PDF, texto). `corporation`: la de la serie de la gaceta, si no la dice el acta."""
    found: dict[tuple[str, int, date | None], ActaHeader] = {}
    for pdf_page, text in pages:
        folded = _fold_same_length(text)
        for m in _HEAD.finditer(folded):
            # «ACTA … NÚMERO N DE AAAA» en mayúsculas (el mes entre paréntesis suele ir en minúsculas).
            if not _mostly_upper(text[m.start(): m.end(2)]):
                continue  # una acta citada en el cuerpo del texto, no el encabezado de esta acta
            year = int(m[2])
            try:
                when = date(int(m[5]) if m[5] else year, MONTHS[m[3]], int(m[4]))
            except ValueError:
                when = None
            # El cuerpo va a veces antes del encabezado («COMISIÓN SÉPTIMA … DEL SENADO / ACTA NÚMERO 03 DE 2026»)
            # y a veces después («… (septiembre 30) / SESIÓN PLENARIA»). Gana el cuerpo específico (plenaria o
            # comisión N) más cercano; «otra comisión» solo si ninguno de los dos lados es específico.
            before = folded[max(0, m.start() - 160): m.start()]
            after = folded[m.end(): m.end() + 300]
            # Manda el bloque de encabezado (líneas previas a «ACTA NÚMERO»): comisión numerada, plenaria o
            # comisión con nombre. El texto posterior solo se usa si el encabezado no la dice: puede nombrar el
            # salón donde se reunió («Recinto de Sesiones de la Comisión Quinta»), que no es el cuerpo.
            head_body = _body(before[-160:])
            named = head_body[1] and (head_body[1] != "comision_otra" or head_body[0] != "Otra comisión")
            body, body_key = head_body if named else _body(after)
            if body_key is None and head_body[1]:
                body, body_key = head_body
            # Corporación: solo la mención explícita junto al encabezado; si no hay, o hay ambas, la serie de la gaceta.
            # Solo las líneas inmediatamente anteriores (el membrete «SECRETARIO GENERAL DEL SENADO» va en
            # todas las páginas y no cuenta) y el comienzo del acta («…de la honorable Cámara de Representantes»).
            near = before[-75:] + " " + after[:300]
            senado = re.search(r"senado\s+de\s+la\s+republica|honorable\s+senado", near) is not None
            camara = re.search(r"camara\s+de\s+representantes", near) is not None
            corp = "senado" if senado and not camara else "camara" if camara and not senado else corporation
            if body_key == "comisiones_conjuntas":
                corp = "congreso"
            key = (m[1].lstrip("0") or "0", year, when)
            found.setdefault(key, ActaHeader(m[1].lstrip("0") or "0", year, when, corp, body, body_key, pdf_page))
    return list(found.values())


def heading_contexts(pages: Iterable[tuple[int, str]], radius: int = 400) -> list[list]:
    """Texto alrededor de cada encabezado de acta, [página, fragmento]: permite volver a interpretar con un
    analizador mejorado sin descargar de nuevo la gaceta."""
    out = []
    for pdf_page, text in pages:
        for m in _HEAD.finditer(_fold_same_length(text)):
            out.append([pdf_page, text[max(0, m.start() - radius): m.end() + radius]])
    return out
