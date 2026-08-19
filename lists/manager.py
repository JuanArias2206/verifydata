"""
lists/manager.py — Gestor de listas bulk (OFAC, UN, PEP, ICIJ, etc).

Cada lista se descarga una vez, se guarda en SQLite y se busca en local.
Ahorra ancho de banda y latencia vs. query por query a la fuente original.

Uso:
    from lists.manager import LocalListManager

    mgr = LocalListManager()
    mgr.refresh("ofac_sdn")  # descarga si no está fresca
    rows = mgr.search("ofac_sdn", "VLADIMIR PUTIN")
"""
from __future__ import annotations
import csv
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from db import get_db, init_db, upsert_entries, search_entries, list_meta_get, list_meta_set


def normalize(s: str) -> str:
    """Normalización común para matching por tokens."""
    if not s:
        return ""
    s = s.upper()
    for a, b in (("Á","A"),("É","E"),("Í","I"),("Ó","O"),("Ú","U"),("Ñ","N")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def tokenize(name: str) -> list[str]:
    """Tokeniza un nombre en pedazos ≥3 chars."""
    return [t for t in normalize(name).split() if len(t) >= 3]


class DatasetMissing(RuntimeError):
    """La lista local NO está disponible: la búsqueda no debe reportarse
    como '0 coincidencias' sino como dataset_missing/error."""


class LocalListManager:
    """Gestiona listas bulk. Usa SQLite como cache."""

    DEFAULT_TTL = timedelta(days=7)  # refrescar cada 7 días

    def __init__(self, db_path: Path | None = None):
        from db import DB_PATH
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)

    def meta(self, source: str) -> dict | None:
        with get_db(self.db_path) as conn:
            return list_meta_get(conn, source)

    def needs_refresh(self, source: str, ttl: timedelta | None = None) -> bool:
        # `ttl if ttl is not None` (no `or`): timedelta(0) es falsy pero
        # significa "refrescar siempre".
        ttl = ttl if ttl is not None else self.DEFAULT_TTL
        m = self.meta(source)
        if not m or not m.get("last_fetched"):
            return True
        # last_fetched llega como str ISO (SQLite) o datetime (Postgres
        # TIMESTAMPTZ); as_naive_utc lo normaliza a datetime naive UTC.
        from db import as_naive_utc
        try:
            last = as_naive_utc(m["last_fetched"])
        except Exception:
            return True
        # Comparar contra utcnow() (las fechas se guardan en UTC), no now()
        # local (en Colombia el TTL corría 5h tarde).
        return datetime.utcnow() - last > ttl

    def refresh(self, source: str, fetcher: Callable[[], tuple[list[dict], str, str]],
               *, name_key: str = "name", id_key: str | None = None,
               force: bool = False) -> int:
        """Descarga y guarda la lista. fetcher() -> (rows, url, format)."""
        if not force and not self.needs_refresh(source):
            m = self.meta(source)
            return m.get("last_count", 0) if m else 0
        rows, url, fmt = fetcher()
        with get_db(self.db_path) as conn:
            n = upsert_entries(conn, source, rows, name_key, id_key)
            list_meta_set(conn, source, url=url, count=n, format=fmt)
        return n

    def search(self, source: str, nombre: str, limit: int = 30) -> list[dict]:
        """Busca por tokens. Todos los tokens (≥3 chars) deben estar
        presentes en el name_norm de la entrada."""
        tokens = tokenize(nombre)
        with get_db(self.db_path) as conn:
            if tokens:
                return search_entries(conn, source, "", tokens=tokens, limit=limit)
            return search_entries(conn, source, normalize(nombre), limit=limit)

    def count(self, source: str) -> int:
        m = self.meta(source)
        return m.get("last_count", 0) if m else 0

    def ensure_dataset(self, source: str,
                       fetcher: Callable[[], tuple[list[dict], str, str]],
                       *, min_rows: int = 1, name_key: str = "name",
                       id_key: str | None = None,
                       ttl: timedelta | None = None) -> dict:
        """Garantiza que la lista local existe y tiene ≥ min_rows registros
        ANTES de permitir una búsqueda.

        - Si el TTL venció o la lista está vacía → descarga (force).
        - Si la descarga falla pero hay cache previa válida → la usa y marca
          stale=True (el caller debe reportar dataset_stale).
        - Si no hay forma de tener datos → lanza DatasetMissing: el caller
          NUNCA debe responder '0 coincidencias'.

        Devuelve el meta dict: {last_fetched, last_count, stale, ...}."""
        stale = False
        refresh_error = None
        needs = self.needs_refresh(source, ttl) or self.count(source) < min_rows
        if needs:
            try:
                self.refresh(source, fetcher, name_key=name_key,
                             id_key=id_key, force=True)
            except Exception as e:
                refresh_error = str(e)
                if self.count(source) >= min_rows:
                    stale = True   # hay cache previa utilizable
                else:
                    raise DatasetMissing(
                        f"dataset '{source}' no disponible: descarga falló "
                        f"({e}) y no hay cache local") from e
        m = self.meta(source) or {}
        if m.get("last_count", 0) < min_rows:
            raise DatasetMissing(
                f"dataset '{source}' vacío tras descarga "
                f"({m.get('last_count', 0)} filas; mínimo {min_rows})")
        m["stale"] = stale
        if refresh_error:
            m["refresh_error"] = refresh_error
        return m
