"""Normalización de nombres para búsqueda de candidatos (SRS-F08).

El nombre normalizado sirve para proponer coincidencias, nunca para fusionar identidades:
homónimos y cambios de nombre exigen identificadores o evidencia (T-07).
"""

from __future__ import annotations

import re
import unicodedata


def normalize_name(name: str) -> str:
    """«José  María Pérez-Gómez» → «jose maria perez-gomez»."""
    decomposed = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[^\w\s'-]", " ", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return " ".join(text.split())


def display_name(name: str) -> str:
    """Espacios normalizados y forma NFC, conservando mayúsculas y tildes originales."""
    return " ".join(unicodedata.normalize("NFC", name).split())
