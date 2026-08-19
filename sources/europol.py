"""
sources/europol.py — EUROPOL "Europe's Most Wanted" Fugitives.

Portal: https://eumostwanted.eu/  (Drupal 10)
URL de búsqueda: https://eumostwanted.eu/search/node?keys=<nombre>

Observaciones del sitio (Drupal 10, sin captcha, sin JS obligatorio):
  - El endpoint de búsqueda es HTML estático — se puede consultar con
    un GET de `requests` y parsear la respuesta.
  - Cuando la búsqueda NO tiene coincidencias, el HTML contiene la
    frase "Your search yielded no results." (case-insensitive).
  - Cuando SÍ tiene coincidencias, el HTML contiene un `<ol>` con
    `<li>` por cada fugitivo, cada uno con:
        <h3><a href="/#/<slug>">APELLIDO, Nombre</a></h3>
        <p>...bio con delito...</p>
  - No tiene captcha ni rate limit agresivo, pero usamos timeout
    corto + Playwright solo para el screenshot de evidencia.

Implementación:
  1. GET con `requests` → parsear HTML con regex (sin bs4 para no
     agregar dependencia).
  2. Determinar matched/no-results.
  3. Tomar screenshot del resultado con Playwright sync (browser
     NUEVO por llamada para evitar el bug de threads).
  4. Devolver Hit con download_url apuntando al screenshot.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from urllib.parse import quote

from .base import Hit
from .registry import register
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


# Path de los screenshots — sigue la convención de
# demo/sources/_browser_helper.py: data/screenshots/
from sources.base import DATA
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)


# Frase exacta que Drupal 10 muestra cuando la búsqueda no tiene
# coincidencias. El sitio la renderiza con la 'y' en minúscula.
NO_RESULTS_PHRASE = "Your search yielded no results"


# Expresiones regulares compiladas una sola vez (rendimiento en
# queries repetidos).
_LI_BLOCK_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL)
_H3_NAME_RE = re.compile(r"<h3>\s*<a[^>]+>([^<]+)</a>")
_H3_HREF_RE = re.compile(r"<h3>\s*<a[^>]+href=\"([^\"]+)\"")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OL_RE = re.compile(r"<ol[^>]*>(.*?)</ol>", re.DOTALL)


def _strip_html(s: str) -> str:
    """Quita tags HTML y colapsa whitespace."""
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _parse_results(html: str) -> list[dict]:
    """Extrae la lista de fugitivos del HTML de resultados.

    Devuelve una lista de dicts con keys: nombre, slug, bio.
    Si el HTML no contiene un <ol> con resultados, devuelve [].
    """
    ol_match = _OL_RE.search(html)
    if not ol_match:
        return []
    ol_body = ol_match.group(1)
    items = _LI_BLOCK_RE.findall(ol_body)

    out: list[dict] = []
    for it in items:
        name_m = _H3_NAME_RE.search(it)
        href_m = _H3_HREF_RE.search(it)
        if not name_m or not href_m:
            continue
        nombre = _WS_RE.sub(" ", name_m.group(1)).strip()
        slug = href_m.group(1).strip()
        # El primer <p> contiene la bio resumida (delito, país, etc.)
        p_match = re.search(r"<p>(.*?)</p>", it, re.DOTALL)
        bio = _strip_html(p_match.group(1)) if p_match else ""
        if not nombre:
            continue
        out.append({
            "nombre": nombre,
            "slug": slug,
            "bio": bio[:400],
            "url": f"https://eumostwanted.eu/{slug.lstrip('/')}",
        })
    return out


def _shot_path(query: str) -> Path:
    """Genera un path único para el screenshot de esta búsqueda."""
    safe = re.sub(r"[^\w-]", "_", f"europol_{query}")[:60]
    return DATA / "screenshots" / f"{safe}_{int(time.time())}.png"


def _shot_search_via_playwright(search_url: str, query: str) -> str | None:
    """Abre la URL de búsqueda en Chromium headless y guarda un
    screenshot de la página renderizada.

    Se usa Playwright SOLO para el screenshot — la lógica de matching
    ya se hizo arriba con `requests` (más rápido y robusto). Crea un
    sync_playwright NUEVO por llamada para evitar el bug
    "Cannot switch to a different thread" cuando se ejecuta dentro de
    un ThreadPoolExecutor.

    Devuelve la ruta relativa (desde demo/) al PNG, o None si falló.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    out = _shot_path(query)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=("VerifyData-Demo/1.0 (Mozilla/5.0 compatible)"),
            )
            try:
                page = ctx.new_page()
                page.goto(search_url, wait_until="domcontentloaded",
                          timeout=20000)
                # Esperar un instante para que Drupal pinte resultados
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # Headless Chromium NO renderiza la barra de
                # direcciones. Para que la evidencia del screenshot
                # incluya la URL exacta (parte del criterio de
                # aceptación), inyectamos un overlay fijo en la parte
                # superior con la URL actual + el query buscado.
                try:
                    actual_url = page.url or search_url
                    page.evaluate(
                        """([url, q]) => {
                            const bar = document.createElement('div');
                            bar.style.cssText = (
                              'position:fixed;top:0;left:0;right:0;'
                              + 'z-index:2147483647;'
                              + 'background:#1a1a1a;color:#00d4d4;'
                              + 'font:13px/1.4 -apple-system,BlinkMacSystemFont,monospace;'
                              + 'padding:8px 14px;'
                              + 'border-bottom:2px solid #00d4d4;'
                              + 'box-shadow:0 2px 6px rgba(0,0,0,.4);'
                              + 'word-break:break-all;'
                            );
                            bar.innerHTML =
                              '<span style="opacity:.7">URL &rsaquo;</span> '
                              + '<span style="color:#fff">' + url + '</span>'
                              + ' &nbsp;|&nbsp; '
                              + '<span style="opacity:.7">query &rsaquo;</span> '
                              + '<span style="color:#fff">' + q + '</span>';
                            document.body.appendChild(bar);
                        }""",
                        [actual_url, query],
                    )
                    # Mover el contenido de la página hacia abajo
                    # para que no quede tapado por el overlay.
                    page.evaluate(
                        """() => {
                            const b = document.body;
                            if (b) b.style.paddingTop = '48px';
                        }"""
                    )
                except Exception:
                    pass
                # Screenshot del viewport (1280x800) — incluye la URL
                # en el overlay y el texto de resultado abajo.
                page.screenshot(path=str(out), full_page=False,
                                timeout=15000)
                return str(out.relative_to(DATA))
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
        finally:
            try:
                browser.close()
            except Exception:
                pass
    except Exception as e:
        # El screenshot es "nice to have" — la búsqueda ya dio
        # resultado. Log en stderr y devolvemos None.
        print(f"  [europol screenshot fail: {type(e).__name__}: {e}]",
              flush=True)
        return None
    finally:
        try:
            pw.stop()
        except Exception:
            pass


@register
class EuropolMostWantedSource:
    name = "EUROPOL — Most Wanted Fugitives"
    source_url = "https://eumostwanted.eu/"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None) -> Hit:
        t0 = time.time()
        if not nombre or not nombre.strip():
            return Hit(
                self.name, False, "",
                notice="Requiere un nombre para buscar.",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        search_url = (f"https://eumostwanted.eu/search/node"
                      f"?keys={quote(nombre.strip())}")

        try:
            # 1) GET con requests — más rápido que Playwright y
            #    suficiente porque Drupal 10 sirve el HTML completo.
            r = requests_get(search_url)
            html = r.text

            # 2) Detectar "no results" (case-insensitive; el sitio
            #    usa 'Your' con y minúscula, Drupal puede
            #    capitalizar a 'YOUR' en otros locales).
            no_results = (NO_RESULTS_PHRASE in html
                          or NO_RESULTS_PHRASE.upper() in html.upper())

            if no_results:
                shot_rel = _shot_search_via_playwright(search_url, nombre)
                elapsed = time.time() - t0
                return Hit(
                    self.name, False,
                    "0 coincidencias en EUROPOL Most Wanted",
                    details=[],
                    evidence_urls=[search_url],
                    download_url=shot_rel,
                    elapsed_s=elapsed,
                )

            # 3) Parsear resultados
            results = _parse_results(html)
            n = len(results)
            elapsed = time.time() - t0
            shot_rel = _shot_search_via_playwright(search_url, nombre)

            if n == 0:
                # Drupal no dijo "no results" pero tampoco encontramos
                # un <ol> con <li> — caso raro. Devolvemos 0 con aviso.
                return Hit(
                    self.name, False,
                    "0 coincidencias en EUROPOL Most Wanted",
                    notice=("La página no mostró el mensaje estándar "
                            "de 'no results' pero tampoco se "
                            "encontraron tarjetas de fugitivos."),
                    evidence_urls=[search_url],
                    download_url=shot_rel,
                    elapsed_s=elapsed,
                )

            # matched=True — emitir una fila por fugitivo
            details = []
            for r in results:
                details.append({
                    "nombre": r["nombre"],
                    "delito_o_bio": r["bio"] or "—",
                    "url": r["url"],
                    "fuente": "EUROPOL Most Wanted",
                })
            return Hit(
                self.name, True,
                f"{n} coincidencia(s) en EUROPOL Most Wanted",
                details=details,
                evidence_urls=[search_url] + [r["url"] for r in results],
                download_url=shot_rel,
                elapsed_s=elapsed,
            )

        except Exception as e:
            return Hit(
                self.name, False, "",
                error=f"{type(e).__name__}: {e}",
                evidence_urls=[search_url, self.source_url],
                elapsed_s=time.time() - t0,
            )


def requests_get(url: str, timeout: int = 20):
    """Wrapper pequeño sobre requests.get con un User-Agent
    'realista'. Import lazy para no penalizar el import-time del
    paquete."""
    import requests
    return requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X "
                           "10_15_7) AppleWebKit/537.36 (KHTML, like "
                           "Gecko) Chrome/124.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;"
                      "q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        allow_redirects=True,
    )
