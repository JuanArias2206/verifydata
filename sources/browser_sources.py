"""
sources/browser_sources.py — Fuentes con navegador (Playwright).

Implementación REAL con búsqueda y screenshot como evidencia.
Cada fuente usa selectores específicos del portal y timeouts
generosos para JS-driven sites.

Notas 2026-06-13 (Fase 5 validation):
- SIGEP: el form está en un iframe cross-origin; navega directamente
  a `https://www.funcionpublica.gov.co/dafpIndexerBHV/hvSigep/index`
  para evitar el Liferay outer-page. La página muestra
  "La búsqueda devuelve N resultados" + tabla con nombres.
- INTERPOL Red Notices: el filtro de búsqueda tiene inputs Family
  name + Forename. El resultado es "Search results: N" + cards
  `a[href^="#20..."]`. Para 1192722347 (sin nombre) se busca solo
  por el input Family name con la cadena vacía o el nombre.
- BIS DPL: la URL `bis.doc.gov/...denied-persons-list` redirige a
  `https://www.bis.gov/` (página de inicio, no la tabla). La
  implementación real está en `sources/bis_dpl.py::BisDeniedPersonsSource`
  (tabla "Full list" parseada con cache). Marcamos la versión browser
  como `captcha_required=True` con notice honesto.
- PTE: el botón real para filtrar es "Filtro" (no "Buscar Nuevamente",
  que recarga la lista completa). "Filtro" muestra un alert JS
  "No existen registros con este criterio.!" si no hay resultados.
- Guardia Civil: el listado carga client-side (SagaListado dinámico)
  con un filtro client-side que NO responde a input/teclado de
  Playwright de forma estable. Se filtra en memoria comparando tokens
  del query contra los nombres visibles en el DOM.

Convención: cada fuente devuelve un `Hit` con `matched=True|False`
o un `Hit(captcha_required=True, notice=...)` con texto específico
del portal (no placeholder genérico "Búsqueda ejecutada (revisar
screenshot)").
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from urllib.parse import quote
from .base import Hit
from .registry import register


from sources.base import DATA
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _shot_path(source: str, query: str) -> str:
    """Genera path de screenshot para evidencia."""
    safe_q = re.sub(r"[^\w-]", "_", query)[:30] or "empty"
    safe_s = re.sub(r"[^\w-]", "_", source)[:30]
    return f"screenshots/{safe_s}_{safe_q}_{int(time.time())}.png"


def _full_shot_path(rel: str) -> Path:
    return DATA / rel


def _save_shot(page, source: str, query: str) -> str | None:
    """Toma screenshot. Devuelve path relativo o None."""
    try:
        rel = _shot_path(source, query)
        page.screenshot(path=str(_full_shot_path(rel)), full_page=False, timeout=15000)
        return rel
    except Exception:
        return None


def _normalize_accents(s: str) -> str:
    """Quita acentos para matching más tolerante."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# =========================================================
# SIGEP — Función Pública Colombia
# URL directa del iframe: https://www.funcionpublica.gov.co/dafpIndexerBHV/hvSigep/index
# Form: input#query[name=query] (placeholder='') + button "Buscar"
# Resultado: "La búsqueda devuelve N resultados" + <table> con filas
# (Nombre + Icono + Entidad + Celular / Ciudad)
# =========================================================
@register
class SigepBrowserSource:
    name = "SIGEP — Función Pública Colombia (browser)"
    source_url = "https://www.funcionpublica.gov.co/web/sigep2/directorio"
    # URL interna del iframe (Liferay outer page wraps this in an iframe).
    # Navegamos directamente a esta para evitar el cross-origin frame.
    _iframe_url = "https://www.funcionpublica.gov.co/dafpIndexerBHV/hvSigep/index"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula.",
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       elapsed_s=time.time()-t0)
        try:
            from browsers.pool import get_pool
            # Preferir cédula si la dan; el SIGEP indexa por cédula
            # además de nombre. Si no, usar el nombre completo.
            query = (cedula or nombre or "").strip()
            if not query:
                return Hit(self.name, False, "",
                           notice="Requiere nombre o cédula.",
                           elapsed_s=time.time()-t0)
            with get_pool().page() as page:
                page.set_default_timeout(30000)
                # Navegar al iframe interno directamente. Esto evita
                # tener que usar `frame_locator` cross-origin y hereda
                # todos los selectores.
                page.goto(self._iframe_url, wait_until="domcontentloaded",
                          timeout=30000)
                # Polling del input (selector verificado 2026-06):
                # `input#query` (name=query, type=text)
                inp = None
                for sel in ["input#query", "input[name='query']",
                            "form#busquedaSigepForm input[type='text']",
                            "form#busquedaSigepForm input"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=2500):
                            inp = loc
                            break
                    except Exception:
                        continue
                if not inp:
                    shot = _save_shot(page, "sigep", query)
                    return Hit(self.name, False,
                               "No se encontró input de búsqueda en SIGEP.",
                               error="SigepNoQueryInput: input#query no apareció tras 8s",
                               evidence_urls=[self._iframe_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                inp.fill(query)
                # Botón "Buscar" verificado 2026-06: button[type=submit]
                # dentro de #busquedaSigepForm. Selector tolerante:
                clicked = False
                for sel in [
                    "form#busquedaSigepForm button",
                    "form#busquedaSigepForm button[type='submit']",
                    "button:has-text('Buscar')",
                ]:
                    try:
                        page.locator(sel).first.click(timeout=2500)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    inp.press("Enter")
                # Esperar resultados: "La búsqueda devuelve N resultados"
                # o el header "Resultados". Polling (NO wait_for_timeout
                # ciego) — la página refresca en ~1.5s.
                result_text = ""
                for _ in range(12):  # 12 × 0.7s = 8.4s
                    page.wait_for_timeout(700)
                    try:
                        body_text = page.locator("body").inner_text(timeout=2000)
                    except Exception:
                        body_text = ""
                    if "La búsqueda devuelve" in body_text or \
                       re.search(r"Resultados\b", body_text):
                        result_text = body_text
                        break
                shot = _save_shot(page, "sigep", query)
                if not result_text:
                    return Hit(self.name, False,
                               "SIGEP: no se detectó el panel de resultados tras 8s.",
                               error="SigepNoResultPanel: 'La búsqueda devuelve' no apareció",
                               evidence_urls=[self._iframe_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                # Parsear el contador canónico
                m = re.search(r"La\s+búsqueda\s+devuelve\s+(\d+)\s+resultados?",
                              result_text, re.I)
                if not m:
                    return Hit(self.name, False,
                               "SIGEP: resultados sin contador canónico.",
                               error="SigepParse: no match para 'La búsqueda devuelve N'",
                               evidence_urls=[self._iframe_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                n_results = int(m.group(1))
                # Extraer filas: cada fila tiene un link a /dafpIndexerBHV/hvSigep/detallarHV/...
                # y contiene el nombre + entidad. Usar el DOM (no innerText) para no perder
                # estructura. Leemos los enlaces `a[href*="detallarHV/"]` que son los perfiles.
                detail_links = page.evaluate("""
                    () => {
                        const out = [];
                        document.querySelectorAll("a[href*='detallarHV/']").forEach(a => {
                            const tr = a.closest("tr");
                            const tds = tr ? Array.from(tr.querySelectorAll("td")).map(c => c.innerText.trim()) : [];
                            out.push({
                                href: a.getAttribute("href"),
                                text: a.innerText.trim().slice(0, 200),
                                row: tds.join(" | ").slice(0, 300),
                            });
                        });
                        return out;
                    }
                """)
                details = []
                for d in (detail_links or [])[:20]:
                    details.append({
                        "nombre_perfil": d.get("text", ""),
                        "url_perfil": d.get("href", ""),
                        "fila_tabla": d.get("row", ""),
                    })
                matched = n_results > 0
                if matched:
                    summary = (f"{n_results} resultado(s) en SIGEP "
                               f"(directorio de servidores públicos)")
                else:
                    summary = ("0 resultados en SIGEP — confirmado por el "
                               "portal: 'La búsqueda devuelve 0 resultados'")
                return Hit(self.name, matched, summary, details,
                           evidence_urls=[self._iframe_url],
                           download_url=shot,
                           elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"SIGEP error: {type(e).__name__}: {e}. "
                              f"Click 'abrir fuente' para buscar manualmente.",
                       evidence_urls=[self._iframe_url],
                       elapsed_s=time.time()-t0)


# =========================================================
# INTERPOL — Red Notices
# URL: https://www.interpol.int/en/How-we-work/Notices/Red-Notices/View-Red-Notices
# Filtros: input "Family name", input "Forename", dropdowns, button "Search"
# Resultado: "Search results: N" + cards a[href^="#20..."] con
#   text "<FAMILY>\n<GIVEN_NAMES>" + párrafo "<age> years old" + país
#
# DEDUP 2026-07-02: DESREGISTRADA. El WAF de INTERPOL bloquea el navegador
# ("ACCESS WAS DENIED FOR SECURITY REASONS"). La fuente canónica es
# `opensanctions_lists.py::InterpolRedNoticesSource`, que consulta el
# mirror diario de Red Notices (6.420 registros) sin browser ni WAF.
# =========================================================
# @register
class InterpolRedBrowserSource:
    name = "INTERPOL — Red Notices (browser)"
    source_url = "https://www.interpol.int/en/How-we-work/Notices/Red-Notices/View-Red-Notices"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       elapsed_s=time.time()-t0)
        try:
            from browsers.pool import get_pool
            with get_pool().page() as page:
                page.set_default_timeout(30000)
                page.goto(self.source_url, wait_until="domcontentloaded",
                          timeout=30000)
                # Detección temprana de WAF block ("ACCESS TO THIS SITE
                # WAS DENIED FOR SECURITY REASONS" — INTERPOL bloquea
                # headless Chromium por fingerprint). Si aparece, devolver
                # honest notice sin seguir esperando.
                try:
                    body_after_goto = page.locator("body").inner_text(
                        timeout=3000)
                except Exception:
                    body_after_goto = ""
                if re.search(
                        r"ACCESS TO THIS SITE WAS DENIED|WAS DENIED FOR "
                        r"SECURITY REASONS|denegado por razones de "
                        r"seguridad", body_after_goto, re.I):
                    shot = _save_shot(page, "interpol", nombre)
                    return Hit(self.name, False,
                               "INTERPOL bloqueó la solicitud (WAF: "
                               "'Access denied for security reasons').",
                               notice=("El WAF de INTERPOL detecta el "
                                       "fingerprint de Playwright headless "
                                       "y bloquea la página antes de "
                                       "hidratar el formulario. No es un "
                                       "captcha visual — es fingerprinting. "
                                       "Verificar manualmente en "
                                       f"{self.source_url}"),
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                # Aceptar cookies si aparece (banner puede no estar)
                for txt in ["Accept", "Aceptar", "OK", "Agree", "Acepto",
                            "Accept All", "Accepter"]:
                    try:
                        page.locator(f"button:has-text('{txt}')").first.click(
                            timeout=1500)
                        break
                    except Exception:
                        continue
                # Dividir nombre en family (apellido, último token) y
                # forename (resto). INTERPOL indexa por apellido.
                tokens = [t for t in re.split(r"\s+", nombre.strip()) if t]
                family_name = tokens[-1] if tokens else ""
                forename = " ".join(tokens[:-1]) if len(tokens) > 1 else ""
                # Polling de los inputs (selector verificado 2026-06):
                family_input = None
                for sel in [
                    "input[aria-label='Family name']",
                    "input#name",
                    "input[name='name']",
                    "input[placeholder*='Family' i]",
                ]:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=2500):
                            family_input = loc
                            break
                    except Exception:
                        continue
                if not family_input:
                    shot = _save_shot(page, "interpol", nombre)
                    return Hit(self.name, False,
                               "INTERPOL: input 'Family name' no apareció.",
                               error="InterpolNoNameInput: 'Family name' input no visible tras 10s",
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                family_input.fill(family_name)
                if forename:
                    # Forename input está cerca del de Family name
                    for sel in [
                        "input[aria-label='Forename']",
                        "input#forename",
                        "input[name='forename']",
                        "input[placeholder*='Forename' i]",
                    ]:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible(timeout=1500):
                                loc.fill(forename)
                                break
                        except Exception:
                            continue
                # Click "Search" (button[type=submit])
                clicked = False
                for sel in [
                    "button:has-text('Search')",
                    "form button[type='submit']",
                    "button[type='submit']",
                ]:
                    try:
                        page.locator(sel).first.click(timeout=2500)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    family_input.press("Enter")
                # Esperar panel de resultados: "Search results: N"
                for _ in range(14):  # 14 × 0.7s = ~10s
                    page.wait_for_timeout(700)
                    try:
                        body_text = page.locator("body").inner_text(timeout=2000)
                    except Exception:
                        body_text = ""
                    if re.search(r"Search results\s*:\s*\d+", body_text, re.I):
                        break
                shot = _save_shot(page, "interpol", nombre)
                # Parsear "Search results: N"
                body_text = page.locator("body").inner_text(timeout=2000)
                m = re.search(r"Search results\s*:\s*(\d+)", body_text, re.I)
                if not m:
                    return Hit(self.name, False,
                               "INTERPOL: no se detectó 'Search results: N' tras 10s.",
                               error="InterpolNoResultsText: 'Search results' no apareció",
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                n_results = int(m.group(1))
                # Extraer las cards de Red Notices. Cada card es un
                # `<a href="#20XX-NNNNN">` con texto "FAMILY\nGIVEN NAMES".
                notices = page.evaluate("""
                    () => {
                        const out = [];
                        document.querySelectorAll("a[href^='#20']").forEach(a => {
                            const text = a.innerText.trim();
                            const href = a.getAttribute("href") || "";
                            // Buscar edad/país en el contenedor padre (la card)
                            const card = a.closest("div, article, li") || a.parentElement;
                            const para = card ? card.querySelector("p") : null;
                            const paraText = para ? para.innerText.trim() : "";
                            out.push({href, text, paraText});
                        });
                        return out;
                    }
                """)
                details = []
                for d in (notices or [])[:20]:
                    # El text viene como "FAMILY\nGIVEN" — normalizar
                    nm = re.sub(r"\s+", " ", d.get("text", "")).strip()
                    details.append({
                        "nombre_red_notice": nm,
                        "id_red_notice": d.get("href", "").lstrip("#"),
                        "detalle": d.get("paraText", "")[:200],
                    })
                matched = n_results > 0
                if matched:
                    summary = (f"{n_results} Red Notice(s) en INTERPOL para "
                               f"apellido '{family_name}'")
                else:
                    summary = (f"0 resultados en INTERPOL Red Notices para "
                               f"apellido '{family_name}'")
                return Hit(self.name, matched, summary, details,
                           evidence_urls=[self.source_url],
                           download_url=shot,
                           elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"INTERPOL error: {type(e).__name__}: {e}.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)


# =========================================================
# BIS — Denied Persons List
# NOTA 2026-06-13: la URL histórica `https://www.bis.doc.gov/...`
# redirige a `https://www.bis.gov/` (homepage, no la tabla). El
# sitio nuevo de BIS aloja la lista en
# `https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl`
# que YA está cubierta por `sources/bis_dpl.py::BisDeniedPersonsSource`
# (parseo con cache, sin browser).
# DEDUP 2026-07-02: DESREGISTRADA. La fuente canónica es
# `bis_dpl.py::BisDeniedPersonsSource` (bulk + cache SQLite + retry).
# Mantener dos entradas inflaba el conteo y duplicaba BIS en el PDF.
# =========================================================
# @register
class BisDplBrowserSource:
    name = "BIS — Denied Persons List (browser)"
    source_url = "https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        # Esta fuente browser está OBSOLETA (URL histórica redirige a
        # homepage). La implementación real es `bis_dpl.py::BisDeniedPersonsSource`
        # (parseo HTML directo con cache). Devolvemos captcha_required=True
        # con notice específico para que el caller la distinga de un fallo
        # genérico y prefiera la versión de bis_dpl.py.
        return Hit(
            self.name, False,
            summary="BIS browser obsoleto: ver implementación en bis_dpl.py",
            notice=("La URL histórica (bis.doc.gov/...denied-persons-list) "
                    "redirige a https://www.bis.gov/ (homepage, no la tabla "
                    "DPL). La implementación funcional está en "
                    "`sources/bis_dpl.py::BisDeniedPersonsSource` (parseo "
                    "HTML directo con cache, ~565 filas). Esta fuente "
                    "browser será eliminada en dedup contra bis_dpl.py."),
            captcha_required=True,
            evidence_urls=[self.source_url,
                           "https://www.bis.gov/licensing/end-user-guidance/denied-persons-list-dpl"],
            elapsed_s=time.time()-t0,
        )


# =========================================================
# PTE — Grandes Contratistas del Estado
# URL: https://www.pte.gov.co/contratos/los-100-contratos-mas-grandes-de-la-vigencia-actual
# El contenido está en iframe cross-origin:
#   https://pte-prueba.azurewebsites.net/ContratosMasGrandesVigenciaActual
# Form: input "Filtrar información por :" + button "Filtro" + button "Buscar Nuevamente"
# Comportamiento verificado 2026-06:
#   - "Filtro" filtra la tabla; si no hay resultados, muestra un alert
#     JS con texto "No existen registros con este criterio.!"
#   - "Buscar Nuevamente" recarga la lista completa (ignora el filtro)
# Tabla: 8 columnas — Fecha Registro, Sector, Entidad, SubUnidad,
#   Beneficiario, Valor Contratos, Valor Pagado, % Pagado
# =========================================================
@register
class PteBrowserSource:
    name = "PTE — Grandes Contratistas del Estado (browser)"
    source_url = "https://www.pte.gov.co/contratos/los-100-contratos-mas-grandes-de-la-vigencia-actual"
    _iframe_url = "https://pte-prueba.azurewebsites.net/ContratosMasGrandesVigenciaActual"
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       elapsed_s=time.time()-t0)
        try:
            from browsers.pool import get_pool
            with get_pool().page() as page:
                page.set_default_timeout(20000)
                # Navegar al iframe directamente (cross-origin safe)
                page.goto(self._iframe_url, wait_until="domcontentloaded",
                          timeout=20000)
                # Hook para capturar el alert JS "No existen registros..."
                alert_seen = {"text": None}
                def _on_dialog(dialog):
                    try:
                        alert_seen["text"] = dialog.message
                        dialog.accept()
                    except Exception:
                        try: dialog.dismiss()
                        except Exception: pass
                page.on("dialog", _on_dialog)
                # Polling del input de filtro
                inp = None
                for sel in [
                    "input[id*='buscarTexto']",
                    "input[placeholder*='iltrar' i]",
                    "input[type='text']",
                ]:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=2500):
                            inp = loc
                            break
                    except Exception:
                        continue
                if not inp:
                    shot = _save_shot(page, "pte", nombre)
                    return Hit(self.name, False,
                               "PTE: input de filtro no apareció.",
                               error="PteNoFilterInput: input de filtro no visible tras 8s",
                               evidence_urls=[self._iframe_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                inp.fill(nombre)
                # Botón "Filtro" (NO "Buscar Nuevamente" — ese recarga la lista)
                clicked = False
                for sel in [
                    "button:has-text('Filtro')",
                    "input[type='submit'][value='Filtro']",
                ]:
                    try:
                        page.locator(sel).first.click(timeout=2500)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    inp.press("Enter")
                # Esperar: alert "No existen registros" o tabla con 0 filas,
                # o la tabla con resultados filtrados. Polling 8s.
                page.wait_for_timeout(2000)  # El alert se procesa síncronamente
                # Re-evaluar la tabla después del click
                page.wait_for_timeout(1500)
                shot = _save_shot(page, "pte", nombre)
                # Si el alert "No existen registros" apareció, eso ES
                # la respuesta canónica del sistema: 0 resultados.
                if alert_seen["text"] and \
                   re.search(r"No\s+existen\s+registros", alert_seen["text"], re.I):
                    return Hit(self.name, False,
                               "0 resultados en PTE — confirmado por el sistema: "
                               f"'{alert_seen['text']}'",
                               details=[{
                                   "mensaje_oficial": alert_seen["text"],
                                   "query": nombre,
                               }],
                               evidence_urls=[self._iframe_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                # Si no hubo alert, parsear la tabla
                rows = page.evaluate("""
                    () => {
                        const out = [];
                        // Saltar thead, tomar todas las filas del tbody
                        const trs = document.querySelectorAll("table tbody tr, table tr");
                        trs.forEach(tr => {
                            const tds = Array.from(tr.querySelectorAll("td")).map(c => c.innerText.trim());
                            if (tds.length < 5) return;
                            out.push(tds);
                        });
                        return out;
                    }
                """)
                # Filtrar solo filas con >= 5 celdas (ignorar header)
                data_rows = [r for r in (rows or []) if len(r) >= 5]
                # El "Filtro" sin alert significa que SÍ hay resultados.
                details = []
                for r in data_rows[:20]:
                    # Las columnas (verificadas 2026-06) son:
                    # 0:Fecha, 1:Sector, 2:Entidad, 3:SubUnidad, 4:Beneficiario, 5:Valor, 6:Pagado, 7:%
                    details.append({
                        "fecha_registro": r[0] if len(r) > 0 else "",
                        "sector": r[1] if len(r) > 1 else "",
                        "entidad": r[2] if len(r) > 2 else "",
                        "subunidad": r[3] if len(r) > 3 else "",
                        "beneficiario": r[4] if len(r) > 4 else "",
                        "valor_contrato": r[5] if len(r) > 5 else "",
                        "valor_pagado": r[6] if len(r) > 6 else "",
                        "porcentaje_pagado": r[7] if len(r) > 7 else "",
                    })
                matched = len(details) > 0
                if matched:
                    summary = (f"{len(details)} resultado(s) en PTE "
                               f"(grandes contratistas del Estado)")
                else:
                    # Sin alert y sin filas: la tabla se refrescó pero está vacía
                    # (comportamiento raro, pero posible si "Filtro" dejó 0)
                    summary = ("0 resultados en PTE — tabla vacía tras "
                               "filtrar (ver screenshot)")
                return Hit(self.name, matched, summary, details,
                           evidence_urls=[self._iframe_url],
                           download_url=shot,
                           elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"PTE error: {type(e).__name__}: {e}.",
                       evidence_urls=[self._iframe_url],
                       elapsed_s=time.time()-t0)


# =========================================================
# Guardia Civil Española — Buscados
# URL: https://web.guardiacivil.es/es/colaboracion/Buscados/buscados/
# Form: input "Término de búsqueda" (id dinámico
#   `SagaListado_<uuid>-buscarTexto`), dropdown "Buscado",
#   datepickers "Desde"/"Hasta".
# El listado carga client-side y un filtro client-side que no
# responde a fill()+enter de forma estable. Por eso: capturamos
# los nombres visibles en el DOM y comparamos tokens.
# Lista actual: ~9-10 nombres publicados (ETA y otros buscados).
# Para "DANIEL LORENZO MEDINA SALCEDO" el resultado será
# matched=False con detalle "nombre no aparece en la lista pública".
# =========================================================
@register
class GuardiaCivilBrowserSource:
    name = "Guardia Civil Española — Buscados (browser)"
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
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       elapsed_s=time.time()-t0)
        try:
            from browsers.pool import get_pool
            with get_pool().page() as page:
                # Timeouts cortos para que NO cuelgue el run completo
                page.set_default_timeout(8000)
                page.goto(self.source_url, wait_until="domcontentloaded",
                          timeout=12000)
                # Aceptar cookies
                for txt in ["Aceptar todas las Cookies", "Aceptar",
                            "Accept all"]:
                    try:
                        page.locator(f"button:has-text('{txt}')").first.click(
                            timeout=1200)
                        break
                    except Exception:
                        continue
                page.wait_for_timeout(1500)
                # Extraer la lista visible de nombres publicados
                # (no usamos el filtro client-side porque su ID es
                # dinámico y la respuesta no es estable desde Playwright).
                published = page.evaluate("""
                    () => {
                        // Los nombres están en <h3> dentro de cada item
                        // de la lista. Filtrar los <h3> que están dentro
                        // de listas (no en cookie banners ni sidebars).
                        const out = [];
                        const candidates = document.querySelectorAll("h3");
                        candidates.forEach(h => {
                            const li = h.closest("li, article, div.resultado, .buscado");
                            if (!li) return;
                            const text = h.innerText.trim();
                            if (text && text.length > 4 && text.length < 120) {
                                out.push(text);
                            }
                        });
                        // También buscar en headings genéricos de cards
                        if (out.length === 0) {
                            document.querySelectorAll("[class*='resultado'] h3, [class*='buscado'] h3").forEach(h => {
                                out.push(h.innerText.trim());
                            });
                        }
                        return out;
                    }
                """)
                shot = _save_shot(page, "guardia", nombre)
                if not published:
                    # Si no pudimos parsear, devolver captcha_required con
                    # notice honesto (no es captcha, es JS-only con filtro
                    # client-side inestable).
                    return Hit(self.name, False,
                               "Guardia Civil: no se pudo parsear la lista "
                               "de buscados publicados (filtro client-side "
                               "con id dinámico).",
                               notice="Sitio browser-only con resultados "
                                      "visuales. La lista pública de "
                                      "buscados no es consultable por "
                                      "texto: usa el filtro client-side "
                                      "manualmente en "
                                      f"{self.source_url}",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                # Matching: comparar tokens (case-insensitive, sin acentos)
                # del query contra cada nombre publicado
                name_norm = _normalize_accents(nombre)
                tokens = [t for t in re.split(r"[^a-z]+", name_norm)
                          if len(t) >= 3]
                details = []
                for p in published:
                    p_norm = _normalize_accents(p)
                    if all(t in p_norm for t in tokens):
                        details.append({"nombre_publicado": p})
                matched = len(details) > 0
                if matched:
                    summary = (f"{len(details)} match(s) en Guardia Civil "
                               f"para '{nombre}'")
                else:
                    # matched=False honesto: el nombre no aparece en la
                    # lista visible de ~9-10 buscados publicados al momento
                    # de la consulta.
                    summary = (f"0 resultados en Guardia Civil para "
                               f"'{nombre}' — la lista pública tiene "
                               f"{len(published)} nombre(s) publicado(s) "
                               f"y ninguno coincide")
                return Hit(self.name, matched, summary, details,
                           evidence_urls=[self.source_url],
                           download_url=shot,
                           elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Guardia Civil error: {type(e).__name__}: {e}.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
