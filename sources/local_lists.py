"""
sources/local_lists.py — Utilidades para búsqueda en listas locales (SQLite).

Las funciones `normalize` y `tokenize` se usan en todas las fuentes
que consultan listas bulk (OFAC, UN, etc).
"""
from __future__ import annotations
import re


def normalize(s: str) -> str:
    """Mayúsculas, sin acentos, espacios colapsados."""
    if not s:
        return ""
    s = s.upper()
    for a, b in (("Á","A"),("É","E"),("Í","I"),("Ó","O"),("Ú","U"),("Ñ","N")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def tokenize(name: str) -> list[str]:
    """Tokens con ≥3 caracteres para búsqueda por tokens."""
    return [t for t in normalize(name).split() if len(t) >= 3]
