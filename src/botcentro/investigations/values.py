"""Importes, identificadores y fechas de fuentes (DAT-04, DAT-06, SEG-02; T-05, T-18).

* Importes con `Decimal`: se conserva el signo y la precisión; desconocido es `None`, nunca 0.
* Identificadores como texto: sin pasar por flotantes, sin perder ceros ni dígito de verificación.
* Documentos de personas: solo HMAC (con clave fuera de la base) y valor enmascarado.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_EMPTY = {"", "no definido", "sin descripcion", "sin descripción", "no aplica", "n/a", "na", "null", "none"}


class InvalidValue(ValueError):
    """Un valor que la fuente declaró con un tipo no se puede interpretar: no se inventa."""


def money(raw: object) -> Decimal | None:
    """«-12.50» → Decimal('-12.50'); «» o None → None. Rechaza flotantes: pierden precisión."""
    if raw is None:
        return None
    if isinstance(raw, float):
        raise InvalidValue("un importe no puede llegar como float: usar el texto de la fuente")
    text = str(raw).strip().replace(" ", "")
    if text.lower() in _EMPTY:
        return None
    if not _NUMERIC_RE.match(text):
        raise InvalidValue(f"importe no numérico: {text[:30]!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - cubierto por la expresión regular
        raise InvalidValue(str(exc)) from exc


def identifier(raw: object) -> str | None:
    """Identificador como texto: se quitan espacios de relleno, nada más (ceros y DV se conservan)."""
    if raw is None:
        return None
    if isinstance(raw, float):
        raise InvalidValue("un identificador no puede pasar por float (DAT-04)")
    text = str(raw).strip()
    if text.lower() in _EMPTY:
        return None
    if isinstance(raw, int):
        return str(raw)
    return text


def mask(value: str, *, visible: int = 3) -> str:
    """«1234567890» → «*******890». Nunca muestra más de `visible` caracteres."""
    value = value.strip()
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


def identity_hmac(key: bytes, issuer: str, id_type: str, value: str) -> str:
    """Seudónimo estable de un documento: igual documento ⇒ igual HMAC, sin guardar el número."""
    normalized = re.sub(r"[\s.\-]", "", value).upper()
    message = f"{issuer}|{id_type}|{normalized}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def source_date(raw: object) -> date | None:
    """Fechas de Socrata («2025-09-17T00:00:00.000») o texto «dd/mm/aaaa» de SIRI; sin inventar hora."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in _EMPTY:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise InvalidValue(f"fecha no reconocida: {text[:30]!r}")


def is_person_document(id_type: str | None) -> bool:
    """NIT de persona jurídica es público; cédulas, pasaportes y documentos de extranjería no."""
    folded = (id_type or "").strip().lower()
    return not (folded in {"nit", "31"} or folded.startswith("nit"))
