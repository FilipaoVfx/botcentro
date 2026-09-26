from datetime import date, datetime, timezone

import pytest

from botcentro.domain.dates import (
    DateParseError,
    DateRange,
    PartialDate,
    format_local_date,
    format_partial_date,
    local_date,
    local_day_bounds,
    parse_spanish_date,
    resolve_relative_period,
)
from botcentro.domain.enums import DatePrecision


@pytest.mark.parametrize(
    ("text", "expected", "precision"),
    [
        ("2025-03-15", date(2025, 3, 15), DatePrecision.DAY),
        ("15/03/2025", date(2025, 3, 15), DatePrecision.DAY),
        ("martes, 15 de marzo de 2025", date(2025, 3, 15), DatePrecision.DAY),
        ("1 de Septiembre del 2026", date(2026, 9, 1), DatePrecision.DAY),
        ("Setiembre de 2024", date(2024, 9, 1), DatePrecision.MONTH),
        ("2024", date(2024, 1, 1), DatePrecision.YEAR),
    ],
)
def test_parse_spanish_date_keeps_precision(text: str, expected: date, precision: DatePrecision) -> None:
    assert parse_spanish_date(text) == PartialDate(expected, precision)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_is_unknown_not_zero(text: str | None) -> None:
    assert parse_spanish_date(text) == PartialDate.unknown()


@pytest.mark.parametrize("text", ["31 de febrero de 2025", "próximamente", "15 de brumario de 2025"])
def test_unrecognized_or_impossible_dates_raise(text: str) -> None:
    with pytest.raises(DateParseError):
        parse_spanish_date(text)


def test_partial_date_invariants() -> None:
    with pytest.raises(ValueError):
        PartialDate(date(2025, 3, 15), DatePrecision.MONTH)
    with pytest.raises(ValueError):
        PartialDate(None, DatePrecision.DAY)
    assert PartialDate(date(2025, 3, 1), DatePrecision.MONTH).iso() == "2025-03"
    assert format_partial_date(PartialDate(date(2025, 3, 1), DatePrecision.MONTH)) == "marzo de 2025"


def test_tomorrow_near_utc_midnight_uses_bogota_calendar() -> None:
    # T-11: 02:30 UTC del 26/09 son las 21:30 del 25/09 en Bogotá; «mañana» es el 26/09.
    now = datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)
    assert local_date(now) == date(2026, 9, 25)
    assert resolve_relative_period("mañana", now) == DateRange(date(2026, 9, 26), date(2026, 9, 26))
    assert resolve_relative_period("¿hoy?", now) == DateRange(date(2026, 9, 25), date(2026, 9, 25))


def test_local_day_bounds_cover_the_whole_local_day() -> None:
    start, end = local_day_bounds(date(2026, 9, 26))
    assert start == datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)


def test_weekday_expressions() -> None:
    thursday = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    assert resolve_relative_period("el jueves", thursday).start == date(2026, 9, 24)
    assert resolve_relative_period("el próximo jueves", thursday).start == date(2026, 10, 1)
    assert resolve_relative_period("el lunes", thursday).start == date(2026, 9, 28)
    week = resolve_relative_period("la próxima semana", thursday)
    assert (week.start, week.end) == (date(2026, 9, 28), date(2026, 10, 4))
    assert resolve_relative_period("cuando sea", thursday) is None


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError):
        local_date(datetime(2026, 9, 26, 12, 0))


def test_format_local_date() -> None:
    assert format_local_date(date(2026, 9, 26)) == "sábado 26 de septiembre de 2026"
