"""Texto y comandos → `UiAction` (centrorequirement.md §5).

Reglas:
* Un alias de navegación solo se aplica si es el mensaje completo (o alias + parámetro breve):
  «no encuentro proyectos» no es un clic en «Proyectos» (UI-F02).
* Se toleran errores leves solo en palabras de navegación («proyctos»); nunca en números,
  años, apellidos o títulos (§5.2 regla 4).
* Un ordinal requiere palabra ordinal o «abre el N»: «el 249» no es la posición 249 (§5.4).
"""

from __future__ import annotations

import difflib
import re

from botcentro.query.intent import fold
from botcentro.telegram_ui.contracts import Intent, UiAction

_ALIASES: dict[str, Intent] = {
    "inicio": Intent.HOME, "menu": Intent.HOME, "/start": Intent.HOME, "/inicio": Intent.HOME,
    "/menu": Intent.HOME, "ayuda": Intent.HELP, "/help": Intent.HELP, "/ayuda": Intent.HELP,
    "fuentes": Intent.EVIDENCE, "/fuentes": Intent.SOURCES, "de donde sale eso": Intent.EVIDENCE,
    "fuentes de esta respuesta": Intent.EVIDENCE, "de donde sale": Intent.EVIDENCE,
    "volver": Intent.BACK, "atras": Intent.BACK, "/volver": Intent.BACK,
    "cancelar": Intent.CANCEL, "/cancel": Intent.CANCEL, "/cancelar": Intent.CANCEL,
    "actualizar": Intent.REFRESH,
    "proyectos": Intent.PROJECTS_LIST, "/proyectos": Intent.PROJECTS_LIST,
    "senadohoy": Intent.DAY_OVERVIEW, "senado hoy": Intent.DAY_OVERVIEW, "/senadohoy": Intent.DAY_OVERVIEW,
    "camarahoy": Intent.DAY_OVERVIEW, "camara hoy": Intent.DAY_OVERVIEW, "/camarahoy": Intent.DAY_OVERVIEW,
    "/agenda": Intent.AGENDA, "agenda": Intent.AGENDA, "discusiones": Intent.DISCUSSIONS, "/discusiones": Intent.DISCUSSIONS, "debates": Intent.DISCUSSIONS,
    "votaciones": Intent.VOTINGS, "/votaciones": Intent.VOTINGS,
    "documentos": Intent.DOCUMENTS, "autores": Intent.PROJECT_PARTICIPANTS,
    "investigaciones": Intent.PROCEEDINGS, "/investigaciones": Intent.PROCEEDINGS,
    "grandes casos": Intent.CASES, "casos": Intent.CASES, "/casos": Intent.CASES, "gran caso": Intent.CASES,
    "entidades y territorios": Intent.TERRITORIES, "territorios": Intent.TERRITORIES, "/territorios": Intent.TERRITORIES,
    "entidades": Intent.TERRITORIES, "mis seguimientos": Intent.SUBSCRIPTIONS, "seguimientos": Intent.SUBSCRIPTIONS,
    "/seguimientos": Intent.SUBSCRIPTIONS, "seguir": Intent.SUBSCRIBE, "dejar de seguir": Intent.UNSUBSCRIBE,
    "cobertura": Intent.COVERAGE, "ayuda y cobertura": Intent.COVERAGE,
}
_NAV_WORDS = sorted({k for k in _ALIASES if " " not in k and not k.startswith("/")})
_CONTEXTUAL = [
    (re.compile(r"^(?:y )?(?:quien(?:es)? lo (?:presento|presentaron|radico|radicaron)|(?:sus |los )?autores)$"),
     Intent.PROJECT_PARTICIPANTS),
    (re.compile(r"^(?:y )?(?:sus |las )?votaciones(?: del proyecto)?$"), Intent.VOTINGS),
    (re.compile(r"^(?:y )?(?:sus |los )?documentos$|^muestrame el texto$"), Intent.DOCUMENTS),
    (re.compile(r"^(?:volver|regresar) al proyecto$"), Intent.BACK),
]
_ORDINALS = {"primero": 1, "primer": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercer": 3,
             "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6,
             "septimo": 7, "septima": 7, "octavo": 8, "octava": 8}
_ORDINAL_RE = re.compile(r"^(?:abre |abrir |ver |el |la |ese |esa )*(?:el |la )?(?P<word>" +
                         "|".join(_ORDINALS) + r")$")
_OPEN_N_RE = re.compile(r"^(?:abre|abrir|ver|opcion|numero) (?:el |la |la opcion )?(?P<n>[1-8])$")
_SEARCH_RE = re.compile(r"^(?:proyectos?|buscar|busca) (?:de |sobre |del |de la )?(?P<q>.{2,60})$")
_TERRITORY_RE = re.compile(r"^(?:investigaciones |casos |contratos )?(?:de |en )?(?:la )?(?:alcaldia|municipio|gobernacion) "
                           r"(?:de |del )?(?P<name>[a-z][a-z .'-]{2,60})$")
_CASES_SEARCH_RE = re.compile(r"^(?:grandes casos|casos|investigaciones) (?:de |sobre |del |en )?(?P<q>[a-z][a-z .'-]{2,60})$")
_DAY_RE = re.compile(r"^(?P<corp>senado|camara)(?: (?:el |del |de )?(?P<when>.+))?$")
_AGENDA_RE = re.compile(r"^agenda(?: (?:de |del |para |el )?(?P<when>.+))?$")
_DISCUSSION_SEARCH_RE = re.compile(r"^(?:discusiones|debates) (?:de |sobre |del )?(?P<q>.{3,60})$")


def _is_date_expression(text: str) -> bool:
    """Fecha explícita o expresión relativa reconocible («hoy», «ayer», «el martes», «esta semana»)."""
    from datetime import datetime

    from botcentro.domain.dates import BOGOTA, parse_spanish_date, resolve_relative_period

    if resolve_relative_period(text, datetime.now(BOGOTA)) is not None:
        return True
    try:
        return parse_spanish_date(text).value is not None
    except Exception:  # noqa: BLE001 — cualquier texto no fechable
        return False


def _corporation(folded: str) -> str | None:
    return "camara" if folded.startswith(("camara", "/camara")) else "senado" if "senado" in folded else None


def parse_text(text: str) -> UiAction:
    raw = text.strip()
    folded = fold(raw).rstrip(".")
    command = folded.split("@", 1)[0] if folded.startswith("/") else folded

    if command in _ALIASES:
        intent = _ALIASES[command]
        params: dict = {}
        if intent is Intent.DAY_OVERVIEW:
            params = {"corporation": _corporation(command) or "senado"}
        return UiAction(intent=intent, entry_point="command" if command.startswith("/") else "text",
                        parameters=params, parameter_origins={k: "explicit" for k in params})
    if m := _TERRITORY_RE.match(folded):
        return UiAction(intent=Intent.TERRITORY_RESOLVE, entry_point="text", parameters={"name": m["name"].strip(), "text": raw},
                        parameter_origins={"name": "explicit"})
    if m := _CASES_SEARCH_RE.match(folded):
        return UiAction(intent=Intent.CASES, entry_point="text", parameters={"query": m["q"].strip(), "text": raw},
                        parameter_origins={"query": "explicit"})
    if (m := _DAY_RE.match(folded)) and _is_date_expression(m["when"] or "hoy"):
        return UiAction(intent=Intent.DAY_OVERVIEW, entry_point="text",
                        parameters={"corporation": m["corp"], "expression": m["when"] or "hoy"},
                        parameter_origins={"corporation": "explicit", "expression": "explicit" if m["when"] else "default"})
    if (m := _AGENDA_RE.match(folded)) and _is_date_expression(m["when"] or "esta semana"):
        return UiAction(intent=Intent.AGENDA, entry_point="text", parameters={"expression": m["when"] or "esta semana"})
    if m := _DISCUSSION_SEARCH_RE.match(folded):
        return UiAction(intent=Intent.DISCUSSIONS, entry_point="text", parameters={"query": m["q"], "text": raw})
    for pattern, intent in _CONTEXTUAL:
        if pattern.match(folded):
            return UiAction(intent=intent, entry_point="text", parameters={"contextual": True})
    if m := _ORDINAL_RE.match(folded):
        return UiAction(intent=Intent.ORDINAL, entry_point="text", parameters={"position": _ORDINALS[m["word"]]})
    if m := _OPEN_N_RE.match(folded):
        return UiAction(intent=Intent.ORDINAL, entry_point="text", parameters={"position": int(m["n"])})
    if (m := _SEARCH_RE.match(folded)) and not re.search(r"\d", m["q"]):
        return UiAction(intent=Intent.PROJECTS_SEARCH, entry_point="text", parameters={"query": m["q"], "text": raw},
                        parameter_origins={"query": "explicit"})
    if " " not in folded and (close := difflib.get_close_matches(folded, _NAV_WORDS, n=1, cutoff=0.8)):
        return parse_text(close[0])  # error leve en una palabra de navegación
    return UiAction(intent=Intent.QUESTION, entry_point="command" if raw.startswith("/") else "text",
                    parameters={"text": raw[:4096]})
