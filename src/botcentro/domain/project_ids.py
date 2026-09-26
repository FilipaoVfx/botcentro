"""Referencias a proyectos de ley y de acto legislativo (SRS-F07; T-05, T-06).

El número aislado no identifica un proyecto: la clave es corporación + tipo de iniciativa +
año de radicación + número (dentro de un ámbito de numeración). Este módulo extrae
referencias de texto libre o de fichas oficiales sin completar campos que no aparecen: una
referencia incompleta se resuelve contra candidatos y, si hay varios, se pide aclaración.

«Ley 1712 de 2014» o «Acto Legislativo 02 de 2015» son normas, no proyectos; solo se
reconocen las formas «proyecto de …», «PL» y «PAL».
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from botcentro.domain.enums import Corporation, InitiativeType

MIN_FILING_YEAR = 1991  # Constitución de 1991
MAX_FILING_YEAR = 2100
DEFAULT_NUMBERING_SCOPE = "corporation_filing_year"


class IncompleteReference(ValueError):
    """Se pidió la clave completa de una referencia a la que le faltan campos."""


@dataclass(frozen=True, slots=True)
class ProjectKey:
    """Clave contextual única (coincide con la restricción de project_identifiers)."""

    corporation: Corporation
    initiative_type: InitiativeType
    filing_year: int
    number: str
    numbering_scope: str = DEFAULT_NUMBERING_SCOPE


@dataclass(frozen=True, slots=True)
class ProjectRef:
    number: str
    number_raw: str
    initiative_type: InitiativeType | None = None
    filing_year: int | None = None
    corporation: Corporation | None = None

    @property
    def is_complete(self) -> bool:
        return None not in (self.initiative_type, self.filing_year, self.corporation)

    @property
    def missing_fields(self) -> tuple[str, ...]:
        names = ("initiative_type", "filing_year", "corporation")
        return tuple(name for name in names if getattr(self, name) is None)

    def key(self) -> ProjectKey:
        if not self.is_complete:
            raise IncompleteReference(f"faltan campos: {', '.join(self.missing_fields)}")
        assert self.corporation and self.initiative_type and self.filing_year
        return ProjectKey(self.corporation, self.initiative_type, self.filing_year, self.number)

    def matches(self, key: ProjectKey) -> bool:
        """Compatibilidad parcial: cada campo presente debe coincidir con la clave."""
        return (
            self.number == key.number
            and (self.initiative_type is None or self.initiative_type == key.initiative_type)
            and (self.filing_year is None or self.filing_year == key.filing_year)
            and (self.corporation is None or self.corporation == key.corporation)
        )


@dataclass(frozen=True, slots=True)
class ParsedCitation:
    """Una mención en el texto. Con numeración doble Senado–Cámara, `explicit_link` indica que
    la propia fuente asocia ambos números al mismo expediente (evidencia de vínculo, T-06)."""

    refs: tuple[ProjectRef, ...]
    explicit_link: bool
    span: tuple[int, int]


def normalize_number(raw: str) -> str:
    digits = raw.strip()
    if not digits.isdigit():
        raise ValueError(f"número de proyecto inválido: {raw!r}")
    return str(int(digits))


def normalize_year(raw: str) -> int | None:
    """Años de 4 dígitos o abreviados (24 → 2024, 98 → 1998). Fuera de rango → None."""
    value = int(raw)
    if len(raw) == 2:
        value += 2000 if value < 70 else 1900
    if MIN_FILING_YEAR <= value <= MAX_FILING_YEAR:
        return value
    return None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


_TYPE = (
    r"(?P<type>"
    r"proyectos?\s+de\s+acto\s+legislativo"
    r"|proyectos?\s+de\s+ley(?:\s+(?:estatutaria|organica))?"
    r"|p\.?\s?a\.?\s?l\.?"
    r"|p\.?\s?l\.?"
    r"|proyectos?"
    r")"
)
_MARKER = r"(?:(?:numero|num|nro|no|n)\s*\.?\s*[°]?\s*|#\s*)?"
_GROUP = (
    r"(?P<num>\d{1,5})"
    r"(?:\s*(?:de|del)\s+(?P<year_a>\d{4}|\d{2})\b"
    r"|\s*/\s*(?P<year_b>\d{4}|\d{2})"
    r"|-(?P<year_c>\d{4}|\d{2}))?"
    r"(?:\s*(?P<corp>senado|camara|s|c)\b)?"
)
_FIRST_RE = re.compile(rf"\b{_TYPE}\s*{_MARKER}{_GROUP}")
_NEXT_RE = re.compile(rf"\s*(?P<sep>[-–—]|,|\by\b)\s*{_MARKER}{_GROUP}")
_LINK_SEPARATORS = {"-", "–", "—"}


def _initiative_type(token: str) -> InitiativeType | None:
    compact = re.sub(r"[\s.]", "", token)
    if "actolegislativo" in compact or compact == "pal":
        return InitiativeType.PROYECTO_ACTO_LEGISLATIVO
    if "deley" in compact or compact == "pl":
        return InitiativeType.PROYECTO_LEY
    return None  # «proyecto 249»: tipo no declarado


def _corporation(token: str | None) -> Corporation | None:
    if token is None:
        return None
    return Corporation.SENADO if token in {"senado", "s"} else Corporation.CAMARA


def _ref_from_match(m: re.Match[str], initiative_type: InitiativeType | None) -> ProjectRef:
    year_raw = m["year_a"] or m["year_b"] or m["year_c"]
    return ProjectRef(
        number=normalize_number(m["num"]),
        number_raw=m["num"],
        initiative_type=initiative_type,
        filing_year=normalize_year(year_raw) if year_raw else None,
        corporation=_corporation(m["corp"]),
    )


def parse_project_citations(text: str) -> list[ParsedCitation]:
    """Extrae menciones de proyectos, incluida la numeración doble Senado–Cámara.

    >>> [c.refs[0].number for c in parse_project_citations("¿Qué pasa con el PL 249?")]
    ['249']
    """
    folded = _fold(text)
    citations: list[ParsedCitation] = []
    pos = 0
    while m := _FIRST_RE.search(folded, pos):
        initiative_type = _initiative_type(m["type"])
        refs = [_ref_from_match(m, initiative_type)]
        separators: list[str] = []
        end = m.end()
        while n := _NEXT_RE.match(folded, end):
            refs.append(_ref_from_match(n, initiative_type))
            separators.append(n["sep"])
            end = n.end()

        linked = (
            len(refs) == 2
            and separators[0] in _LINK_SEPARATORS
            and refs[0].corporation is not None
            and refs[1].corporation is not None
            and refs[0].corporation != refs[1].corporation
        )
        citations.append(ParsedCitation(tuple(refs), linked, (m.start(), end)))
        pos = end
    return citations


def parse_project_refs(text: str) -> list[ProjectRef]:
    """Todas las referencias mencionadas, en orden de aparición."""
    return [ref for citation in parse_project_citations(text) for ref in citation.refs]


def format_ref(ref: ProjectRef) -> str:
    """Forma de presentación: «PL 249/2024 Senado», «PAL 5/2024», «proyecto 249»."""
    prefix = {
        InitiativeType.PROYECTO_LEY: "PL",
        InitiativeType.PROYECTO_ACTO_LEGISLATIVO: "PAL",
        None: "proyecto",
    }[ref.initiative_type]
    text = f"{prefix} {ref.number}"
    if ref.filing_year is not None:
        text += f"/{ref.filing_year}"
    if ref.corporation is not None:
        text += " Senado" if ref.corporation is Corporation.SENADO else " Cámara"
    return text
