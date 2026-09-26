"""Validación determinista de afirmaciones antes de responder (SRS-F19, F20, §9.1 pasos 7–9,
§9.3; T-19, T-20).

Comprueba que cada afirmación factual:
  * cite evidencia recuperada en esta consulta y dentro del ámbito del usuario;
  * en la sección oficial, se apoye en fuentes primarias (el enriquecimiento va aparte);
  * reproduzca solo números presentes en su evidencia;
  * no cite literalmente pasajes de extracción de baja calidad.
La sustentación semántica requiere evaluación adicional: una cita presente no garantiza que
la afirmación esté respaldada (SRS §9.3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from botcentro.domain.enums import Authority, QualityStatus, QueryStatus

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTE_RE = re.compile(r"[«\"“].{8,}?[»\"”]")


class AnswerSection(StrEnum):
    OFFICIAL = "official"
    SECONDARY = "secondary"
    PUBLIC_DISCOURSE = "public_discourse"


_ALLOWED_AUTHORITIES = {
    AnswerSection.OFFICIAL: {Authority.PRIMARY},
    AnswerSection.SECONDARY: {Authority.PRIMARY, Authority.SECONDARY},
    AnswerSection.PUBLIC_DISCOURSE: {Authority.PUBLIC_DISCOURSE},
}


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source_url: str
    authority: Authority
    in_scope: bool = True
    facts: Mapping[str, Any] = field(default_factory=dict)
    excerpt: str | None = None
    quality: QualityStatus = QualityStatus.ACCEPTED


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    evidence_ids: tuple[str, ...]
    section: AnswerSection = AnswerSection.OFFICIAL
    interpretation: bool = False  # interpretación generada: se presenta rotulada como tal


@dataclass(frozen=True)
class ClaimVerdict:
    claim: Claim
    problems: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class ValidationResult:
    verdicts: tuple[ClaimVerdict, ...]
    status: QueryStatus

    @property
    def accepted_claims(self) -> list[Claim]:
        return [v.claim for v in self.verdicts if v.accepted]

    @property
    def rejected(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if not v.accepted]


def _digits(token: str) -> str:
    return re.sub(r"\D", "", token)


def _evidence_numbers(items: Sequence[EvidenceItem]) -> set[str]:
    found: set[str] = set()
    for item in items:
        texts = [str(v) for v in item.facts.values()]
        if item.excerpt:
            texts.append(item.excerpt)
        for text in texts:
            found.update(_digits(n) for n in _NUMBER_RE.findall(text))
    return found


def validate_claims(claims: Sequence[Claim], catalog: Mapping[str, EvidenceItem]) -> ValidationResult:
    verdicts = []
    for claim in claims:
        problems: list[str] = []
        if not claim.evidence_ids:
            problems.append("sin evidencia")
        missing = [e for e in claim.evidence_ids if e not in catalog]
        if missing:
            problems.append(f"evidencia no recuperada en esta consulta: {', '.join(missing)}")
        items = [catalog[e] for e in claim.evidence_ids if e in catalog]
        if any(not item.in_scope for item in items):
            problems.append("evidencia fuera del ámbito del usuario")
        allowed = _ALLOWED_AUTHORITIES[claim.section]
        if items and any(item.authority not in allowed for item in items):
            problems.append(f"autoridad no admitida en la sección {claim.section.value}")
        if not claim.interpretation and items:
            available = _evidence_numbers(items)
            unsupported = [n for n in _NUMBER_RE.findall(claim.text) if _digits(n) not in available]
            if unsupported:
                problems.append(f"números sin respaldo en la evidencia: {', '.join(unsupported)}")
        if _QUOTE_RE.search(claim.text) and any(item.quality is not QualityStatus.ACCEPTED for item in items):
            problems.append("cita literal de un pasaje con extracción de baja calidad")
        verdicts.append(ClaimVerdict(claim, tuple(problems)))

    factual_ok = [v for v in verdicts if v.accepted and not v.claim.interpretation]
    status = QueryStatus.ANSWERED if factual_ok else QueryStatus.INSUFFICIENT_EVIDENCE
    return ValidationResult(tuple(verdicts), status)
