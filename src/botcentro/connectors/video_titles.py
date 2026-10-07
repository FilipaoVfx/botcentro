"""Lectura de títulos y descripciones de los videos oficiales del Congreso (SRC-26).

Los canales oficiales nombran cada sesión con el cuerpo y la fecha en el título:
«PLENARIA DE LA CÁMARA DE REPRESENTANTES - 1 DE OCTUBRE DE 2026», «Plenaria Cámara de
Representantes: Moción de Censura MinDefensa - 22/09/2026», «AUDIENCIA PÚBLICA - COMISIÓN PRIMERA
DE SENADO - 05 DE OCTUBRE DE 2026». De ahí salen corporación, cuerpo, tipo, fecha de la sesión y
tema. La fecha de la sesión viene del título: la de publicación en YouTube es otra (suele ser el día
siguiente) y nunca la sustituye (FE-SEM-06). Si el título no permite saber qué sesión es, el video
queda como «programa» y no se enlaza a sesiones.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
          "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "sept": 9,
          "oct": 10, "nov": 11, "dic": 12}
ORDINALS = {"primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6, "septima": 7}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE_WORDS = re.compile(rf"\b(\d{{1,2}})\s+(?:de\s+)?({_MONTH})\.?\s+(?:de(?:l)?\s+)?(\d{{4}})\b")
_DATE_MONTH_FIRST = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b")
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_BOILERPLATE = re.compile(r"siguenos|instagram|facebook|tiktok|x\.com|twitter|youtube\.com|suscribete|redes sociales|"
                          r"https?://|www\.|^#|^@")


def fold(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).lower().split())


@dataclass(frozen=True)
class SessionInfo:
    kind: str                 # sesion | audiencia | programa | senal
    corporation: str | None   # senado | camara | congreso (comisiones conjuntas)
    body: str | None          # texto legible: «Plenaria», «Comisión Primera», «Comisiones económicas conjuntas»
    body_key: str | None      # plenaria | comision_1..7 | comisiones_economicas | audiencia
    session_date: date | None
    topic: str | None


def session_date(title: str) -> date | None:
    folded = fold(title)
    for pattern, order in ((_DATE_WORDS, "dmy"), (_DATE_MONTH_FIRST, "mdy"), (_DATE_NUMERIC, "dmy_num")):
        m = pattern.search(folded)
        if not m:
            continue
        try:
            if order == "dmy":
                return date(int(m[3]), MONTHS[m[2]], int(m[1]))
            if order == "mdy":
                return date(int(m[3]), MONTHS[m[1]], int(m[2]))
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def parse_title(title: str, channel: str | None = None) -> SessionInfo:
    folded = fold(title)
    if "senal en vivo" in folded or "en vivo 24" in folded:
        return SessionInfo("senal", None, None, None, None, None)
    corporation = ("congreso" if "conjuntas" in folded else
                   "senado" if re.search(r"\bsenado\b|\bsenate\b", folded) else
                   "camara" if re.search(r"\bcamara\b|\brepresentantes\b", folded) else
                   "camara" if channel == "camara" and "plenaria" in folded else None)
    body = body_key = None
    if "conjuntas" in folded and "economic" in folded:
        body, body_key = "Comisiones económicas conjuntas", "comisiones_economicas"
    elif m := re.search(r"comision(?:es)?\s+(primera|segunda|tercera|cuarta|quinta|sexta|septima)", folded):
        n = ORDINALS[m[1]]
        body, body_key = f"Comisión {m[1].capitalize().replace('Septima', 'Séptima')}", f"comision_{n}"
    elif "plenaria" in folded:
        body, body_key = "Plenaria", "plenaria"
    audiencia = "audiencia publica" in folded
    when = session_date(title)
    # Las sesiones se titulan empezando por el cuerpo («PLENARIA DEL SENADO…», «Comisión Séptima…»,
    # «AUDIENCIA PÚBLICA…»). Un programa que solo lo menciona («ANTES DE LA PLENARIA - 30 de
    # septiembre») no es la sesión, aunque tenga cuerpo y fecha.
    starts_with_body = re.match(r"(sesion\s+)?(plenaria|comision|comisiones|audiencia publica)\b", folded) is not None
    if not starts_with_body:
        kind = "programa"
    elif audiencia:
        kind = "audiencia"
    elif body_key and when:
        kind = "sesion"
    else:
        kind = "programa"
    topic = None
    if ":" in title:  # «Plenaria Cámara de Representantes: Moción de Censura MinDefensa - 22/09/2026»
        after = title.split(":", 1)[1]
        after = re.split(r"\s+-\s+\d", after)[0].strip(" -")
        topic = after or None
    return SessionInfo(kind, corporation, body, body_key, when, topic)


def clean_description(text: str | None, limit: int = 600) -> str | None:
    """Descripción útil sin la publicidad de redes sociales que repiten los canales."""
    kept = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or _BOILERPLATE.search(fold(stripped)):
            continue
        kept.append(stripped)
    value = " ".join(" ".join(kept).split())
    return value[:limit] or None
