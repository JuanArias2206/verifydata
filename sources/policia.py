"""
sources/policia.py — Policía Nacional de Colombia.

Reescrito con Playwright + 2captcha (reCAPTCHA v2 Enterprise).

Flujo del portal:
  1) https://antecedentes.policia.gov.co:7005/  →  términos de uso
     Click "Acepto" + "Enviar"
  2) Lleva al form:
     - Tipo de documento (Cédula de Ciudadanía)
     - Número de documento
     - reCAPTCHA Enterprise (v2 con sitekey conocido)
  3) Click "Consultar" → muestra resultado
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
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    return f"screenshots/{safe}_{int(time.time())}.png"


# Sitekey oficial del reCAPTCHA Enterprise de la Policía Nacional
# (extraído del HTML de la página, puede rotar — si 2captcha rechaza,
# el código re-extrae el sitekey dinámicamente del form)
POLICIA_RECAPTCHA_SITEKEY = "6Le9t9Wd1XHzL8IQESIAESIIEsReCPe6Huf9ztHq1EH1ld9H"
POLICIA_TERMS_URL = "https://antecedentes.policia.gov.co:7005/WebJudicial/"


@register
class PoliciaAntecedentesSource:
    name = "Policía Nacional — Antecedentes Judiciales"
    source_url = POLICIA_TERMS_URL
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "recaptcha_v2_enterprise"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere cédula.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        if not solver or not solver.is_available():
            return Hit(self.name, False, "",
                       notice="Requiere solver de reCAPTCHA Enterprise. "
                              "Configurar 2captcha (api_key en config.yaml).",
                       captcha_required=True,
                       evidence_urls=[POLICIA_TERMS_URL],
                       elapsed_s=time.time()-t0)
        try:
            results = {}

            def _do_policia(page):
                page.set_default_timeout(30000)
                # 1) Cargar página de términos
                page.goto(POLICIA_TERMS_URL, wait_until="domcontentloaded",
                         timeout=30000)
                page.wait_for_timeout(3000)
                # 2) Aceptar términos: click "Acepto" (radio) + "Enviar"
                try:
                    page.locator("input[name*='acept']").first.check(timeout=5000)
                except Exception:
                    try:
                        page.locator("label:has-text('Acepto')").first.click(
                            timeout=3000)
                    except Exception:
                        pass
                page.wait_for_timeout(1000)
                # Botón Enviar es BUTTON con id=continuarBtn o text=Enviar
                try:
                    page.locator("#continuarBtn").first.click(timeout=5000)
                except Exception:
                    try:
                        page.locator("button:has-text('Enviar')").first.click(
                            timeout=5000)
                    except Exception:
                        try:
                            page.locator("input[value='Enviar']").first.click(
                                timeout=3000)
                        except Exception:
                            pass
                # 3) Esperar al form
                page.wait_for_timeout(8000)
                # 4) Extraer sitekey del reCAPTCHA (puede ser dinámicamente cargado)
                html = page.content()
                sitekey = None
                m = re.search(r'data-sitekey=["\']([A-Za-z0-9_-]{20,80})', html)
                if m: sitekey = m.group(1)
                if not sitekey:
                    m = re.search(r'(6L[A-Za-z0-9_-]{30,80})', html)
                    if m: sitekey = m.group(1)
                if not sitekey:
                    try:
                        sitekey = page.evaluate("""
                            () => {
                              const ifr = document.querySelector(
                                'iframe[src*="recaptcha"]');
                              if (ifr) {
                                const m = ifr.src.match(/k=([^&]+)/);
                                if (m) return m[1];
                              }
                              const div = document.querySelector('[data-sitekey]');
                              if (div) return div.getAttribute('data-sitekey');
                              return null;
                            }
                        """)
                    except Exception:
                        pass
                if not sitekey:
                    sitekey = POLICIA_RECAPTCHA_SITEKEY
                results["sitekey"] = sitekey
                # 5) Seleccionar tipo de documento "Cédula de ciudadanía"
                try:
                    select = page.locator("select#cedulaTipo, "
                                        "select[name='cedulaTipo']").first
                    select.wait_for(timeout=10000)
                    select.select_option(value="cc")
                except Exception:
                    try:
                        page.locator("select").first.select_option(value="cc")
                    except Exception:
                        pass
                # 6) Llenar número de documento
                page.wait_for_timeout(2000)
                try:
                    inp = page.locator("input#cedulaInput, "
                                       "input[name='cedulaInput']").first
                    inp.wait_for(timeout=10000)
                    inp.fill(cedula, timeout=5000)
                except Exception:
                    page.evaluate(f"""
                        () => {{
                          const inp = document.getElementById('cedulaInput') ||
                                       document.querySelector(
                                         "input[name='cedulaInput']");
                          if (inp) {{
                            inp.focus();
                            inp.value = "{cedula}";
                            inp.dispatchEvent(new Event('input', {{
                              bubbles: true}}));
                            inp.dispatchEvent(new Event('change', {{
                              bubbles: true}}));
                            inp.dispatchEvent(new Event('blur', {{
                              bubbles: true}}));
                          }}
                        }}
                    """)
                page.wait_for_timeout(2000)
                # 7) Screenshot del form lleno (pre-captcha)
                shot_form = _shot_path("policia_form", cedula)
                page.screenshot(path=str(DATA / shot_form),
                              full_page=False, timeout=15000)
                results["shot_form"] = shot_form
                results["page_url"] = page.url
                # 8) Resolver reCAPTCHA v2 Enterprise vía 2captcha
                token = solver.solve_recaptcha_v2(
                    sitekey=sitekey,
                    page_url=page.url,
                    enterprise=True,
                )
                if not token:
                    results["error"] = "Solver no retornó token"
                    return
                # 8) Inyectar token en la página
                page.evaluate(f"""
                    () => {{
                      const ta = document.querySelector(
                        '#g-recaptcha-response, textarea[name=g-recaptcha-response]');
                      if (ta) {{
                        ta.value = "{token}";
                        ta.style.display = 'block';
                        ta.innerHTML = "{token}";
                      }}
                    }}
                """)
                # Llamar al callback de grecaptcha si existe
                page.evaluate("""
                    (token) => {
                      try {
                        if (typeof ___grecaptcha_cfg !== 'undefined') {
                          const clients = ___grecaptcha_cfg.clients;
                          for (const k of Object.keys(clients)) {
                            const c = clients[k];
                            for (const kk of Object.keys(c || {})) {
                              const obj = c[kk];
                              if (obj && typeof obj.callback === 'function') {
                                obj.callback(token);
                                return;
                              }
                            }
                          }
                        }
                      } catch (e) {}
                    }
                """, token)
                page.wait_for_timeout(3000)
                # 9) Click en Consultar
                try:
                    page.locator("#consultarBtn, "
                                "button:has-text('Consultar')").first.click(
                        timeout=5000)
                except Exception:
                    try:
                        page.locator("input[value='Consultar']").first.click(
                            timeout=3000)
                    except Exception:
                        pass
                # 10) Esperar resultado
                page.wait_for_timeout(8000)
                # 11) Screenshot del resultado
                shot_result = _shot_path("policia_result", cedula)
                page.screenshot(path=str(DATA / shot_result),
                              full_page=False, timeout=15000)
                results["shot_result"] = shot_result
                # 12) Parsear resultado — SOLO un veredicto si el texto de la
                # página contiene las frases oficiales. Un resultado ilegible
                # NUNCA se reporta como "no registra" (falso negativo).
                html = page.content()
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text)
                norm = text.upper()
                for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"),
                             ("Ó", "O"), ("Ú", "U")):
                    norm = norm.replace(a, b)
                ced_digits = re.sub(r"\D", "", cedula or "")
                ced_on_page = bool(ced_digits) and ced_digits in re.sub(
                    r"[.\s]", "", norm)
                # Frases oficiales del portal de antecedentes de la Policía.
                # El sitio actual usa variantes (mayúsculas/minúsculas,
                # con/sin acentos) y a veces fragmentos de la frase.
                CLEAN_PHRASES = (
                    # Forma canónica completa
                    "NO TIENE ASUNTOS PENDIENTES CON LAS AUTORIDADES "
                    "JUDICIALES",
                    "NO REGISTRA ANTECEDENTES",
                    "NO PRESENTA ANTECEDENTES",
                    # Variantes parciales / sin acentos
                    "NO TIENE ASUNTOS PENDIENTES",
                    "ASUNTOS PENDIENTES CON LAS AUTORIDADES",
                    "EL CIUDADANO NO REGISTRA",
                    "NO APARECE REGISTRADO",
                    "CONSULTA NEGATIVA",
                    "SIN ANTECEDENTES",
                    "NO REPORTA ANTECEDENTES",
                )
                # Frases que indican SÍ registra (match)
                HIT_PHRASES = (
                    "REGISTRA ANTECEDENTES",
                    "PRESENTA ANTECEDENTES",
                    "EL CIUDADANO REGISTRA",
                    "TIENE ASUNTOS PENDIENTES",
                    "ASUNTOS PENDIENTES",
                )
                # Palabras que confirman que estamos en la página de
                # resultado real (vs página de términos / WAF).
                RESULT_PAGE_MARKERS = (
                    "RESULTADO",
                    "CONSULTA",
                    "ANTECEDENTE",
                    "CEDULA",
                    "CÉDULA",
                    "NOMBRE",
                    "FECHA DE NACIMIENTO",
                )
                result_page_visible = sum(
                    1 for m in RESULT_PAGE_MARKERS if m in norm) >= 3

                if any(p in norm for p in CLEAN_PHRASES):
                    # Verificación adicional: la cédula debe estar en la
                    # página de resultado. Si NO está, es probable que la
                    # página sea de términos y la frase vino de otro lado
                    # → marcar como manual_review.
                    if ced_on_page or result_page_visible:
                        results["resultado"] = (
                            "NO REGISTRA antecedentes (frase oficial "
                            "verificada en el texto de la página"
                            + (", documento visible" if ced_on_page else "")
                            + ")", False)
                        results["verdict"] = "nomatch"
                    else:
                        # Frase de "no registra" presente pero sin señal
                        # fuerte de estar en la página de resultado real.
                        # Marcar como manual_review (NO como nomatch
                        # silencioso, para evitar falsos negativos).
                        results["resultado"] = (
                            "NO REGISTRA antecedentes: se detectó una "
                            "frase compatible, pero la página no expone "
                            "el documento consultado ni los marcadores "
                            "típicos de la página de resultado. Revisar "
                            "screenshot para confirmar.", False)
                        results["verdict"] = "manual_review"
                elif any(p in norm for p in HIT_PHRASES):
                    # Match: extraer filas de detalle
                    details = []
                    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S):
                        cells = re.findall(r'<td[^>]*>([^<]+)</td>', m.group(1))
                        if not cells: continue
                        joined = " | ".join(c.strip() for c in cells)
                        if 20 < len(joined) < 300:
                            details.append({"fila": joined})
                            if len(details) >= 5: break
                    # Si la cédula NO está en la página, NO reportar
                    # 'match' automático: marcar para revisión.
                    if ced_on_page or result_page_visible:
                        results["resultado"] = (
                            f"SÍ REGISTRA antecedentes ({len(details)} "
                            f"filas, documento verificado)", True)
                        results["details"] = details
                        results["verdict"] = "match"
                    else:
                        # Frase de 'match' presente pero sin señal fuerte
                        # de la página de resultado: pedir revisión.
                        results["resultado"] = (
                            f"SÍ REGISTRA antecedentes: se detectó una "
                            f"frase compatible ({len(details)} filas "
                            f"extraídas), pero sin marcadores de página "
                            f"de resultado. Revisar screenshot.", True)
                        results["details"] = details
                        results["verdict"] = "manual_review"
                else:
                    # No se detectó ninguna frase oficial → manual_review
                    # (NUNCA reportar 'no registra' silencioso).
                    # Antes: cualquier página sin frases matcheaba aquí
                    # y se etiquetaba 'unclear'. Ahora: añadir
                    # diagnóstico más explícito de QUÉ se vio en pantalla
                    # (¿hay un mensaje? ¿hay un campo de cédula?).
                    diag = []
                    if ced_on_page:
                        diag.append("documento visible en pantalla")
                    if result_page_visible:
                        diag.append("marcadores de página de resultado")
                    if "CONSULTA EN PROCESO" in norm or "PROCESANDO" in norm:
                        diag.append("posible mensaje de 'procesando'")
                    if "SERVICIO NO DISPONIBLE" in norm or "NO DISPONIBLE" in norm:
                        diag.append("sitio reporta 'no disponible'")
                    if not diag:
                        diag.append("sin marcadores de resultado visibles")
                    results["resultado"] = (
                        "La búsqueda se ejecutó pero el texto del "
                        "resultado no pudo verificarse automáticamente. "
                        f"Diagnóstico: {', '.join(diag)}.", False)
                    results["verdict"] = "unclear"
            _run_in_fresh_browser(_do_policia)
            if "error" in results:
                return Hit(self.name, False, "",
                           notice=f"Policía: {results['error']}",
                           captcha_required=True,
                           evidence_urls=[POLICIA_TERMS_URL],
                           download_url=results.get("shot_form"),
                           elapsed_s=time.time()-t0)
            summary, matched = results.get(
                "resultado", ("Sin respuesta", False))
            details = results.get("details", [])
            verdict = results.get("verdict", "unclear")
            hit = Hit(self.name, matched, summary, details,
                      evidence_urls=[POLICIA_TERMS_URL],
                      download_url=results.get("shot_result")
                                   or results.get("shot_form"),
                      elapsed_s=time.time()-t0)
            if verdict == "match":
                hit.status = "match_exact"
                hit.confidence = "exacta"
                hit.matched_document = cedula
            elif verdict == "nomatch":
                hit.status = "nomatch_verified"
                hit.matched_document = cedula
            elif verdict == "manual_review":
                # Detectamos una frase compatible (match o nomatch) pero
                # sin señal fuerte de estar en la página de resultado real.
                # Pedir revisión humana del screenshot, sin afirmar ni
                # negar el resultado.
                hit.status = "manual_review"
                hit.requires_manual_review = True
                hit.matched_document = cedula
                hit.notes = ("La página de la Policía contiene una frase "
                             "compatible con el veredicto, pero faltan "
                             "marcadores de la página de resultado (cédula "
                             "consultada visible o estructura típica). "
                             "Revisar el screenshot para emitir el "
                             "veredicto final.")
            else:
                # verdict == "unclear" (no se detectó ninguna frase)
                # Resultado ilegible: pedir revisión humana del screenshot,
                # jamás dejarlo con el badge de "sin registro".
                hit.status = "manual_review"
                hit.requires_manual_review = True
                hit.notes = ("El texto de la página de resultados no contiene "
                             "las frases oficiales esperadas. Revisar el "
                             "screenshot para emitir el veredicto.")
            return hit
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Policía error: {type(e).__name__}: {e}.",
                       captcha_required=True,
                       evidence_urls=[POLICIA_TERMS_URL],
                       elapsed_s=time.time()-t0)


def _run_in_fresh_browser(fn):
    """Crea un Playwright sync NUEVO por llamada (evita el bug de threads)."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerifyData-Demo/1.0 (Mozilla/5.0 compatible)")
            try:
                page = ctx.new_page()
                return fn(page)
            finally:
                try: ctx.close()
                except Exception: pass
        finally:
            try: browser.close()
            except Exception: pass
    finally:
        try: pw.stop()
        except Exception: pass


@register
class PoliciaDelitosSexualesSource:
    """Consulta de inhabilidades por delitos sexuales contra menores de 18
    años. Es EXACTAMENTE el mismo portal y consulta que la Ley 1918
    (inhabilidades.policia.gov.co:8080/consulta), por lo que delegamos en la
    implementación completa (browser + reCAPTCHA Enterprise + proxy residencial)
    de `PoliciaInhabilidadesSource` en lugar de un stub manual."""
    name = "Policía — Delitos Sexuales contra Menores"
    source_url = "https://inhabilidades.policia.gov.co:8080/consulta"
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "recaptcha_v2_enterprise"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            from .policia_inhab import PoliciaInhabilidadesSource
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"No se pudo cargar el flujo de consulta: {e}.",
                       captcha_required=True,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
        # Ejecutar el flujo real y re-etiquetar el Hit con el nombre de esta
        # fuente (misma consulta, distinta etiqueta en el reporte).
        hit = PoliciaInhabilidadesSource().fetch(nombre, cedula, fecha_exp, solver)
        hit.source = self.name
        if not hit.evidence_urls:
            hit.evidence_urls = [self.source_url]
        return hit
