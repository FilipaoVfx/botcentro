import pytest

from botcentro.domain.names import normalize_name
from botcentro.domain.votes import (
    AttendanceStatus,
    VoteEvidenceStatus,
    VoteMapping,
    VoteValue,
    classify_vote_evidence,
    compare_nominal_with_totals,
    normalize_attendance,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SÍ", VoteValue.YES),
        (" No ", VoteValue.NO),
        ("Abstención", VoteValue.ABSTAIN),
        ("Impedimento", VoteValue.IMPEDED),
        ("Ausente", VoteValue.ABSENT),
        ("", VoteValue.NOT_RECORDED),
        (None, VoteValue.NOT_RECORDED),
        ("S", VoteValue.OTHER),  # abreviaturas: solo con mapeo validado de la fuente
        ("Voto en blanco", VoteValue.OTHER),
    ],
)
def test_default_mapping_is_conservative(raw, expected) -> None:
    assert VoteMapping().normalize(raw) == expected


def test_source_specific_overrides() -> None:
    mapping = VoteMapping({"S": VoteValue.YES, "N": VoteValue.NO})
    assert mapping.normalize("s") == VoteValue.YES and mapping.normalize("n") == VoteValue.NO


def test_missing_row_is_not_absence() -> None:
    # T-08: fila nominal, solo agregado, fila ausente y abstención son casos distintos.
    assert classify_vote_evidence(person_row_found=True, nominal_list_available=True, totals_available=True) \
        is VoteEvidenceStatus.NOMINAL
    assert classify_vote_evidence(person_row_found=False, nominal_list_available=True, totals_available=True) \
        is VoteEvidenceStatus.NOT_IN_NOMINAL_RECORD
    assert classify_vote_evidence(person_row_found=False, nominal_list_available=False, totals_available=True) \
        is VoteEvidenceStatus.AGGREGATE_ONLY
    assert classify_vote_evidence(person_row_found=False, nominal_list_available=False, totals_available=False) \
        is VoteEvidenceStatus.NO_RECORD


def test_totals_comparison_flags_discrepancies_without_filling_rows() -> None:
    check = compare_nominal_with_totals({VoteValue.YES: 50, VoteValue.NO: 10}, {VoteValue.YES: 52, VoteValue.NO: 10})
    assert not check.consistent and check.differences == {VoteValue.YES: (50, 52)}


def test_attendance() -> None:
    assert normalize_attendance("Con excusa") is AttendanceStatus.EXCUSED
    assert normalize_attendance("?") is AttendanceStatus.OTHER


def test_normalize_name() -> None:
    assert normalize_name("  José  María PÉREZ - Gómez. ") == "jose maria perez-gomez"
