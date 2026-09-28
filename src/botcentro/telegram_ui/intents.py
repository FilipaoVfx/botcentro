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
    "fuentes": Intent.SOURCES, "/fuentes": Intent.SOURCES, "de donde sale eso": Intent.EVIDENCE,
    "volver": Intent.BACK, "atras": Intent.BACK, "/volver": Intent.BACK,
    "cancelar": Intent.CANCEL, "/cancel": Intent.CANCEL, "/cancelar": Intent.CANCEL,
    "actualizar": Intent.REFRESH,
    "proyectos": Intent.PROJECTS_LIST, "/proyectos": Intent.PROJECTS_LIST,
    "senadohoy": Intent.DAY_OVERVIEW, "senado hoy": Intent.DAY_OVERVIEW, "/senadohoy": Intent.DAY_OVERVIEW,
    "camarahoy": Intent.DAY_OVERVIEW, "camara hoy": Intent.DAY_OVERVIEW, "/camarahoy": Intent.DAY_OVERVIEW,
    "discusiones": Intent.DISCUSSIONS, "/discusiones": Intent.DISCUSSIONS, "debates": Intent.DISCUSSIONS,
    "votaciones": Intent.VOTINGS, "/votaciones": Intent.VOTINGS,
    "documentos": Intent.DOCUMENTS, "autores": Intent.PROJECT_PARTICIPANTS,
}
_NAV_WORDS = sorted({k for k in _ALIASES if " " not in k and not k.startswith("/")})
_CONTEXTUAL = [
    (re.compile(r"^(?:y )?(?:quien(?:es)? lo (?:presento|presentaron|radico|radicaron)|(?:sus |los )?autores)$"),
     Intent.PROJECT_PARTICIPANTS),
    (re.compile(r"^(?:y )?(?:sus |las )?votaciones(?: del proyecto)?$"), Intent.VOTINGS),
    (re.compile(r"^(?:y )?(?:sus |los )?documentos$|^muestrame el texto$"), Intent.DOCUMENTS),
    (re.compile(r"^(?:volver|regresar) al proyecto$"), Intent.BACK),
]
_ORDINALS = {"primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3, "cuarto": 4, "quinto": 5,
             "sexto": 6, "septimo": 7, "octavo": 8}
_ORDINAL_RE = re.compile(r"^(?:abre |abrir |ver |el |la |ese |esa )*(?:el |la )?(?P<word>" +
                         "|".join(_ORDINALS) + r")$")
_OPEN_N_RE = re.compile(r"^(?:abre|abrir|ver|opcion|numero) (?:el |la |la opcion )?(?P<n>[1-8])$")
_SEARCH_RE = re.compile(r"^(?:proyectos?|buscar|busca) (?:de |sobre |del |de la )?(?P<q>.{2,60})$")
_DAY_RE = re.compile(r"^(?P<corp>senado|camara)(?: hoy| ayer| manana)?$")


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
    if m := _DAY_RE.match(folded):
        return UiAction(intent=Intent.DAY_OVERVIEW, entry_point="text",
                        parameters={"corporation": m["corp"], "when": folded.split(" ", 1)[1] if " " in folded else "hoy"},
                        parameter_origins={"corporation": "explicit"})
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
