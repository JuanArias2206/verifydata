"""
sources/bis_dpl.py — BIS (Bureau of Industry and Security) Denied Persons List.

Portal: https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl
         (también se puede descargar el CSV bulk en
         https://media.bis.gov/sites/default/files/dpl_*.csv)

Observaciones del sitio:
  - Tabla HTML estática con la pestaña "Full list" activa. No requiere
    captcha, no requiere JavaScript, no requiere autenticación.
  - La página entera pesa ~750 KB (565 filas × 5 columnas) y se sirve
    desde el origen con cache `s-maxage=60, stale-while-revalidate`. Es
    seguro cachear localmente por un proceso (varias queries de la misma
    ejecución reutilizan el mismo HTML).
  - La lista BIS DPL contiene personas y entidades a las que se les han
    negado privilegios de exportación desde USA. NO es la misma lista
    que OFAC SDN (que sí incluye a políticos/designados como Maduro);
    este parser NO va a encontrar políticos/designados por nombre de
    país. La búsqueda por nombre debe hacerse sobre la columna
    "Name and Address".
  - No tiene search box visible; la página es una sola tabla grande.
    Para "buscar" hay que descargar la página completa y filtrar en
    memoria por nombre.

Implementación:
  1. GET con `requests` (timeout 30s) → parsear la única `<table>` con
     `beautifulsoup4` (lxml parser).
  2. Cachear el HTML parseado en memoria (lazy) para no volver a
     bajarlo en cada query.
  3. Para cada fila, comparar el contenido de la primera celda
     ("Name and Address") contra el query del usuario: token match
     case-insensitive (todas las palabras del query deben aparecer en
     el texto de la celda, en cualquier orden).
  4. matched=True si hay al menos una coincidencia, matched=False si
     no. evidence_urls = [source_url]. download_url = None (es
     scraping puro, no descargamos cert ni PDF).
"""
from __future__ import annotations

import threading
import time
from functools import lru_cache

from .base import Hit
from .registry import register


SOURCE_URL = "https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl"
HEADERS = ["Name and Address", "Effective Date", "Expiration Date",
           "Appropriate Federal Register Citations", "Type of Denial"]
# Columnas que se reportan en details[] cuando hay match
_DETAIL_KEYS = ["name_and_address", "effective_date", "expiration_date",
                "federal_register_citations", "type_of_denial"]


# ---------- Cache de página en proceso ----------
# _page_lock protege _PAGE_CACHE. _PAGE_CACHE es una lista de dicts
# con las filas parseadas (o None si el último fetch falló). Usar
# threading.Lock porque los sources se ejecutan en
# ThreadPoolExecutor (ver sources/registry.py:run_all).
_PAGE_LOCK = threading.Lock()
_PAGE_CACHE: list[dict] | None = None
_PAGE_LAST_FETCH: float = 0.0
# Re-fetch cada 10 min. La página cachea en CDN 60s, pero la lista
# cambia lento (típicamente ~10 entradas/semana según el "List of
# Changes" que se ve en la tabla 2 de la página).
_PAGE_TTL_S = 600.0


def _fetch_page(timeout: int = 45) -> list[dict]:
    """Descarga la página BIS DPL, la parsea, y devuelve una lista de
    dicts con keys = HEADERS. Lanza excepción si algo falla.

    Usa Session con retry (3 intentos, backoff) porque bis.gov presenta
    ReadTimeouts intermitentes. Se cachea en módulo (thread-safe)."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from bs4 import BeautifulSoup

    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1.0, connect=3, read=2,
                    status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    r = s.get(
        SOURCE_URL,
        timeout=timeout,
        headers={
            "User-Agent": "VerifyData-Demo/1.0 (compatible; +https://verifydata.example)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        allow_redirects=True,
    )
    r.raise_for_status()
    html = r.text

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("BIS DPL: no <table> found in HTML")

    # La primera tabla es "Full list" con 5 columnas exactas
    # (verificado: 565 filas con headers HEADERS). Filtramos por
    # headers para ser tolerantes a reordenamientos.
    for t in tables:
        first_row = t.find("tr")
        if not first_row:
            continue
        header_cells = [c.get_text(" ", strip=True) for c in
                        first_row.find_all(["th", "td"])]
        if header_cells[:5] == HEADERS:
            return _parse_rows(t)

    # Si ninguna tabla tiene los headers esperados, fallar con info útil
    raise ValueError(
        f"BIS DPL: no table with expected headers {HEADERS}. "
        f"Found: {[ [c.get_text(' ', strip=True)[:30] for c in t.find('tr').find_all(['th','td'])] for t in tables if t.find('tr') ]}"
    )


def _parse_rows(table) -> list[dict]:
    """Convierte una <table> con headers=HEADERS en una lista de
    dicts. Tolera celdas faltantes (rellena con '')."""
    rows: list[dict] = []
    body_rows = table.find_all("tr")[1:]  # saltar header
    for tr in body_rows:
        cells = tr.find_all(["td", "th"])
        # Sólo las primeras 5 celdas; extras (si las hay) se ignoran
        texts = [c.get_text(" ", strip=True) for c in cells[:5]]
        # Padding si vienen menos de 5
        while len(texts) < 5:
            texts.append("")
        rows.append({k: v for k, v in zip(_DETAIL_KEYS, texts)})
    return rows


def _get_cached_rows() -> list[dict]:
    """Devuelve la lista cacheada, refrescando si pasó el TTL."""
    global _PAGE_CACHE, _PAGE_LAST_FETCH
    with _PAGE_LOCK:
        now = time.time()
        if _PAGE_CACHE is not None and (now - _PAGE_LAST_FETCH) < _PAGE_TTL_S:
            return _PAGE_CACHE
        _PAGE_CACHE = _fetch_page()
        _PAGE_LAST_FETCH = now
        return _PAGE_CACHE


def _tokenize(s: str) -> list[str]:
    """Tokeniza en minúsculas, separando por no-alfanuméricos.
    Filtra tokens de 1 sola letra y dígitos solos."""
    import re
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) >= 2]


def _row_matches(row: dict, query: str) -> bool:
    """True si todos los tokens del query aparecen en el campo
    name_and_address de la fila (case-insensitive)."""
    name = (row.get("name_and_address") or "").lower()
    if not name:
        return False
    tokens = _tokenize(query)
    if not tokens:
        # Sin tokens útiles, fallback a substring literal
        return query.lower().strip() in name
    return all(t in name for t in tokens)


@register
class BisDeniedPersonsSource:
    name = "BIS — Denied Persons List (USA)"
    source_url = SOURCE_URL
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None) -> Hit:
        t0 = time.time()
        if not nombre or not nombre.strip():
            return Hit(
                self.name, False, "",
                notice="Requiere un nombre para buscar en BIS DPL.",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        from lists import LocalListManager
        mgr = LocalListManager()
        stale = False
        version = ""
        live_err: Exception | None = None
        matches: list[dict] = []
        records = 0
        try:
            rows = _get_cached_rows()
            records = len(rows)
            matches = [r for r in rows if _row_matches(r, nombre.strip())]
            # Persistir en SQLite: fallback para cuando bis.gov haga timeout.
            try:
                mgr.refresh("bis_dpl", lambda: (
                    [{**r, "name": r.get("name_and_address", "")} for r in rows],
                    SOURCE_URL, "html"), force=True)
            except Exception:
                pass
        except Exception as e:
            live_err = e
            # Fallback: copia local previa (si es utilizable).
            records = mgr.count("bis_dpl")
            if records < 10:
                etype = ("timeout" if "timeout" in str(e).lower()
                         or "timed out" in str(e).lower() else "network")
                return Hit(
                    self.name, False,
                    "NO FUE POSIBLE CONSULTAR BIS (sitio no respondió y no "
                    "hay copia local)",
                    error=f"{type(e).__name__}: {e}",
                    error_type=etype,
                    evidence_urls=[self.source_url],
                    elapsed_s=time.time() - t0,
                )
            stale = True
            meta = mgr.meta("bis_dpl") or {}
            version = (meta.get("last_fetched") or "")[:19]
            matches = mgr.search("bis_dpl", nombre.strip())

        # Cap de evidencia a 30 filas (consistente con internacionales.py)
        details = [dict(r) for r in matches[:30]]

        if not matches:
            hit = Hit(
                self.name, False,
                f"SIN COINCIDENCIA en BIS Denied Persons List "
                f"({records} registros verificados)",
                details=[],
                status="dataset_stale" if stale else "nomatch_verified",
                dataset_records=records,
                dataset_version=version or None,
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )
            if stale:
                hit.notice = (f"bis.gov no respondió ({live_err}); resultado "
                              f"basado en la copia local de {version}.")
            return hit

        # Construir summary legible con la primera coincidencia
        first = matches[0]
        name_text = (first.get("name_and_address")
                     or first.get("name") or "(sin nombre)")
        eff = first.get("effective_date") or "?"
        n = len(matches)
        head = "REGISTRA" if n == 1 else f"REGISTRA ({n} coincidencias)"
        summary = f"{head} en BIS Denied Persons List: {name_text} (effective {eff})"

        hit = Hit(
            self.name, True,
            summary,
            details=details,
            status="match_probable",
            confidence="fuerte",
            matched_name=name_text,
            dataset_records=records,
            dataset_version=version or None,
            evidence_urls=[self.source_url],
            elapsed_s=time.time() - t0,
        )
        if stale:
            hit.notice = (f"Coincidencia hallada en copia local de {version} "
                          f"(bis.gov no respondió en vivo).")
        return hit


# Helper exportado para tests y reuso desde otras tools
def clear_cache() -> None:
    """Limpia el cache en proceso (útil para tests)."""
    global _PAGE_CACHE, _PAGE_LAST_FETCH
    with _PAGE_LOCK:
        _PAGE_CACHE = None
        _PAGE_LAST_FETCH = 0.0
