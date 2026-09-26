"""Fechas legislativas: precisión explícita y calendario de America/Bogota.

Reglas (SRS §2, SRS-F11, PRD §9.3):
  * Los instantes se guardan en UTC y se presentan en America/Bogota.
  * Una fecha sin día exacto conserva su precisión (mes, año o desconocida).
  * Las consultas de un día usan el intervalo local completo [00:00, 00:00 del día siguiente).
  * Las expresiones relativas («mañana») se convierten a fechas explícitas de Colombia.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from botcentro.domain.enums import DatePrecision

BOGOTA = ZoneInfo("America/Bogota")

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]
WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
WEEKDAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class DateParseError(ValueError):
    """El texto no corresponde a un formato de fecha reconocido o la fecha no existe."""


@dataclass(frozen=True, slots=True)
class PartialDate:
    """Fecha con precisión. Con precisión mes/año, `value` es el primer día del periodo."""

    value: date | None
    precision: DatePrecision

    def __post_init__(self) -> None:
        if (self.value is None) != (self.precision is DatePrecision.UNKNOWN):
            raise ValueError("value es None si y solo si la precisión es 'unknown'")
        if self.value is not None:
            if self.precision is DatePrecision.MONTH and self.value.day != 1:
                raise ValueError("una fecha con precisión de mes debe ser el día 1")
            if self.precision is DatePrecision.YEAR and (self.value.month, self.value.day) != (1, 1):
                raise ValueError("una fecha con precisión de año debe ser el 1 de enero")

    @classmethod
    def unknown(cls) -> PartialDate:
        return cls(None, DatePrecision.UNKNOWN)

    @classmethod
    def of_day(cls, value: date) -> PartialDate:
        return cls(value, DatePrecision.DAY)

    def iso(self) -> str | None:
        """Representación ISO 8601 truncada a la precisión: 2025-03-15, 2025-03 o 2025."""
        if self.value is None:
            return None
        if self.precision is DatePrecision.DAY:
            return self.value.isoformat()
        if self.precision is DatePrecision.MONTH:
            return f"{self.value.year:04d}-{self.value.month:02d}"
        return f"{self.value.year:04d}"


@dataclass(frozen=True, slots=True)
class DateRange:
    """Intervalo de fechas locales, ambos extremos incluidos."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("el final del intervalo es anterior al inicio")

    @property
    def is_single_day(self) -> bool:
        return self.start == self.end

    def utc_bounds(self) -> tuple[datetime, datetime]:
        """Intervalo semiabierto en UTC que cubre todos los días locales del rango."""
        start, _ = local_day_bounds(self.start)
        _, end = local_day_bounds(self.end)
        return start, end


def _fold(text: str) -> str:
    """Minúsculas, sin tildes y con espacios normalizados."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def _build_day(year: int, month: int, day: int, original: str) -> PartialDate:
    try:
        return PartialDate.of_day(date(year, month, day))
    except ValueError as exc:
        raise DateParseError(f"fecha inexistente: {original!r}") from exc


_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[t\s].*)?$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_LONG_RE = re.compile(
    r"^(?:(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo),?\s+)?"
    r"(\d{1,2})\s+de\s+([a-z]+)\s+(?:de|del)\s+(\d{4})$"
)
_MONTH_RE = re.compile(r"^([a-z]+)\s+(?:de|del)\s+(\d{4})$")
_YEAR_RE = re.compile(r"^(\d{4})$")


def parse_spanish_date(text: str | None) -> PartialDate:
    """Interpreta fechas habituales en fuentes colombianas.

    Formatos: ``2025-03-15``, ``15/03/2025`` (día primero), ``martes, 15 de marzo de 2025``,
    ``marzo de 2025`` y ``2025``. Vacío o None devuelve precisión desconocida; un formato no
    reconocido o una fecha inexistente lanza :class:`DateParseError` para que el parser lo
    registre como incidencia en lugar de adivinar.
    """
    if text is None or not text.strip():
        return PartialDate.unknown()
    folded = _fold(text).rstrip(".")

    if m := _ISO_RE.match(folded):
        return _build_day(int(m[1]), int(m[2]), int(m[3]), text)
    if m := _NUMERIC_RE.match(folded):
        return _build_day(int(m[3]), int(m[2]), int(m[1]), text)
    if m := _LONG_RE.match(folded):
        month = MONTHS.get(m[2])
        if month is None:
            raise DateParseError(f"mes no reconocido: {text!r}")
        return _build_day(int(m[3]), month, int(m[1]), text)
    if m := _MONTH_RE.match(folded):
        month = MONTHS.get(m[1])
        if month is None:
            raise DateParseError(f"mes no reconocido: {text!r}")
        return PartialDate(date(int(m[2]), month, 1), DatePrecision.MONTH)
    if m := _YEAR_RE.match(folded):
        return PartialDate(date(int(m[1]), 1, 1), DatePrecision.YEAR)
    raise DateParseError(f"formato de fecha no reconocido: {text!r}")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("se requiere un datetime con zona horaria")
    return value


def to_local(value: datetime) -> datetime:
    return require_aware(value).astimezone(BOGOTA)


def local_date(value: datetime) -> date:
    """Fecha de calendario en Colombia de un instante."""
    return to_local(value).date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Intervalo semiabierto [inicio, fin) en UTC del día local indicado."""
    start = datetime.combine(day, time.min, tzinfo=BOGOTA)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=BOGOTA)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


_RELATIVE_DAYS = {
    "hoy": 0,
    "manana": 1,
    "pasado manana": 2,
    "ayer": -1,
    "anteayer": -2,
    "antier": -2,
}
_WEEKDAY_RE = re.compile(
    r"^(?:(?:el|este|esta)\s+)?(?:(proximo|proxima|siguiente)\s+)?"
    r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
    r"(?:\s+(proximo|siguiente|que viene))?$"
)


def resolve_relative_period(expression: str, now: datetime) -> DateRange | None:
    """Convierte una expresión temporal relativa en fechas de Colombia.

    * «hoy», «mañana», «pasado mañana», «ayer», «anteayer»/«antier».
    * «esta semana», «la próxima semana»/«la semana que viene» (lunes a domingo).
    * «el lunes»/«este lunes»: la próxima ocurrencia a partir de hoy (incluido).
      «el próximo lunes»/«el lunes que viene»: estrictamente después de hoy.

    Devuelve None si la expresión no se reconoce. El llamante debe mostrar siempre la fecha
    resultante para que cualquier ambigüedad sea visible (PRD §9.3).
    """
    today = local_date(now)
    folded = _fold(expression).strip(" ?¿!¡.,")
    folded = re.sub(r"^(para|de|del|en)\s+", "", folded)

    if folded in _RELATIVE_DAYS:
        day = today + timedelta(days=_RELATIVE_DAYS[folded])
        return DateRange(day, day)

    week_start = today - timedelta(days=today.weekday())
    if folded in {"esta semana", "la semana", "semana actual"}:
        return DateRange(week_start, week_start + timedelta(days=6))
    if folded in {"la proxima semana", "proxima semana", "la semana que viene", "la semana entrante"}:
        start = week_start + timedelta(days=7)
        return DateRange(start, start + timedelta(days=6))

    if m := _WEEKDAY_RE.match(folded):
        prefix, weekday_name, suffix = m[1], m[2], m[3]
        target = WEEKDAYS[weekday_name]
        strictly_after = prefix is not None or suffix is not None
        delta = (target - today.weekday()) % 7
        if delta == 0 and strictly_after:
            delta = 7
        day = today + timedelta(days=delta)
        return DateRange(day, day)
    return None


def format_local_date(day: date) -> str:
    """«jueves 25 de septiembre de 2026»."""
    return f"{WEEKDAY_NAMES[day.weekday()]} {day.day} de {MONTH_NAMES[day.month - 1]} de {day.year}"


def format_partial_date(value: PartialDate) -> str:
    """Presentación en español respetando la precisión; nunca inventa el día."""
    if value.value is None:
        return "fecha desconocida"
    if value.precision is DatePrecision.DAY:
        return f"{value.value.day} de {MONTH_NAMES[value.value.month - 1]} de {value.value.year}"
    if value.precision is DatePrecision.MONTH:
        return f"{MONTH_NAMES[value.value.month - 1]} de {value.value.year}"
    return str(value.value.year)
