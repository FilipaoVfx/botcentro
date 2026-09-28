"""Planificación de consultas: comandos y referencias (SRS-F16)."""


def test_proyecto_command_accepts_number_without_type() -> None:
    from datetime import datetime

    from botcentro.domain.dates import BOGOTA
    from botcentro.query.intent import plan_query

    plan = plan_query("/proyecto 396 de 2026 Cámara", datetime(2026, 9, 28, tzinfo=BOGOTA))
    [ref] = plan.project_refs
    assert (ref.number, ref.filing_year, ref.corporation.value) == ("396", 2026, "camara")
