"""Votos nominales, totales y asistencia (SRS-F09; PRD §9.2; T-08).

Reglas:
  * El mapeo por defecto solo reconoce etiquetas inequívocas; lo demás es ``other`` con el
    valor original conservado. Las abreviaturas («S», «N») dependen de la fuente y se
    declaran en el mapeo de su conector, nunca aquí.
  * Una fila ausente no es «ausente»: solo la fuente puede decir ``absent``.
  * Ausencia de registro, ausencia a sesión, abstención e impedimento son categorías distintas.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class VoteValue(StrEnum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"
    IMPEDED = "impeded"
    ABSENT = "absent"
    NOT_RECORDED = "not_recorded"
    OTHER = "other"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    EXCUSED = "excused"
    OTHER = "other"


def fold_label(raw: str) -> str:
    """Minúsculas, sin tildes ni puntuación final y con espacios normalizados."""
    decomposed = unicodedata.normalize("NFKD", raw)
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^\w\s/-]", " ", text)
    return " ".join(text.split())


DEFAULT_VOTE_LABELS: Mapping[str, VoteValue] = {
    "si": VoteValue.YES,
    "a favor": VoteValue.YES,
    "no": VoteValue.NO,
    "en contra": VoteValue.NO,
    "abstencion": VoteValue.ABSTAIN,
    "se abstiene": VoteValue.ABSTAIN,
    "abstenido": VoteValue.ABSTAIN,
    "impedimento": VoteValue.IMPEDED,
    "impedido": VoteValue.IMPEDED,
    "declarado impedido": VoteValue.IMPEDED,
    "ausente": VoteValue.ABSENT,
    "no asistio": VoteValue.ABSENT,
    "": VoteValue.NOT_RECORDED,
    "sin registro": VoteValue.NOT_RECORDED,
    "no registra": VoteValue.NOT_RECORDED,
    "no voto": VoteValue.NOT_RECORDED,
}

DEFAULT_ATTENDANCE_LABELS: Mapping[str, AttendanceStatus] = {
    "presente": AttendanceStatus.PRESENT,
    "asistio": AttendanceStatus.PRESENT,
    "ausente": AttendanceStatus.ABSENT,
    "no asistio": AttendanceStatus.ABSENT,
    "excusa": AttendanceStatus.EXCUSED,
    "excusado": AttendanceStatus.EXCUSED,
    "con excusa": AttendanceStatus.EXCUSED,
    "excusa valida": AttendanceStatus.EXCUSED,
}


@dataclass(frozen=True)
class VoteMapping:
    """Mapeo de etiquetas de una fuente. `overrides` se valida con muestras de la fuente."""

    overrides: Mapping[str, VoteValue] = field(default_factory=dict)

    def normalize(self, raw: str | None) -> VoteValue:
        label = fold_label(raw or "")
        folded_overrides = {fold_label(k): v for k, v in self.overrides.items()}
        if label in folded_overrides:
            return folded_overrides[label]
        return DEFAULT_VOTE_LABELS.get(label, VoteValue.OTHER)


def normalize_attendance(raw: str | None, overrides: Mapping[str, AttendanceStatus] | None = None) -> AttendanceStatus:
    label = fold_label(raw or "")
    if overrides:
        folded = {fold_label(k): v for k, v in overrides.items()}
        if label in folded:
            return folded[label]
    return DEFAULT_ATTENDANCE_LABELS.get(label, AttendanceStatus.OTHER)


class VoteEvidenceStatus(StrEnum):
    """Qué puede afirmarse sobre el voto de una persona en una votación concreta (T-08)."""

    NOMINAL = "nominal"  # hay fila nominal para la persona
    NOT_IN_NOMINAL_RECORD = "not_in_nominal_record"  # hay lista nominal, pero sin la persona
    AGGREGATE_ONLY = "aggregate_only"  # solo totales: no se puede atribuir voto individual
    NO_RECORD = "no_record"  # no hay registro de resultados disponible


def classify_vote_evidence(
    *, person_row_found: bool, nominal_list_available: bool, totals_available: bool
) -> VoteEvidenceStatus:
    if person_row_found:
        return VoteEvidenceStatus.NOMINAL
    if nominal_list_available:
        return VoteEvidenceStatus.NOT_IN_NOMINAL_RECORD
    if totals_available:
        return VoteEvidenceStatus.AGGREGATE_ONLY
    return VoteEvidenceStatus.NO_RECORD


VOTE_LABELS_ES: Mapping[VoteValue, str] = {
    VoteValue.YES: "votó sí",
    VoteValue.NO: "votó no",
    VoteValue.ABSTAIN: "se abstuvo",
    VoteValue.IMPEDED: "se declaró impedido",
    VoteValue.ABSENT: "figura como ausente en el registro de la votación",
    VoteValue.NOT_RECORDED: "aparece en la lista sin voto registrado",
    VoteValue.OTHER: "tiene un registro con una categoría no normalizada",
}

EVIDENCE_STATUS_ES: Mapping[VoteEvidenceStatus, str] = {
    VoteEvidenceStatus.NOT_IN_NOMINAL_RECORD: (
        "no aparece en el registro nominal disponible; esto no demuestra que estuviera ausente"
    ),
    VoteEvidenceStatus.AGGREGATE_ONLY: (
        "solo hay totales agregados de esta votación; no es posible atribuir votos individuales"
    ),
    VoteEvidenceStatus.NO_RECORD: "no se encontró registro de resultados de esta votación en las fuentes cubiertas",
}


@dataclass(frozen=True)
class TotalsCheck:
    consistent: bool
    differences: Mapping[VoteValue, tuple[int, int]]


def compare_nominal_with_totals(
    nominal: Mapping[VoteValue, int], reported: Mapping[VoteValue, int]
) -> TotalsCheck:
    """Compara conteo nominal con totales reportados del mismo acto y evidencia (SRS §6.4).

    Solo compara categorías reportadas. Una discrepancia genera control de calidad; nunca
    autoriza completar filas faltantes.
    """
    differences = {
        category: (nominal.get(category, 0), total)
        for category, total in reported.items()
        if nominal.get(category, 0) != total
    }
    return TotalsCheck(consistent=not differences, differences=differences)
