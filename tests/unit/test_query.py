"""Intención (SRS-F16), validación de afirmaciones (SRS §9.3; T-19, T-20) y política (T-01)."""

from datetime import date, datetime, timezone

import pytest

from botcentro.domain.enums import Authority, QualityStatus, QueryIntent, QueryStatus, SourceState
from botcentro.errors import PolicyViolation
from botcentro.query.intent import plan_query
from botcentro.query.validator import AnswerSection, Claim, EvidenceItem, validate_claims
from botcentro.sources.policy import (
    Operation,
    Permission,
    SourceActivationInput,
    UsageProfile,
    activation_diagnostics,
)

NOW = datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)  # 25/09 21:30 en Bogotá


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("¿Qué está pasando con el PL 249?", QueryIntent.STRUCTURED),
        ("¿Cómo votó la senadora X el proyecto 249 de 2024?", QueryIntent.STRUCTURED),
        ("¿Qué se discutirá mañana?", QueryIntent.AGENDA),
        ("Compara el texto radicado y el aprobado del PL 249", QueryIntent.COMPARISON),
        ("¿Qué dice el artículo 3 del PL 249 de 2024 Senado?", QueryIntent.HYBRID),
        ("proyectos sobre transporte público", QueryIntent.DOCUMENT),
        ("¿El PL 249 va a aprobarse? ¿Qué probabilidad tiene?", QueryIntent.UNSUPPORTED),
        ("¿Debo demandar esta ley?", QueryIntent.UNSUPPORTED),
    ],
)
def test_intent(text: str, intent: QueryIntent) -> None:
    assert plan_query(text, NOW).intent is intent


def test_agenda_tomorrow_resolves_to_explicit_bogota_date() -> None:
    plan = plan_query("¿Qué se discutirá mañana en plenaria?", NOW)
    assert plan.period and plan.period.start == date(2026, 9, 26) and plan.period_expression == "manana"


def test_commands() -> None:
    assert plan_query("/start", NOW).command == "/start"
    plan = plan_query("/proyecto PL 249 de 2024 Senado", NOW)
    assert plan.intent is QueryIntent.STRUCTURED and plan.project_refs[0].filing_year == 2024
    assert plan_query("/agenda@botcentro_bot el próximo martes", NOW).period.start == date(2026, 9, 29)


CATALOG = {
    "e1": EvidenceItem("e1", "https://leyes.senado.gov.co/p/249", Authority.PRIMARY,
                       facts={"number": "249", "filing_year": 2024, "status": "Radicado", "date": "2024-08-01"}),
    "e2": EvidenceItem("e2", "https://congresovisible.uniandes.edu.co/p/1", Authority.SECONDARY,
                       facts={"topic": "transporte"}),
    "e3": EvidenceItem("e3", "https://leyes.senado.gov.co/g/1", Authority.PRIMARY,
                       excerpt="texto con OCR dudoso", quality=QualityStatus.REVIEW_REQUIRED),
}


def test_supported_claim_passes() -> None:
    result = validate_claims([Claim("c1", "El PL 249 de 2024 fue radicado el 2024-08-01.", ("e1",))], CATALOG)
    assert result.status is QueryStatus.ANSWERED and not result.rejected


def test_invented_citation_and_unsupported_numbers_are_rejected() -> None:
    result = validate_claims([
        Claim("c1", "Fue aprobado con 58 votos.", ("e1",)),
        Claim("c2", "Fue radicado en 2024.", ("e99",)),
        Claim("c3", "Trata sobre transporte.", ()),
    ], CATALOG)
    assert result.status is QueryStatus.INSUFFICIENT_EVIDENCE
    problems = {v.claim.id: v.problems for v in result.rejected}
    assert "58" in problems["c1"][0]
    assert "no recuperada" in problems["c2"][0]
    assert problems["c3"] == ("sin evidencia",)


def test_secondary_source_cannot_back_official_section() -> None:
    official = Claim("c1", "El tema oficial es transporte.", ("e2",))
    secondary = Claim("c2", "Congreso Visible lo clasifica como transporte.", ("e2",), section=AnswerSection.SECONDARY)
    result = validate_claims([official, secondary], CATALOG)
    assert [c.id for c in result.accepted_claims] == ["c2"]


def test_literal_quote_of_low_quality_passage_is_rejected() -> None:
    result = validate_claims([Claim("c1", "El texto dice «texto con OCR dudoso».", ("e3",))], CATALOG)
    assert "baja calidad" in result.rejected[0].problems[0]


def _profile(**overrides: Permission) -> UsageProfile:
    base = dict(capture_metadata=Permission.ALLOWED, download_files=Permission.ALLOWED,
                reviewed_at=NOW, reviewer="revisor")
    return UsageProfile(**{**base, **overrides})


def test_unknown_permission_is_never_approval() -> None:
    profile = _profile()
    assert profile.permits(Operation.DOWNLOAD_FILES)
    assert not profile.permits(Operation.INDEX_CONTENT)  # unknown
    with pytest.raises(PolicyViolation):
        profile.require(Operation.REDISTRIBUTE)
    assert not _profile(capture_metadata=Permission.DENIED).permits(Operation.DOWNLOAD_FILES)


def test_activation_diagnostics() -> None:
    source = SourceActivationInput("SRC-01", SourceState.ACTIVE, None, 0, None, [], None, None, None)
    problems = activation_diagnostics(source)
    assert "sin perfil de uso asociado" in problems and "sin alcance de cobertura declarado" in problems
    assert "sin dominios permitidos" in problems
    ready = SourceActivationInput("SRC-01", SourceState.ACTIVE, _profile(), 1, "https://app.senado.gov.co/",
                                  ["senado.gov.co"], "senado_open_data", "0.1.0", "equipo-datos")
    assert activation_diagnostics(ready) == []
