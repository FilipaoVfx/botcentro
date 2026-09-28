"""Resolución de intención y extracción de entidades (SRS-F16, §9.1 pasos 2–4).

Primera etapa determinista: clasifica la consulta, extrae referencias a proyectos y el
periodo temporal (siempre convertido a fechas explícitas de Colombia) y rechaza lo que el
MVP no ofrece (PRD §6.5). Un modelo de lenguaje puede refinar la clasificación después, pero
no ampliar lo permitido.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from botcentro.domain.dates import DateParseError, DateRange, parse_spanish_date, resolve_relative_period
from botcentro.domain.enums import QueryIntent
from botcentro.domain.project_ids import ProjectRef, parse_project_refs

COMMANDS = {"/start", "/help", "/proyecto", "/agenda", "/fuentes", "/privacidad"}

_UNSUPPORTED = [
    (re.compile(r"\b(va(?:n)? a (?:aprobar|pasar|hundir)|se aprobara|probabilidad|pronostic|predic)"),
     "no hago predicciones sobre el resultado de proyectos"),
    (re.compile(r"\b(debo|me conviene|asesori|abogado|demanda(?:r)?|tutela)\b"),
     "no ofrezco asesoría jurídica personalizada"),
    (re.compile(r"\b(por quien votar|recomienda(?:s)? votar|mejor (?:congresista|candidato))\b"),
     "no hago recomendaciones de voto ni califico congresistas"),
    (re.compile(r"\b(que opina la gente|apoyo popular|sentimiento)\b"),
     "no mido opinión pública ni apoyo político"),
]
_COMPARISON = re.compile(r"\b(compar\w*|diferencias?|que cambio|cambios entre|versus|vs\.?)\b")
_AGENDA = re.compile(r"\b(agenda|orden del dia|se (?:discutira|debatira|votara)|sesion(?:es)?|que se discute|citad[oa]s?)\b")
_STRUCTURED = re.compile(
    r"\b(voto|votaron|votacion(?:es)?|votos|asistencia|asistio|cuant[oa]s|estado|en que va|que paso|"
    r"que esta pasando|tramite|ponentes?|autor(?:es|a)?|radicad[oa]|comision)\b"
)
_DOCUMENT = re.compile(r"\b(que dice|articulos?|texto|menciona|de que (?:se )?trata|sobre que|contenido|paragrafo)\b")

_RELATIVE_EXPRESSIONS = re.compile(
    r"\b(pasado manana|manana|hoy|ayer|anteayer|antier|esta semana|la (?:proxima )?semana(?: que viene| entrante)?|"
    r"(?:el |este |esta |proximo |el proximo )?(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
    r"(?: proximo| que viene)?)\b"
)
_EXPLICIT_DATE = re.compile(r"\b\d{1,2} de [a-z]+ de \d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b")


@dataclass(frozen=True)
class QueryPlan:
    intent: QueryIntent
    project_refs: tuple[ProjectRef, ...] = ()
    period: DateRange | None = None
    period_expression: str | None = None
    command: str | None = None
    reason: str | None = None

    @property
    def needs_project_resolution(self) -> bool:
        return bool(self.project_refs)


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[¿?¡!,;]", " ", stripped).split())


def _period(folded: str, now: datetime) -> tuple[DateRange | None, str | None]:
    if m := _EXPLICIT_DATE.search(folded):
        try:
            parsed = parse_spanish_date(m.group(0))
        except DateParseError:
            return None, None
        if parsed.value is not None:
            return DateRange(parsed.value, parsed.value), m.group(0)
    if m := _RELATIVE_EXPRESSIONS.search(folded):
        period = resolve_relative_period(m.group(0), now)
        if period is not None:
            return period, m.group(0)
    return None, None


def plan_query(text: str, now: datetime) -> QueryPlan:
    stripped = text.strip()
    command = None
    if stripped.startswith("/"):
        head, _, rest = stripped.partition(" ")
        candidate = head.split("@", 1)[0].lower()
        if candidate in COMMANDS:
            command, stripped = candidate, rest.strip()
    folded = fold(stripped)
    refs = tuple(parse_project_refs(stripped))
    if command == "/proyecto" and not refs:  # «/proyecto 396 de 2026 Cámara»: el comando ya dice el tipo
        refs = tuple(parse_project_refs(f"proyecto {stripped}"))
    period, expression = _period(folded, now)

    if command in {"/start", "/help", "/fuentes", "/privacidad"}:
        return QueryPlan(QueryIntent.STRUCTURED, command=command)
    if command == "/agenda":
        return QueryPlan(QueryIntent.AGENDA, refs, period, expression, command)

    for pattern, reason in _UNSUPPORTED:
        if pattern.search(folded):
            return QueryPlan(QueryIntent.UNSUPPORTED, refs, period, expression, command, reason)

    if command == "/proyecto" or (refs and _STRUCTURED.search(folded) and not _DOCUMENT.search(folded)):
        return QueryPlan(QueryIntent.STRUCTURED, refs, period, expression, command)
    if _COMPARISON.search(folded):
        return QueryPlan(QueryIntent.COMPARISON, refs, period, expression, command)
    if _AGENDA.search(folded) or (period is not None and not refs and not _DOCUMENT.search(folded)):
        return QueryPlan(QueryIntent.AGENDA, refs, period, expression, command)
    if _STRUCTURED.search(folded) and not _DOCUMENT.search(folded):
        return QueryPlan(QueryIntent.STRUCTURED, refs, period, expression, command)
    if refs:
        return QueryPlan(QueryIntent.HYBRID, refs, period, expression, command)
    return QueryPlan(QueryIntent.DOCUMENT, refs, period, expression, command)
