"""
sources/internacionales.py — Listas internacionales de sanciones.

Fuentes sin captcha que consultan listas bulk cacheadas en SQLite:
  - OFAC Non-SDN Consolidated (XML)
  - OFAC Address (CSV)
  - BIS Denied Persons (manual, link a búsqueda)
  - EU Consolidated (intenta, con fallback)
  - Canada SEMA-LMES (XML)
  - World Bank Ineligible (HTML scrape)
"""
from __future__ import annotations
import time
from .base import Hit
from .registry import register
from .local_lists import normalize, tokenize
from lists import LocalListManager
from lists.manager import DatasetMissing
from lists.downloaders import (
    ofac_consolidated, ofac_addrs, bis_dpl, eu_consolidated,
    canada_sema, worldbank_ineligible,
)


_mgr = LocalListManager()


def _hit_from_rows(rows: list[dict], source_name: str, summary: str,
                   program_key: str = "program") -> Hit:
    if rows:
        details = [{k: v for k, v in r.items() if v not in (None, "", "N/A")}
                   for r in rows[:30]]
        return Hit(source_name, True,
                   f"{len(details)} coincidencia(s)", details)
    return Hit(source_name, False, "0 coincidencias")


def _list_lookup(source_name: str, list_key: str, fetcher, nombre: str,
                 label: str, *, min_rows: int = 100, t0: float = 0.0) -> Hit:
    """Búsqueda estándar sobre una lista bulk con validación de dataset.

    Garantiza que la lista local está cargada ANTES de buscar. Un dataset
    ausente/vacío jamás se reporta como '0 coincidencias'."""
    try:
        meta = _mgr.ensure_dataset(list_key, fetcher, min_rows=min_rows)
    except DatasetMissing as e:
        return Hit(source_name, False,
                   "NO FUE POSIBLE CONSULTAR: dataset local no disponible",
                   status="dataset_missing", error_type="dataset_missing",
                   notice=str(e), elapsed_s=time.time() - t0)
    version = (meta.get("last_fetched") or "")[:19]
    records = meta.get("last_count", 0)
    stale = meta.get("stale", False)
    rows = _token_match_rows(nombre, _mgr.search(list_key, nombre))
    if rows:
        hit = _hit_from_rows(rows, source_name,
                             f"{len(rows)} coincidencia(s) ({label})")
        hit.status = "match_probable"
        hit.confidence = "fuerte"
        hit.matched_name = rows[0].get("name", "")
        hit.notes = ("Coincidencia por NOMBRE (todas las palabras del nombre "
                     "aparecen en la entrada). Verificar identidad: posible "
                     "homónimo.")
    else:
        hit = Hit(source_name, False,
                  f"0 coincidencias en {label} "
                  f"(dataset verificado: {records:,} registros)".replace(",", "."),
                  status="nomatch_verified")
    hit.dataset_version = version
    hit.dataset_records = records
    hit.elapsed_s = time.time() - t0
    if stale:
        hit.status = "dataset_stale" if not rows else hit.status
        hit.notice = ((hit.notice + " ") if hit.notice else "") + (
            f"El dataset local está VENCIDO (descarga falló: "
            f"{meta.get('refresh_error', 's/d')}); resultado basado en la "
            f"última copia de {version}.")
    return hit


def _token_match_rows(nombre: str, rows: list[dict], name_key: str = "name") -> list[dict]:
    tokens = tokenize(nombre)
    out = []
    for r in rows:
        n = normalize(r.get(name_key, ""))
        if not n: continue
        if tokens and all(t in n for t in tokens):
            out.append(r)
        elif not tokens and nombre.upper() in n:
            out.append(r)
    return out


# ---------- OFAC Non-SDN Consolidated ----------
@register
class OfacConsolidatedSource:
    name = "OFAC — Lista Consolidada (Non-SDN, FSE, SSI, CAPTA)"
    source_url = "https://sanctionssearch.ofac.treas.gov/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            return _list_lookup(self.name, "ofac_consolidated",
                                ofac_consolidated, nombre,
                                "OFAC Consolidated", min_rows=100, t0=t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- OFAC Address ----------
@register
class OfacAddrsSource:
    name = "OFAC — Direcciones y aliases (add.csv)"
    source_url = "https://sanctionssearch.ofac.treas.gov/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            return _list_lookup(self.name, "ofac_addrs", ofac_addrs, nombre,
                                "OFAC aliases", min_rows=100, t0=t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- BIS Denied Persons ----------
# DEPRECATED 2026-06-13 — duplicado de `BisDeniedPersonsSource` en
# sources/bis_dpl.py ("BIS — Denied Persons List (USA)"), que parsea la
# tabla "Full list" del sitio oficial con cache. Se conserva la clase
# por si alguien la quiere rehabilitar; para reactivar, descomentar
# la línea `@register` de abajo.
# @register
class BisDplSource:
    name = "BIS — Denied Persons List"
    source_url = "https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
        # BIS usa JavaScript. Como fallback, generamos link directo
        # con el query prellenado. La verificación visual la hace el
        # usuario al abrir el enlace (es lo que el PDF original hace).
        search_url = ("https://www.bis.gov/licensing/end-user-guidance/"
                      "denied-persons-list-dpl")
        # Intentar primero el downloader (puede no tener CSV público)
        try:
            if _mgr.needs_refresh("bis_dpl"):
                _mgr.refresh("bis_dpl", bis_dpl, force=False)
            rows = _mgr.search("bis_dpl", nombre)
            rows = _token_match_rows(nombre, rows)
            if rows:
                hit = _hit_from_rows(rows, self.name,
                                     f"{len(rows)} coincidencia(s) (BIS DPL)")
                hit.evidence_urls = [search_url, self.source_url]
                return hit
        except Exception:
            pass
        # Sin resultados o sin CSV: dejar aviso con link
        return Hit(self.name, False,
                   f"BIS JS-driven — abrir para verificar '{nombre}'",
                   notice="BIS usa JavaScript. Para búsqueda automática se "
                          "requiere Playwright. Click 'abrir fuente' para "
                          "verificar manualmente.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)


# ---------- EU Consolidated Sanctions ----------
@register
class EuConsolidatedSource:
    name = "UE — Lista Consolidada de Sanciones"
    source_url = "https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            return _list_lookup(self.name, "eu_consolidated", eu_consolidated,
                                nombre, "UE Consolidated", min_rows=1000, t0=t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"No fue posible consultar la lista UE. Abrir "
                              f"{self.source_url} y buscar '{nombre}'.",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- Canada SEMA-LMES ----------
@register
class CanadaSemaSource:
    name = "Canadá — SEMA / LMES Sanctions"
    source_url = "https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolide.aspx?lang=eng"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            return _list_lookup(self.name, "canada_sema", canada_sema, nombre,
                                "Canadá SEMA/LMES", min_rows=100, t0=t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- World Bank Ineligible ----------
# DEPRECATED 2026-06-13 — duplicado de `WorldBankDebarredSource` en
# sources/worldbank_debarred.py ("Banco Mundial — Debarred Firms &
# Individuals"), que usa el mirror OpenSanctions con cache 24h (datos
# más actuales). Se conserva la clase por si alguien la quiere
# rehabilitar; para reactivar, descomentar la línea `@register` de abajo.
# @register
class WorldbankIneligibleSource:
    name = "Banco Mundial — Firms/Individuals Ineligible"
    source_url = "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            if _mgr.needs_refresh("worldbank_ineligible"):
                _mgr.refresh("worldbank_ineligible", worldbank_ineligible,
                             force=False)
            rows = _mgr.search("worldbank_ineligible", nombre)
            rows = _token_match_rows(nombre, rows)
            return _hit_from_rows(rows, self.name,
                                  f"{len(rows)} coincidencia(s) (World Bank)")
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)
