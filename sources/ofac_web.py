"""
sources/ofac_web.py — OFAC Sanctions List Search (form web oficial).

A diferencia de OfacSdnSource y OfacConsolidatedSource (que leen CSVs/XML
locales cacheados en data/ofac_sdn.csv), esta fuente usa el formulario
web oficial en vivo de OFAC para hacer una busqueda por nombre y devolver
un screenshot del resultado como evidencia.

URL del form: https://sanctionssearch.ofac.treas.gov/

El form es ASP.NET WebForms (sin captcha) con un input "Name:" con
id="ctl00_MainContent_txtLastName" y un boton submit con
id="ctl00_MainContent_btnSearch" (value="Search"). El form hace un
postback sincronico que actualiza el div #ctl00_MainContent_pnlResults
in-place con una tabla de resultados con columnas: Name, Address, Type,
Program(s), List, Score.

Patron de Playwright: 1 sync_playwright por llamada (ver
_browser_helper._run_in_fresh_browser), browser headless, timeout 50s
para navegacion, polling 1.5s para hidratacion.
"""
from __future__ import annotations
import re
import time
from pathlib import Path

from .base import Hit
from .registry import register
from ._browser_helper import _run_in_fresh_browser
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


from sources.base import DATA
SCREENSHOTS = DATA / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)


# Selectores observados en la pagina de OFAC (inspeccion 2026-06).
NAME_INPUT_ID = "ctl00_MainContent_txtLastName"
SEARCH_BTN_ID = "ctl00_MainContent_btnSearch"


def _safe_stamp(nombre: str) -> str:
    """Sanitiza un nombre para usarlo en un filename."""
    return re.sub(r"[^\w-]", "_", nombre)[:40]


def _save_shot(page, nombre: str, tag: str = "") -> str | None:
    """Guarda screenshot en data/screenshots/ y devuelve path relativo
    tipo 'screenshots/ofac_web_<safe>_<tag>_<ts>.png'."""
    safe = _safe_stamp(nombre)
    suffix = f"_{tag}" if tag else ""
    fname = f"ofac_web_{safe}{suffix}_{int(time.time())}.png"
    full = SCREENSHOTS / fname
    try:
        page.screenshot(path=str(full), full_page=False, timeout=15000)
        return f"screenshots/{fname}"
    except Exception as e:
        print(f"  [ofac_web] screenshot fail: {e}", flush=True)
        return None


def _find_name_input(page):
    """Localiza el input 'Name:' en el form de OFAC. Probar en orden:
    1. #ctl00_MainContent_txtLastName   (selector canonico, observado)
    2. input[name='ctl00$MainContent$txtLastName']
    3. input[id*='txtLastName']         (substring)
    4. input[name*='txtLastName']
    5. input[id*='Name']                (fallback generico)
    6. input[name*='Name']
    7. input[type='text'][maxlength='250'] (el campo Name tiene
       maxlength=250 segun el HTML oficial)

    Devuelve (locator, selector_str) o (None, None).
    """
    for sel in [
        f"input#{NAME_INPUT_ID}",
        "input[name='ctl00$MainContent$txtLastName']",
        "input[id*='txtLastName']",
        "input[name*='txtLastName']",
        "input[id*='Name']",
        "input[name*='Name']",
        "input[type='text'][maxlength='250']",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc, sel
        except Exception:
            continue
    return None, None


def _click_search(page) -> bool:
    """Click en el boton 'Search' del form. Devuelve True si hizo
    click (o Enter fallback), False si fallo."""
    for sel in [
        f"input#{SEARCH_BTN_ID}",
        "input[id*='btnSearch']",
        "input[name*='btnSearch']",
        "input[value='Search']",
        "input[type='submit'][value='Search']",
        "button:has-text('Search')",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click(timeout=8000, force=True)
                return True
        except Exception:
            continue
    return False


def _fill_name(page, nombre: str) -> tuple[bool, str | None]:
    """Llena el input Name. Devuelve (ok, error_msg)."""
    inp, sel_used = _find_name_input(page)
    if inp is None:
        return False, "name_input_not_found"
    try:
        inp.click(timeout=5000)
        inp.fill("")
        try:
            inp.type(nombre, delay=20)
        except Exception:
            inp.fill(nombre)
        page.wait_for_timeout(400)
        # Verificar que el valor quedo guardado. ASP.NET WebForms a
        # veces no retiene el valor via fill() por handlers cross-frame.
        verify_ok = False
        try:
            actual = inp.input_value(timeout=2000)
            if actual and nombre in actual:
                verify_ok = True
        except Exception:
            pass
        if not verify_ok:
            # Reintento via JS puro
            try:
                page.evaluate(
                    """(c) => {
                        const el = document.getElementById(
                            'ctl00_MainContent_txtLastName');
                        if (el) {
                            el.focus();
                            el.value = c;
                            el.dispatchEvent(new Event('input', {
                                bubbles: true}));
                            el.dispatchEvent(new Event('change', {
                                bubbles: true}));
                            el.dispatchEvent(new Event('blur', {
                                bubbles: true}));
                        }
                    }""", nombre)
                page.wait_for_timeout(400)
            except Exception as e:
                return False, f"js_fill_fail: {e}"
        return True, None
    except Exception as e:
        return False, f"fill_exception: {e}"


def _parse_results(page) -> dict:
    """Lee el resultado del form OFAC desde la pagina actual.
    Devuelve dict con: count, rows, no_results_text, body_text."""
    try:
        body_text = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        body_text = ""

    no_results_text = any(
        phrase in body_text for phrase in [
            "Your search has not returned any results",
            "no results were found",
            "0 records found",
            "returned no results",
        ]
    )

    rows: list[dict] = []
    try:
        rows_data = page.evaluate(
            """() => {
                const out = [];
                const scroll = document.getElementById('scrollResults');
                if (!scroll) return out;
                const tables = scroll.getElementsByTagName('table');
                for (const t of tables) {
                    const trs = t.getElementsByTagName('tr');
                    for (const tr of trs) {
                        const tds = tr.getElementsByTagName('td');
                        if (tds.length >= 4) {
                            const cells = [];
                            for (const td of tds) {
                                cells.push((td.innerText || '').trim());
                            }
                            out.push(cells);
                        }
                    }
                }
                return out;
            }"""
        )
        if isinstance(rows_data, list):
            for cells in rows_data:
                if not cells:
                    continue
                # OFAC tiene 6 columnas: Name, Address, Type, Program(s),
                # List, Score. Algunos templates tienen más (links de
                # detalle) — guardamos posiciones extra como col_N.
                labels = ["name", "address", "type", "programs",
                          "list", "score"]
                row = {}
                for i, c in enumerate(cells):
                    if i < len(labels):
                        row[labels[i]] = c
                    else:
                        row[f"col_{i}"] = c
                if any(v for v in row.values() if v):
                    rows.append(row)
    except Exception:
        pass

    return {
        "count": len(rows),
        "rows": rows,
        "no_results_text": no_results_text,
        "body_text": body_text[:4000],
    }


def _do_full_search(page, nombre: str) -> dict:
    """Hace TODO en una sola sesion de browser: goto, fill, click,
    wait, parse, screenshot. Devuelve dict con todos los datos."""
    page.set_default_timeout(30000)

    # 1) goto
    try:
        page.goto("https://sanctionssearch.ofac.treas.gov/",
                  wait_until="domcontentloaded", timeout=50000)
    except Exception as e:
        shot = _save_shot(page, nombre, tag="goto_fail")
        return {"status": "goto_fail", "error": str(e),
                "shot_path": shot}

    # 2) Esperar 2-3s para que hidrate (ASP.NET WebForms)
    page.wait_for_timeout(2500)

    # 3) Localizar y llenar el input Name
    inp, sel_used = _find_name_input(page)
    if inp is None:
        shot = _save_shot(page, nombre, tag="no_name_input")
        return {"status": "no_name_input",
                "error": "name_input_not_found",
                "shot_path": shot,
                "selector_used": None}

    ok, fill_err = _fill_name(page, nombre)
    if not ok:
        shot = _save_shot(page, nombre, tag="fill_fail")
        return {"status": "fill_fail", "error": fill_err,
                "shot_path": shot,
                "selector_used": sel_used}

    # 4) Click "Search"
    clicked = _click_search(page)
    if not clicked:
        # Fallback: Enter sobre el input
        try:
            inp.press("Enter")
            clicked = True
        except Exception:
            shot = _save_shot(page, nombre, tag="no_search_btn")
            return {"status": "no_search_btn",
                    "error": "search_btn_not_clickable",
                    "shot_path": shot}

    # 5) Esperar 5-8s para que la pagina actualice con resultados
    # (postback sincronico de WebForms). Polling 1.5s x 7 = 10.5s.
    for _ in range(7):
        page.wait_for_timeout(1500)
    # Tiempo adicional para que el async postback termine
    page.wait_for_timeout(2500)

    # 6) Parsear resultados de la misma pagina
    parsed = _parse_results(page)

    # 7) Tomar screenshot del resultado final
    shot_path = _save_shot(page, nombre, tag="result")

    return {
        "status": "ok",
        "shot_path": shot_path,
        "clicked": clicked,
        "selector_used": sel_used,
        "parsed": parsed,
    }


# DEPRECATED 2026-06-13 — este "form web oficial" de OFAC Sanctions
# List Search es un duplicado de las dos fuentes OFAC en
# sources/internacionales.py: `OfacConsolidatedSource`
# ("OFAC — Lista Consolidada (Non-SDN, FSE, SSI, CAPTA)") +
# `OfacAddrsSource` ("OFAC — Direcciones y aliases (add.csv)"), que
# parsean los archivos bulk oficiales (XML + CSV) con cache. Consumir
# Playwright contra el form no aporta datos adicionales.
# Se conserva la clase por si alguien la quiere rehabilitar; para
# reactivar, descomentar la línea `@register` de abajo.
# @register
class OfacSanctionsSearchSource:
    name = "OFAC — Sanctions List Search (form web oficial)"
    source_url = "https://sanctionssearch.ofac.treas.gov/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(
                source=self.name, matched=False, summary="",
                notice="OFAC web requiere un nombre para buscar.",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        # Verificar Playwright disponible
        try:
            from playwright.sync_api import sync_playwright  # noqa
        except ImportError:
            return Hit(
                source=self.name, matched=False, summary="",
                notice="Playwright no instalado. Click 'abrir fuente' "
                       "para buscar manualmente.",
                captcha_required=True,
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        # Ejecutar toda la busqueda en un browser NUEVO (thread-safe
        # gracias a _run_in_fresh_browser).
        result_holder: dict = {}

        def _runner(page):
            result_holder.update(_do_full_search(page, nombre))

        try:
            _run_in_fresh_browser(_runner)
        except Exception as e:
            return Hit(
                source=self.name, matched=False, summary="",
                error=f"{type(e).__name__}: {e}",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        status = result_holder.get("status", "unknown")
        if status != "ok":
            err = result_holder.get("error", "unknown")
            shot = result_holder.get("shot_path")
            details = [{"screenshot": shot}] if shot else []
            return Hit(
                source=self.name, matched=False, summary="",
                error=f"OfacWeb{status.title()}: {err}",
                evidence_urls=[self.source_url],
                download_url=shot,
                details=details,
                elapsed_s=time.time() - t0,
            )

        parsed = result_holder.get("parsed", {})
        count = parsed.get("count", 0)
        rows = parsed.get("rows", [])
        no_results = parsed.get("no_results_text", False)
        shot = result_holder.get("shot_path")

        # Construir details: primero el screenshot, luego las filas
        details: list[dict] = []
        if shot:
            details.append({"screenshot": shot})
        details.extend(rows[:30])

        if count > 0:
            return Hit(
                source=self.name, matched=True,
                summary=f"{count} resultados en OFAC SDN",
                details=details,
                evidence_urls=[self.source_url],
                download_url=shot,
                elapsed_s=time.time() - t0,
            )

        # 0 resultados
        if no_results:
            summary = "0 resultados en OFAC SDN"
        else:
            # Fallo: no se detecto texto "no results" Y no hay filas.
            # Probablemente timeout del sitio o postback fallo.
            summary = ("0 resultados en OFAC SDN (postback sin texto "
                       "'no results' ni filas visibles)")
        return Hit(
            source=self.name, matched=False,
            summary=summary,
            details=details,
            evidence_urls=[self.source_url],
            download_url=shot,
            elapsed_s=time.time() - t0,
        )
