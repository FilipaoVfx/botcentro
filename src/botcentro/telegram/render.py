"""Composición de mensajes de Telegram en modo HTML (PRD §10; SRS-F23; T-22).

* Todo texto dinámico se escapa (&, <, >); solo se emiten etiquetas generadas aquí.
* Un mensaje largo se divide en bloques completos; si un bloque no cabe, se corta en
  límites de línea o palabra fuera de cualquier etiqueta, de modo que una referencia
  (<a>…</a>) nunca queda partida.
* El límite se mide en unidades UTF-16 sobre el HTML (cota superior del texto visible).
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_LIMIT = 4096
_TAG_RE = re.compile(r"<(/?)([a-z]+)[^>]*>")


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def bold(text: str) -> str:
    return f"<b>{escape(text)}</b>"


def link(label: str, url: str) -> str:
    if not url.startswith(("https://", "http://")):
        raise ValueError("solo se enlazan URLs http(s)")
    return f'<a href="{html.escape(url, quote=True)}">{escape(label)}</a>'


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class Section:
    """Sección de respuesta: título opcional y líneas ya renderizadas (HTML seguro)."""

    title: str | None
    lines: Sequence[str]

    def render(self) -> str:
        body = "\n".join(self.lines)
        return f"{bold(self.title)}\n{body}" if self.title else body


def render_sections(sections: Sequence[Section]) -> list[str]:
    """Bloques renderizados; se omiten secciones vacías (PRD §10: sin plantillas vacías)."""
    return [s.render() for s in sections if s.lines]


def _safe_cut_points(block: str) -> list[int]:
    """Posiciones (tras salto de línea o espacio) en las que no hay etiquetas abiertas."""
    depth = 0
    points: list[int] = []
    i = 0
    while i < len(block):
        if block[i] == "<":
            m = _TAG_RE.match(block, i)
            if m:
                depth += -1 if m[1] else 1
                i = m.end()
                continue
        if depth == 0 and block[i] in "\n ":
            points.append(i + 1)
        i += 1
    return points


def _split_block(block: str, limit: int) -> list[str]:
    parts: list[str] = []
    remaining = block
    while utf16_len(remaining) > limit:
        points = _safe_cut_points(remaining)
        fitting = [p for p in points if utf16_len(remaining[:p]) <= limit]
        newline_points = [p for p in fitting if remaining[p - 1] == "\n"]
        if newline_points:
            cut = newline_points[-1]
        elif fitting:
            cut = fitting[-1]
        else:
            raise ValueError("un elemento indivisible (p. ej. un enlace) supera el límite del mensaje")
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def split_message(blocks: Sequence[str], limit: int = DEFAULT_LIMIT) -> list[str]:
    """Empaqueta bloques (separados por línea en blanco) en mensajes que respetan el límite."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        for piece in (_split_block(block, limit) if utf16_len(block) > limit else [block]):
            candidate = f"{current}\n\n{piece}" if current else piece
            if utf16_len(candidate) <= limit:
                current = candidate
            else:
                messages.append(current)
                current = piece
    if current:
        messages.append(current)
    return messages
