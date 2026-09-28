"""Segmentación de gacetas multiexpediente (SRS-F14, T-14).

Una gaceta encadena piezas (ponencias, textos, actas, conceptos). Cada pieza empieza con un
encabezado en mayúsculas; el índice de la portada usa minúsculas y no abre segmentos. Solo se
enlaza un segmento a los proyectos citados en su propio encabezado: nunca toda la gaceta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from botcentro.domain.project_ids import ProjectRef, parse_project_refs

_KINDS = (
    ("ponencia", r"INFORMES?\s+DE\s+PONENCIA|PONENCIA\s+(?:POSITIVA\s+|NEGATIVA\s+)?PARA\s+(?:PRIMER|SEGUNDO|TERCER|CUARTO)"),
    ("texto_aprobado", r"TEXTO\s+(?:DEFINITIVO|APROBADO|PROPUESTO|CONCILIADO)"),
    ("acta", r"ACTA\s+(?:DE\s+PLENARIA\s+)?(?:N[ÚU]MERO|No\.?|N°)"),
    ("concepto", r"CONCEPTO\s+(?:JUR[ÍI]DICO|INSTITUCIONAL|T[ÉE]CNICO)|COMENTARIOS\s+(?:DEL|DE\s+LA)\s+"),
    ("objeciones", r"OBJECIONES\s+(?:PRESIDENCIALES|DEL\s+GOBIERNO)"),
    ("texto_radicado", r"PROYECTO\s+DE\s+(?:LEY|ACTO\s+LEGISLATIVO)(?:\s+ESTATUTARIA|\s+ORG[ÁA]NICA)?\s+N[ÚU]MERO"),
    ("informe", r"INFORME\s+DE\s+(?:CONCILIACI[ÓO]N|COMISI[ÓO]N|SUBCOMISI[ÓO]N)"),
)
_HEADING_RE = re.compile(r"(?m)^[ \t]*(?:" + "|".join(f"(?P<{k}>{p})" for k, p in _KINDS) + ")")
_HEADER_WINDOW = 700


@dataclass(frozen=True)
class Segment:
    kind: str
    char_start: int
    char_end: int
    refs: tuple[ProjectRef, ...]


def _mostly_upper(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) >= 0.8


def segment_gaceta(text: str) -> list[Segment]:
    starts: list[tuple[int, str]] = []
    for m in _HEADING_RE.finditer(text):
        line_end = text.find("\n", m.start())
        line = text[m.start(): line_end if line_end >= 0 else len(text)]
        if _mostly_upper(line):
            starts.append((m.start(), m.lastgroup or "otro"))
    if not starts:
        return [Segment("otro", 0, len(text), ())]
    segments = []
    if starts[0][0] > 0:
        segments.append(Segment("portada", 0, starts[0][0], ()))
    for (start, kind), nxt in zip(starts, starts[1:] + [(len(text), "")]):
        header = text[start: min(start + _HEADER_WINDOW, nxt[0])]
        refs = tuple(r for r in parse_project_refs(header) if r.number and r.filing_year and r.corporation)
        segments.append(Segment(kind, start, nxt[0], refs))
    return segments


def segment_at(segments: list[Segment], offset: int) -> Segment:
    for segment in segments:
        if segment.char_start <= offset < segment.char_end:
            return segment
    return segments[-1]
