"""
sources/wanted.py — Listas de fugitivos y más buscados.

Implementaciones reales con búsqueda local en listas estáticas cuando
es posible, o browser/HTML scraping cuando es necesario.
"""
from __future__ import annotations
import re
import time
from urllib.parse import quote
from .base import Hit
from .registry import register
from .local_lists import normalize, tokenize
from lists import LocalListManager
from lists.downloaders import fbi_wanted, interpol_red, nca_uk_most_wanted


_mgr = LocalListManager()


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


# ---------- FBI Most Wanted ----------
@register
class FbiWantedSource:
    name = "FBI — Most Wanted"
    source_url = "https://www.fbi.gov/wanted"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            if _mgr.needs_refresh("fbi_wanted"):
                _mgr.refresh("fbi_wanted", fbi_wanted, force=False)
            rows = _mgr.search("fbi_wanted", nombre)
            rows = _token_match_rows(nombre, rows)
            if rows:
                return Hit(self.name, True,
                           f"{len(rows)} coincidencia(s) en FBI Wanted",
                           [{k: v for k, v in r.items()
                             if v not in (None, "", "N/A")} for r in rows[:20]])
            return Hit(self.name, False, "0 coincidencias en FBI Wanted",
                       elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- INTERPOL Red Notices ----------
# DEPRECATED 2026-06-13 — esta versión solo devuelve un notice
# ("API bloqueada — abrir búsqueda manual"). El duplicado real es
# `InterpolRedBrowserSource` en sources/browser_sources.py
# ("INTERPOL — Red Notices (browser)"), que sí automatiza la búsqueda
# en el portal público. Se conserva la clase por si alguien la quiere
# rehabilitar; para reactivar, descomentar la línea `@register` de abajo.
# @register
class InterpolRedSource:
    name = "INTERPOL — Red Notices"
    source_url = "https://www.interpol.int/How-we-work/Notices/Red-Notices"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       evidence_urls=["https://www.interpol.int/How-we-work/"
                                     "Notices/Red-Notices/View-Red-Notices"],
                       elapsed_s=time.time()-t0)
        search_url = ("https://www.interpol.int/en/How-we-work/Notices/"
                      "Red-Notices/View-Red-Notices")
        return Hit(self.name, False,
                   f"INTERPOL API bloqueada — abrir búsqueda manual de '{nombre}'",
                   notice="La API de INTERPOL no es pública. "
                          "Click 'abrir fuente' para buscar manualmente.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)


# ---------- DEA Most Wanted ----------
# DEPRECATED 2026-06-13 — este stub solo devuelve un notice
# ("portal JS-driven"). El duplicado real es `DeaFugitivesSource` en
# sources/dea_fugitives.py ("DEA — Most Wanted Fugitives (detallado)"),
# que scrapea el portal paginado con cache 7d y fallback a Wayback si
# Akamai bloquea. Se conserva la clase por si alguien la quiere
# rehabilitar; para reactivar, descomentar la línea `@register` de abajo.
# @register
class DeaWantedSource:
    name = "DEA — Most Wanted Fugitives"
    source_url = "https://www.dea.gov/fugitives/all"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en DEA fugitives",
                   status="not_implemented",
                   notice="Portal DEA es JS-driven. Click 'abrir fuente' para buscar.",
                   evidence_urls=[f"https://www.dea.gov/fugitives/all?q={quote(nombre)}"],
                   elapsed_s=time.time()-t0)


# ---------- ICE Most Wanted ----------
@register
class IceWantedSource:
    name = "ICE — Most Wanted"
    source_url = "https://www.ice.gov/most-wanted"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        search_url = f"https://www.ice.gov/most-wanted?q={quote(nombre or '')}"
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en ICE Most Wanted",
                   status="not_implemented",
                   notice="Para integrar ICE automáticamente se requiere Playwright "
                          "(Fase 5). Por ahora abrir manualmente.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)


# ---------- DSS Most Wanted ----------
@register
class DssWantedSource:
    name = "DSS — Diplomatic Security Most Wanted"
    source_url = "https://www.state.gov/diplomatic-security/dss-wanted"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en DSS Wanted",
                   status="not_implemented",
                   notice="Portal DSS es HTML. Click 'abrir fuente' para buscar.",
                   evidence_urls=[f"https://www.state.gov/diplomatic-security/dss-wanted?q={quote(nombre)}"],
                   elapsed_s=time.time()-t0)


# ---------- CBI Most Wanted (India) ----------
@register
class CbiWantedSource:
    name = "CBI — Most Wanted (India)"
    source_url = "https://cbi.gov.in/most-wanted"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en CBI Most Wanted",
                   status="not_implemented",
                   notice="Sitio CBI no accesible desde este entorno. "
                          "Click 'abrir fuente' para verificar.",
                   evidence_urls=["https://cbi.gov.in/most-wanted"],
                   elapsed_s=time.time()-t0)


# ---------- EUROPOL Most Wanted ----------
# DEPRECATED 2026-06-13 — este stub solo devuelve un notice
# ("portal JS-driven"). El duplicado real es `EuropolMostWantedSource`
# en sources/europol.py ("EUROPOL — Most Wanted Fugitives"), que sí
# parsea el HTML de eumostwanted.eu con screenshot de evidencia.
# Se conserva la clase por si alguien la quiere rehabilitar; para
# reactivar, descomentar la línea `@register` de abajo.
# @register
class EuropolWantedSource:
    name = "EUROPOL — Most Wanted"
    source_url = "https://eumostwanted.eu"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en EUROPOL Most Wanted",
                   status="not_implemented",
                   notice="Portal EUROPOL usa búsqueda interactiva (JS). "
                          "Click 'abrir fuente' para buscar.",
                   evidence_urls=[f"https://eumostwanted.eu/#?search={quote(nombre)}"],
                   elapsed_s=time.time()-t0)


# ---------- UK NCA Most Wanted (lista estática de 23) ----------
@register
class UkNcaWantedSource:
    name = "UK NCA — Most Wanted"
    source_url = "https://www.nationalcrimeagency.gov.uk/most-wanted"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        try:
            # Recargar lista si no está
            if _mgr.needs_refresh("nca_uk_most_wanted"):
                _mgr.refresh("nca_uk_most_wanted", nca_uk_most_wanted, force=False)
            rows = _mgr.search("nca_uk_most_wanted", nombre)
            rows = _token_match_rows(nombre, rows)
            if rows:
                details = [{"nombre": r.get("name"),
                            "url": r.get("url"),
                            "lista": "UK NCA Most Wanted"}
                           for r in rows]
                return Hit(self.name, True,
                           f"{len(rows)} coincidencia(s) en UK NCA Most Wanted",
                           details,
                           evidence_urls=[r.get("url") for r in rows
                                          if r.get("url")],
                           elapsed_s=time.time()-t0)
            return Hit(self.name, False, "0 coincidencias en UK NCA Most Wanted",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- Guardia Civil Española — Terroristas ----------
# DEDUP 2026-07-02: DESREGISTRADA. La fuente canónica es
# `browser_sources.py::GuardiaCivilBrowserSource` (búsqueda real).
# Este stub solo devolvía un link manual y duplicaba la fuente en el PDF.
# @register
class GuardiaCivilTerroristasSource:
    name = "Guardia Civil Española — Lista de Terroristas"
    source_url = "https://web.guardiacivil.es/es/colaboracion/Buscados/buscados/"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        search_url = f"https://web.guardiacivil.es/es/colaboracion/Buscados/buscados/?search={quote(nombre)}"
        return Hit(self.name, False,
                   f"Búsqueda: '{nombre}' en Guardia Civil",
                   notice="Portal Guardia Civil usa JS. Para búsqueda automática "
                          "usar Playwright (Fase 5). Click 'abrir fuente'.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)


# ---------- EPA Fugitives ----------
@register
class EpaFugitivesSource:
    name = "EPA — Fugitives (Environmental Protection Agency)"
    source_url = "https://www.epa.gov/enforcement/fugitives"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en EPA Fugitives",
                   status="not_implemented",
                   notice="Portal EPA. Click 'abrir fuente' para buscar.",
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)


# ---------- Policía Colombia — Fugitivos ----------
@register
class ColombiaFugitivosSource:
    name = "Policía Colombia — Fugitivos y Más Buscados"
    source_url = "https://www.policia.gov.co/contenido/mas-buscados"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Policía Colombia",
                   status="not_implemented",
                   notice="Portal Policía Colombia. Click 'abrir fuente' para buscar.",
                   evidence_urls=[
                       "https://www.policia.gov.co/contenido/mas-buscados",
                       "https://www.policia.gov.co/contenido/fugitivos"],
                   elapsed_s=time.time()-t0)
