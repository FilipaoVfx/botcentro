import pytest

from botcentro.domain.enums import Corporation, InitiativeType
from botcentro.domain.project_ids import (
    IncompleteReference,
    ProjectKey,
    format_ref,
    parse_project_citations,
    parse_project_refs,
)

PL = InitiativeType.PROYECTO_LEY
PAL = InitiativeType.PROYECTO_ACTO_LEGISLATIVO


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PL 249 de 2024 Senado", ("249", PL, 2024, Corporation.SENADO)),
        ("P.L. 012 de 2023 Cámara", ("12", PL, 2023, Corporation.CAMARA)),
        ("PL 249/24C", ("249", PL, 2024, Corporation.CAMARA)),
        ("el proyecto 249-2024S", ("249", None, 2024, Corporation.SENADO)),
        ("Proyecto de Acto Legislativo número 18 de 2025 Cámara", ("18", PAL, 2025, Corporation.CAMARA)),
        ("PAL 05 de 2024 Senado", ("5", PAL, 2024, Corporation.SENADO)),
        ("proyecto de ley estatutaria No. 7 de 2025", ("7", PL, 2025, None)),
        ("¿Qué está pasando con el PL 249?", ("249", PL, None, None)),
    ],
)
def test_parse_single_reference(text, expected) -> None:
    (ref,) = parse_project_refs(text)
    assert (ref.number, ref.initiative_type, ref.filing_year, ref.corporation) == expected


@pytest.mark.parametrize("text", ["la Ley 1712 de 2014", "Acto Legislativo 02 de 2015", "la plenaria 5 votó", "PLENARIA 5"])
def test_norms_and_unrelated_numbers_are_not_projects(text: str) -> None:
    assert parse_project_refs(text) == []


def test_dual_numbering_is_explicit_link_evidence() -> None:
    (citation,) = parse_project_citations("Proyecto de Ley No. 249 de 2024 Senado – 312 de 2024 Cámara")
    assert citation.explicit_link
    assert [format_ref(r) for r in citation.refs] == ["PL 249/2024 Senado", "PL 312/2024 Cámara"]


def test_list_of_projects_is_not_a_link() -> None:
    (citation,) = parse_project_citations("PL 249 de 2024 y 312 de 2024")
    assert not citation.explicit_link and len(citation.refs) == 2


def test_isolated_number_never_builds_a_key() -> None:
    # T-05: el número aislado no es clave; una referencia parcial solo filtra candidatos.
    (ref,) = parse_project_refs("PL 249")
    assert not ref.is_complete and ref.missing_fields == ("filing_year", "corporation")
    with pytest.raises(IncompleteReference):
        ref.key()
    senado = ProjectKey(Corporation.SENADO, PL, 2024, "249")
    camara = ProjectKey(Corporation.CAMARA, PL, 2023, "249")
    other = ProjectKey(Corporation.SENADO, PL, 2024, "250")
    assert [k for k in (senado, camara, other) if ref.matches(k)] == [senado, camara]


def test_out_of_range_years_are_dropped_not_guessed() -> None:
    (ref,) = parse_project_refs("PL 5 de 1850 Senado")
    assert ref.filing_year is None
