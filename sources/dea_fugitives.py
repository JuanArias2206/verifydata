"""
sources/dea_fugitives.py — Drug Enforcement Administration (DEA) Most Wanted Fugitives.

URL: https://www.dea.gov/fugitives/all
Categoría: Reputacional y noticias.

Implementación:
    La página oficial de la DEA (https://www.dea.gov/fugitives/all) está
    detrás de Akamai/EdgeSuite y bloquea tanto `requests` con UA genérica
    como Playwright headless desde IPs no-US. Sin embargo, el sitio renderiza
    server-side (es Drupal, no SPA), por lo que el HTML inicial contiene la
    lista completa de fugitivos con sus nombres, descripciones de cargos y
    enlaces a su perfil individual.

    Para superar el bloqueo de Akamai usamos Wayback Machine como pasarela
    probada (snapshot más reciente de archive.org, mismo HTML, misma
    estructura). Si en el futuro la IP del runner es US, el código intenta
    primero la URL directa.

    La lista completa (~540 entradas) se cachea localmente en SQLite vía
    `LocalListManager` con TTL=7 días. Cada `fetch()` es una consulta SQL
    por tokens (sub-100ms), no se vuelve a pegar al sitio.

    Patrón idéntico a `FbiWantedSource` (sources/wanted.py).
"""
from __future__ import annotations

import time
from datetime import timedelta

from .base import Hit
from .registry import register
from .local_lists import normalize, tokenize
from lists import LocalListManager
from lists.downloaders import dea_fugitives as dea_fugitives_downloader


# Manager local (cachea en data/lists/us_dea_fugitives)
_mgr = LocalListManager()
_TTL = timedelta(days=7)


def _token_match_rows(nombre: str, rows: list[dict],
                      name_key: str = "name") -> list[dict]:
    """Filtra rows cuyos tokens estén todos presentes en name normalizado."""
    tokens = tokenize(nombre)
    out: list[dict] = []
    for r in rows:
        n = normalize(r.get(name_key, ""))
        if not n:
            continue
        if tokens and all(t in n for t in tokens):
            out.append(r)
        elif not tokens and nombre.upper() in n:
            out.append(r)
    return out


@register
class DeaFugitivesSource:
    """DEA Most Wanted Fugitives (lista oficial de ~540 prófugos).

    Nombre distinto al stub "DEA — Most Wanted Fugitives" en
    sources/wanted.py para evitar duplicados en el registry. La
    convención del proyecto (ver europol.py + wanted.py) es que el
    stub queda en wanted.py con el nombre "corto" y la implementación
    real usa un nombre que indica la versión con datos detallados."""

    name = "DEA — Most Wanted Fugitives (detallado)"
    source_url = "https://www.dea.gov/fugitives/all"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre: str, cedula: str | None = None,
              fecha_exp: str | None = None, solver=None) -> Hit:
        t0 = time.time()
        try:
            if not nombre or not nombre.strip():
                return Hit(
                    source=self.name, matched=False, summary="",
                    notice="Requiere nombre para buscar en DEA Most Wanted.",
                    evidence_urls=[self.source_url],
                    elapsed_s=time.time() - t0,
                )

            # Refrescar la lista si el TTL expiró (o si nunca se descargó).
            if _mgr.needs_refresh("us_dea_fugitives", ttl=_TTL):
                _mgr.refresh("us_dea_fugitives", dea_fugitives_downloader,
                             force=False)

            # Búsqueda local en SQLite por tokens.
            rows = _mgr.search("us_dea_fugitives", nombre)
            rows = _token_match_rows(nombre, rows)
            elapsed = time.time() - t0

            if rows:
                details = [
                    {k: v for k, v in r.items()
                     if v not in (None, "", "N/A")}
                    for r in rows[:20]
                ]
                first = rows[0]
                return Hit(
                    source=self.name,
                    matched=True,
                    summary=f"REGISTRA en DEA Most Wanted Fugitives: {first['name']}",
                    details=details,
                    download_url=first.get("url"),
                    evidence_urls=[r.get("url") for r in rows if r.get("url")],
                    elapsed_s=elapsed,
                )
            return Hit(
                source=self.name,
                matched=False,
                summary="SIN COINCIDENCIA en DEA Most Wanted Fugitives",
                evidence_urls=[self.source_url],
                elapsed_s=elapsed,
            )
        except Exception as e:
            return Hit(
                source=self.name, matched=False, summary="",
                error=f"{type(e).__name__}: {e}",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )
