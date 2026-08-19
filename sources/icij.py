"""
sources/icij.py — ICIJ Offshore Leaks (Panama, Paradise, Pandora, Offshore, Bahamas).

Implementación con Playwright que interactúa con la búsqueda real
del portal offshoreleaks.icij.org y captura screenshot como evidencia.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from urllib.parse import quote
from .base import Hit
from .registry import register


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _icij_search(nombre: str, dataset: str = "offshore-leaks",
                 take_screenshot: bool = True) -> Hit:
    """Búsqueda ICIJ via Playwright. Retorna Hit con detalles y screenshot."""
    t0 = time.time()
    if not nombre:
        return Hit(f"ICIJ — {dataset.title()}", False, "",
                   notice="Requiere nombre.",
                   elapsed_s=time.time()-t0)
    if not _have_browser():
        return Hit(f"ICIJ — {dataset.title()}", False, "",
                   notice="Playwright no instalado. Click 'abrir fuente' "
                          "para buscar manualmente.",
                   elapsed_s=time.time()-t0)

    # Mapeo de dataset
    ds_paths = {
        "panama-papers": "panama-papers",
        "paradise-papers": "paradise-papers",
        "pandora-papers": "pandora-papers",
        "offshore-leaks": "offshore-leaks",
        "bahamas-leaks": "bahamas-leaks",
    }
    ds_path = ds_paths.get(dataset, "offshore-leaks")
    search_url = (f"https://offshoreleaks.icij.org/search?q={quote(nombre)}"
                  f"&dt={ds_path}")

    screenshot_path = None
    try:
        from browsers.pool import get_pool
        with get_pool().page() as page:
            page.set_default_timeout(30000)
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            html = page.content()
            # Verificar 403/404 — un bloqueo NO es "0 coincidencias":
            # se reporta como fuente no disponible (nodisp), nunca nomatch.
            if "403 ERROR" in html or "Request blocked" in html:
                return Hit(f"ICIJ — {ds_path.replace('-', ' ').title()}",
                           False,
                           "NO FUE POSIBLE CONSULTAR: la fuente bloqueó la "
                           "solicitud (CloudFront 403).",
                           status="nodisp", error_type="blocked",
                           notice="ICIJ sitio bloqueado (CloudFront 403). "
                                  "Verificar manualmente en el enlace o "
                                  "reintentar más tarde.",
                           evidence_urls=[search_url],
                           elapsed_s=time.time()-t0)

            # Guardar screenshot
            if take_screenshot:
                try:
                    DATA = Path(__file__).parent.parent / "data"
                    (DATA / "screenshots").mkdir(parents=True, exist_ok=True)
                    safe_name = re.sub(r"[^\w-]", "_", f"icij_{dataset}_{nombre}")[:50]
                    fname = f"{safe_name}_{int(time.time())}.png"
                    screenshot_path = f"screenshots/{fname}"
                    page.screenshot(path=str(DATA / screenshot_path),
                                  full_page=False, timeout=15000)
                except Exception:
                    pass

            # Parsear resultados de ICIJ
            details = []
            # ICIJ muestra tarjetas con <a href="/nodes/...">Entity Name</a>
            for m in re.finditer(
                    r'<a[^>]+href="(/nodes/[^"]+)"[^>]*>([^<]+)</a>',
                    html):
                link, name = m.group(1), m.group(2).strip()
                if nombre.lower().split()[0] in name.lower():
                    details.append({
                        "entidad": name,
                        "url": f"https://offshoreleaks.icij.org{link}",
                    })
                    if len(details) >= 30: break

            # También buscar otros formatos
            for m in re.finditer(
                    r'class="[^"]*result[^"]*"[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>',
                    html, re.S):
                name = m.group(1).strip()
                if nombre.lower().split()[0] in name.lower():
                    if not any(d.get("entidad") == name for d in details):
                        details.append({"entidad": name,
                                        "url": search_url})

            # Texto completo para verificar "no results"
            text = re.sub(r"<[^>]+>", " ", html).lower()
            has_no_results = any(w in text for w in
                                 ["no results", "sin resultados", "0 results",
                                  "0 entidades", "no entities"])
            if details:
                summary, status, review = (
                    f"{len(details)} resultado(s) en {dataset}",
                    "match_probable", False)
            elif has_no_results:
                summary, status, review = (
                    f"0 resultados en {dataset} (mensaje de la fuente "
                    "verificado)", "nomatch_verified", False)
            else:
                # La página cargó pero no pudimos verificar textualmente el
                # resultado: revisión humana, no un "sin registro" limpio.
                summary, status, review = (
                    f"Búsqueda ejecutada en {dataset}, resultado no "
                    "verificable automáticamente.", "manual_review", True)

            hit = Hit(f"ICIJ — {ds_path.replace('-', ' ').title()}",
                      len(details) > 0, summary, details,
                      status=status, requires_manual_review=review,
                      evidence_urls=[search_url,
                                     f"https://offshoreleaks.icij.org/"],
                      download_url=screenshot_path,
                      elapsed_s=time.time()-t0)
            if details:
                hit.confidence = "posible"
                hit.matched_name = details[0].get("entidad", "")
                hit.notes = ("Coincidencia por nombre en base ICIJ: alta "
                             "probabilidad de homónimos; verificar entidad.")
            return hit
    except Exception as e:
        return Hit(f"ICIJ — {ds_path.replace('-', ' ').title()}", False, "",
                   notice=f"Browser error: {type(e).__name__}: {e}. "
                          f"Click 'abrir fuente' para buscar manualmente.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)


# ---------- ICIJ Panama Papers ----------
@register
class IcijPanamaSource:
    name = "ICIJ — Panama Papers"
    source_url = "https://offshoreleaks.icij.org/search?dt=panama-papers"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        return _icij_search(nombre, "panama-papers")


# ---------- ICIJ Paradise Papers ----------
@register
class IcijParadiseSource:
    name = "ICIJ — Paradise Papers"
    source_url = "https://offshoreleaks.icij.org/search?dt=paradise-papers"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        return _icij_search(nombre, "paradise-papers")


# ---------- ICIJ Pandora Papers ----------
@register
class IcijPandoraSource:
    name = "ICIJ — Pandora Papers"
    source_url = "https://offshoreleaks.icij.org/search?dt=pandora-papers"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        return _icij_search(nombre, "pandora-papers")


# ---------- ICIJ Offshore Leaks ----------
@register
class IcijOffshoreSource:
    name = "ICIJ — Offshore Leaks"
    source_url = "https://offshoreleaks.icij.org/search?dt=offshore-leaks"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        return _icij_search(nombre, "offshore-leaks")


# ---------- ICIJ Bahamas Leaks ----------
@register
class IcijBahamasSource:
    name = "ICIJ — Bahamas Leaks"
    source_url = "https://offshoreleaks.icij.org/search?dt=bahamas-leaks"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        return _icij_search(nombre, "bahamas-leaks")
