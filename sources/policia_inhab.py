"""
sources/policia_inhab.py — Policía Nacional: Inhabilidades por delitos
sexuales cometidos contra menores de 18 años (Ley 1918 de 2018 / Ley
2375 de 2024).

URL real con form: https://inhabilidades.policia.gov.co:8080/consulta
Form: Tipo de Documento (SELECT) + Número de Documento + Fecha de
Expedición (DD/MM/AAAA) + Empresa o Entidad Consultante + NIT de la
Empresa + reCAPTCHA Enterprise v2 + checkbox "Acepto términos" + botón
"Consultar".

Datos FIJOS de la consulta (entidad consultante, ver CLIENTE_NIT/CLIENTE_EMPRESA):
  - NIT de la Empresa: el de la entidad consultante configurada
  - Empresa o Entidad Consultante: el nombre configurado
  - Cédula: la del usuario que se está consultando
  - Fecha de Expedición: la del documento del usuario (pasada al fetch)

Flujo implementado con Playwright + 2captcha (reCAPTCHA v2 Enterprise):
  1) Cargar /consulta
  2) Esperar a que cargue reCAPTCHA (3-4s)
  3) Detectar sitekey (data-sitekey attribute o fallback regex)
  4) Seleccionar "Cédula de ciudadanía" en Tipo de Documento
  5) Llenar nuip, fechaExpNuip, nombreEmpresa, nitEmpresa
  6) Marcar checkbox "Acepto términos y condiciones"
  7) Resolver reCAPTCHA Enterprise vía 2captcha CON PROXY RESIDENCIAL
     (TwoCaptchaSolver use_webshare=True — reduce rechazos server-side
     del WAF de la Policía, que rechaza tokens resueltos desde IPs
     datacenter).
  8) Inyectar token en TODOS los textareas g-recaptcha-response
     (incluyendo #captcha, hidden, etc.) y disparar callbacks:
       a) data-callback del div .g-recaptcha
       b) Walk recursivo en ___grecaptcha_cfg buscando callbacks con
          sitekey que empieza por 6L (patrón enterprise obfuscated)
       c) grecaptcha.execute(sitekey, {action: 'submit'}) si existe
     Esperar con wait_for_function() hasta que grecaptcha.getResponse()
     retorne un valor no vacío (estado interno del widget = "armed").
  9) Submit en cascada (3 métodos, ejecutar TODOS, no detenerse en el
     primero):
       a) button.click() — dispara el onclick real del botón
       b) jQuery('#form').submit() — handler jQuery
       c) form.submit() — bypass onsubmit, último recurso
 10) Esperar nav_changed=True o POST visible antes de tomar screenshot
     (sin esto, capturamos la pantalla de "Cargando" pre-respuesta).
 11) Polling del resultado: "NO REGISTRA INHABILIDAD" o
     datos del condenado (nombre, cédula, delito, fecha, ley)
 12) Screenshot del estado final (post-submit) — download_url
 13) Parsear resultado honestamente:
       "NO REGISTRA INHABILIDAD" -> matched=False
       Datos de condenado visible -> matched=True
       Otro -> notice honesto
"""
from __future__ import annotations
import os
import re
import time
from pathlib import Path
from urllib.parse import quote
from .base import Hit
from .registry import register
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)

DATA = Path(__file__).parent.parent / "data"
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _shot_path(source: str, query: str, tag: str = "") -> str:
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    suffix = f"_{tag}" if tag else ""
    return f"screenshots/{safe}{suffix}_{int(time.time())}.png"


# --- Constantes del portal ---
POLICIA_INHAB_URL = "https://inhabilidades.policia.gov.co:8080/consulta"
# Sitekey oficial del reCAPTCHA Enterprise del portal de Inhabilidades
# Policía Nacional (extraído del HTML vía [data-sitekey]; estable
# mientras el portal no rote su reCAPTCHA — si 2captcha rechaza, el
# código re-extrae el sitekey dinámicamente).
POLICIA_INHAB_RECAPTCHA_SITEKEY = "6LflZLwUAAAAAP6-I_SuqVa1YDSTqfMyk43peb_M"

# --- Datos fijos de la entidad consultante ---
# Se envían en el formulario del portal (NIT + nombre de la empresa que
# consulta). Configurables por entorno; valores demo por defecto.
CLIENTE_NIT = os.environ.get("VERIFYDATA_ENTITY_NIT", "900000000-1")
CLIENTE_EMPRESA = os.environ.get("VERIFYDATA_ENTITY_NAME", "VerifyData")

# Tiempos / presupuesto (caben en el timeout duro de 70s de runs.py).
# Para reCAPTCHA Enterprise, 2captcha tarda 40-90s con proxy residencial
# (variable según carga del servicio). 1 intento es suficiente; si
# falla por overload, el retry sería igual de lento y consumiría el
# doble de budget. Reportamos honestamente "captcha no resuelto".
PAGE_LOAD_WAIT_MS = 4000      # 4s de espera inicial (carga reCAPTCHA)
RESULT_TXT_TIMEOUT_MS = 8000  # 8s de polling del resultado
RESULT_POLL_MS = 600
MAX_CAPTCHA_ATTEMPTS = 1       # 1 intento; 2captcha es lento (40-90s)
# Budget total dentro del fetch(). 2captcha + form fill + submit +
# polling + screenshot debe caber aquí. Apretamos solver.timeout por
# intento a CAPTCHA_SOLVE_TIMEOUT_S (60s) para evitar esperar 180s
# default que arruinaría el budget.
# runs.py da 170s de subproceso a esta fuente; usamos ~150s para caber con
# holgura. Un solve de reCAPTCHA Enterprise (CapSolver o 2captcha) tarda
# 40-120s según carga, así que damos tiempo real en lugar de matarlo temprano.
HARD_TOTAL_BUDGET_S = 150      # budget total dentro del subproceso de 170s
RESERVED_FOR_REST_S = 15       # submit + polling + screenshot
CAPTCHA_SOLVE_TIMEOUT_S = 120  # timeout interno del solver (CapSolver→2captcha)
GRECAPTCHA_GETRESPONSE_WAIT_MS = 6000  # espera máxima por getResponse()


def _build_solver():
    """Construye la CADENA de solvers CapSolver → 2captcha (ambos servicios),
    con proxy residencial para 2captcha. CapSolver es proxyless y el mejor
    para reCAPTCHA Enterprise (que es lo que usa este portal); si falla, cae
    a 2captcha con proxy webshare. NO confiamos en el solver pasado a fetch()
    (puede ser NoOp/trivia). Fallback a 2captcha-solo si build_chain falla."""
    try:
        from config import load_config
        from solvers.factory import build_chain
        cfg = load_config()
        tc = cfg.get("captcha", {}).get("twocaptcha", {})
        return build_chain(cfg, use_proxy=True,
                           timeout=tc.get("default_timeout", 180))
    except Exception as e:
        print(f"  [policia_inhab] build_chain fail ({e}); usando 2captcha solo",
              flush=True)
        try:
            from solvers.twocaptcha import TwoCaptchaSolver
            from config import load_config
            api_key = (load_config().get("captcha", {}).get("twocaptcha", {})
                       .get("api_key", "") or "39678a755a8df343ddfa075c132e4202")
            return TwoCaptchaSolver(api_key=api_key, use_webshare=True)
        except Exception as e2:
            print(f"  [policia_inhab] error construyendo solver: {e2}", flush=True)
            return None


# JS que inyecta el token y dispara el callback. Estrategia exhaustiva
# porque reCAPTCHA Enterprise obfuscated:
#   1) Token en TODOS los textareas g-recaptcha-response
#      (incluyendo #captcha, hidden, etc.)
#   2) data-callback del div .g-recaptcha (poco probable en enterprise)
#   3) Walk recursivo en ___grecaptcha_cfg buscando callbacks con
#      sitekey que empieza por 6L (patrón enterprise: el callback no
#      es global, está scoped al cliente específico)
#   4) grecaptcha.execute(sitekey, {action: 'submit'}) — v2 enterprise
#      dispara el callback al terminar el challenge. Retorna Promise;
#      no podemos await desde evaluate(), pero la página la procesa
#      async.
#   5) Diagnóstico: grecaptcha.getResponse() para confirmar que el
#      estado interno del widget está "armed" con el token.
INJECT_AND_FIRE_JS = r"""
(token) => {
  const out = {
    textarea: 0, callbackCalled: false, callbacksInvoked: [],
    grecaptchaResponse: '', dataCallbackName: null, sitekey: null,
    executeAttempted: false, executeReturnedPromise: false,
    cfgCallbacksFound: 0, error: null,
  };
  try {
    const selectors = [
      '#g-recaptcha-response',
      'textarea[name="g-recaptcha-response"]',
      'textarea[name$="g-recaptcha-response"]',
      'textarea[id*="g-recaptcha-response"]',
      'textarea.g-recaptcha-response',
    ];
    let count = 0;
    for (const sel of selectors) {
      try {
        document.querySelectorAll(sel).forEach(ta => {
          ta.value = token;
          ta.innerHTML = token;
          ta.style.display = 'block';
          count++;
          try { ta.dispatchEvent(new Event('input', {bubbles: true})); } catch(e){}
          try { ta.dispatchEvent(new Event('change', {bubbles: true})); } catch(e){}
        });
      } catch (e) {}
    }
    // El sitio de Policia Inhabilidades usa un input hidden #captcha
    // que el jQuery validator inspecciona (regla 'captcha.required' que
    // también llama a grecaptcha.getResponse()). Setearlo también ayuda
    // a la habilitación del botón si grecaptcha no se invoca por callback.
    try {
      const captchaInput = document.getElementById('captcha');
      if (captchaInput) {
        captchaInput.value = token;
        captchaInput.dispatchEvent(new Event('change', {bubbles: true}));
        captchaInput.dispatchEvent(new Event('input', {bubbles: true}));
      }
    } catch (e) {}
    out.textarea = count;
  } catch (e) { out.error = 'textarea: ' + e.message; }

  try {
    const div = document.querySelector('.g-recaptcha, [data-sitekey]');
    if (div) {
      out.sitekey = div.getAttribute('data-sitekey') || null;
      const cbName = div.getAttribute('data-callback');
      if (cbName) {
        out.dataCallbackName = cbName;
        try {
          let fn = null;
          try { fn = window[cbName]; } catch (e) {}
          if (!fn) { try { fn = eval(cbName); } catch (e) {} }
          if (typeof fn === 'function') {
            fn(token);
            out.callbacksInvoked.push('data-callback: ' + cbName);
            out.callbackCalled = true;
          }
        } catch (e) { out.error = 'data-callback: ' + e.message; }
      }
    }
  } catch (e) {}

  // Walk recursivo en ___grecaptcha_cfg buscando TODOS los callbacks
  // de cliente con sitekey 6L... Patrón enterprise: cada cliente tiene
  // su propio callback scope; hay que invocar los de nuestro sitekey
  // para que grecaptcha.getResponse() devuelva el token correcto.
  try {
    function walk(obj, depth, path, found) {
      if (!obj || typeof obj !== 'object' || depth > 12) return;
      if (found.length >= 16) return;
      if (typeof obj.callback === 'function' &&
          typeof obj.sitekey === 'string' &&
          obj.sitekey.startsWith('6L')) {
        found.push({path: path + '.callback', fn: obj.callback,
                    sitekey: obj.sitekey});
        return;
      }
      if (typeof obj === 'function') return;
      for (const k of Object.keys(obj)) {
        try { walk(obj[k], depth + 1, path + '.' + k, found); }
        catch (e) {}
      }
    }
    if (typeof ___grecaptcha_cfg !== 'undefined') {
      const found = [];
      walk(___grecaptcha_cfg, 0, '___grecaptcha_cfg', found);
      out.cfgCallbacksFound = found.length;
      for (const f of found) {
        try {
          f.fn(token);
          out.callbacksInvoked.push('cfg: ' + f.path);
          out.callbackCalled = true;
        } catch (e) {}
      }
    }
  } catch (e) {}

  try {
    if (typeof grecaptcha !== 'undefined' && grecaptcha.execute) {
      const sk = out.sitekey;
      if (sk) {
        out.executeAttempted = true;
        try {
          const r = grecaptcha.execute(sk, {action: 'submit'});
          if (r && typeof r.then === 'function') {
            out.executeReturnedPromise = true;
            // No podemos await desde evaluate(); la página la procesa
            // async. Verificamos getResponse() abajo y con
            // page.wait_for_function() del lado Python.
          }
        } catch (e) {}
      }
    }
  } catch (e) {}

  try {
    if (typeof grecaptcha !== 'undefined' && grecaptcha.getResponse) {
      const gr = grecaptcha.getResponse();
      if (gr) {
        out.grecaptchaResponse = gr.length > 40 ? gr.slice(0,40) + '...' : gr;
      }
    }
  } catch (e) {}

  return out;
}
"""


# JS para detectar el sitekey (multi-método, ver
# demo/sources/contraloria.py:331-340)
DETECT_SITEKEY_JS = r"""
() => {
  const div = document.querySelector('[data-sitekey]');
  if (div) return div.getAttribute('data-sitekey');
  const ifr = document.querySelector('iframe[src*="recaptcha"]');
  if (ifr) {
    const m = ifr.src.match(/[?&]k=([^&]+)/);
    if (m) return m[1];
  }
  const scripts = document.querySelectorAll('script[src*="recaptcha"]');
  for (const s of scripts) {
    const m = s.src.match(/[?&]render=([^&]+)/);
    if (m) return m[1];
  }
  const html = document.documentElement.outerHTML;
  const m = html.match(/(6L[A-Za-z0-9_-]{30,60})/);
  if (m) return m[1];
  return null;
}
"""


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
                viewport={"width": 1280, "height": 900},
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


def _wait_for_result_text(page, timeout_ms=RESULT_TXT_TIMEOUT_MS):
    """Polling del HTML de la página hasta que aparezca evidencia del
    resultado (NO REGISTRA / REGISTRA / condena / mensaje de error
    claro del servidor)."""
    deadline = time.time() + (timeout_ms / 1000.0)
    last_html = ""
    while time.time() < deadline:
        try:
            last_html = page.content()
        except Exception:
            last_html = ""
        text = re.sub(r"<[^>]+>", " ", last_html)
        text_clean = re.sub(r"\s+", " ", text).upper()
        text_ascii = (text_clean
                      .replace("Í", "I").replace("É", "E")
                      .replace("Á", "A").replace("Ó", "O")
                      .replace("Ú", "U"))
        if ("NO REGISTRA" in text_ascii and
                ("INHABILID" in text_ascii or "INHAB" in text_ascii)):
            return last_html
        if ("REGISTR" in text_ascii and "INHAB" in text_ascii and
                "NO REGISTRA" not in text_ascii):
            return last_html
        # Detección del mensaje de error/servidor típico
        if ("SERVICE UNAVAILABLE" in text_ascii or
                "NO DISPONIBLE" in text_ascii or
                "CONSULTA NO EXITOSA" in text_ascii or
                "CONSULTA EXITOSA" in text_ascii or
                "SISTEMA NO DISPONIBLE" in text_ascii):
            return last_html
        time.sleep(RESULT_POLL_MS / 1000.0)
    return last_html


def _parse_resultado(html: str) -> tuple[str, bool, list[dict]]:
    """Devuelve (summary, matched, details) según el HTML del resultado.

    Summary HONESTO: si el HTML no contiene evidencia clara de
    "NO REGISTRA INHABILIDAD" ni de un condenado, retornamos un
    summary que describe el estado real de la página (no el
    placeholder genérico que oculta el fallo).
    """
    if not html:
        return ("No se obtuvo respuesta del servidor "
                "(HTML vacío tras submit).", False, [])
    text = re.sub(r"<[^>]+>", " ", html)
    text_clean = re.sub(r"\s+", " ", text).upper()
    text_ascii = (text_clean
                  .replace("Í", "I").replace("É", "E")
                  .replace("Á", "A").replace("Ó", "O")
                  .replace("Ú", "U"))

    # 1) "NO REGISTRA INHABILIDAD" — el caso típico
    if "NO REGISTRA" in text_ascii and "INHAB" in text_ascii:
        return (
            f"NO REGISTRA INHABILIDAD en Policia (consulta por "
            f"NIT {CLIENTE_NIT}, empresa {CLIENTE_EMPRESA})",
            False, [])

    # 2) Persona condenada — extraer detalles
    if "REGISTR" in text_ascii and "INHAB" in text_ascii:
        details = []
        # Buscar filas tipo tabla con datos del condenado
        for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S):
            cells = re.findall(r'<td[^>]*>([^<]+)</td>', m.group(1))
            if not cells:
                continue
            joined = " | ".join(c.strip() for c in cells)
            if 20 < len(joined) < 400:
                details.append({"fila": joined})
                if len(details) >= 5:
                    break
        # También intentar extraer datos del texto visible
        # (nombre, delito, ley, fecha)
        m_nombre = re.search(
            r"NOMBRE\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ ]{10,80})", text_clean)
        m_delito = re.search(
            r"DELITO\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ ]{5,80})", text_clean)
        m_ley = re.search(r"LEY\s*[:\-]?\s*(\d{3,5}(?:\s*DE\s*\d{4})?)",
                          text_clean)
        summary_parts = ["SÍ REGISTRA INHABILIDAD en Policia"]
        if m_nombre:
            summary_parts.append(f"nombre={m_nombre.group(1).strip()}")
        if m_delito:
            summary_parts.append(f"delito={m_delito.group(1).strip()}")
        if m_ley:
            summary_parts.append(f"ley={m_ley.group(1).strip()}")
        if not details and len(summary_parts) == 1:
            summary_parts.append("(ver screenshot)")
        return (" — ".join(summary_parts), True, details)

    # 3) Mensaje de error del servidor explícito
    if "SERVICE UNAVAILABLE" in text_ascii or \
       "SISTEMA NO DISPONIBLE" in text_ascii or \
       "NO DISPONIBLE" in text_ascii:
        return ("No se obtuvo respuesta del servidor: el portal de "
                "inhabilidades de la Policia reporta 'no disponible'.",
                False, [])

    # 3b) WAF CSIRT-PONAL bloqueó la solicitud por actividad no
    #     autorizada (típico cuando el reCAPTCHA Enterprise está
    #     sobrecargado, o cuando se usa un token falso).
    if "ACTIVIDAD NO AUTORIZADA" in text_ascii or \
       "CSIRT-PONAL" in text_ascii or \
       "NO AUTORIZADA" in text_ascii:
        return ("No se obtuvo respuesta del servidor: el WAF de la Policia "
                "(CSIRT-PONAL) bloqueo la solicitud por 'actividad no "
                "autorizada'. Esto indica rechazo del reCAPTCHA Enterprise "
                "o deteccion de automatizacion. Ver screenshot para el "
                "numero de caso.",
                False, [])

    # 4) El site re-renderizó el form con un nuevo bft token → el
    #    server rechazó el captcha
    if "0cAFcWE" in text_ascii or re.search(
            r'0c[A-Za-z0-9_-]{20,}', html):
        return ("No se obtuvo respuesta del servidor: el sitio "
                "re-renderizo el formulario con un nuevo token reCAPTCHA "
                "(posible rechazo server-side del token 2captcha).",
                False, [])

    # 5) Default honesto: no asumimos "NO REGISTRA" si el HTML no lo dice
    return ("No se obtuvo respuesta del servidor (el HTML no contiene "
            "'NO REGISTRA INHABILIDAD' ni datos de condenado tras el "
            "submit; ver screenshot).", False, [])


def _fill_input(page, selector: str, value: str) -> bool:
    """Llena un input con fallback JS si Playwright no lo encuentra
    directamente. Retorna True si parece haberse llenado."""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=8000)
        loc.fill(value, timeout=5000)
        return True
    except Exception:
        # Fallback JS para inputs que Playwright no puede acceder
        try:
            ok = page.evaluate(f"""
                (sel) => {{
                  const el = document.querySelector(sel);
                  if (!el) return false;
                  el.focus();
                  el.value = {value!r};
                  el.dispatchEvent(new Event('input', {{bubbles: true}}));
                  el.dispatchEvent(new Event('change', {{bubbles: true}}));
                  el.dispatchEvent(new Event('blur', {{bubbles: true}}));
                  return true;
                }}
            """, selector)
            return bool(ok)
        except Exception:
            return False


@register
class PoliciaInhabilidadesSource:
    name = ("Policia Nacional — Inhabilidades por delitos sexuales "
            "(Ley 1918)")
    source_url = POLICIA_INHAB_URL
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "recaptcha_v2_enterprise"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere cedula.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        if not fecha_exp:
            return Hit(self.name, False, "",
                       notice="Requiere fecha de expedicion del documento "
                              "(formato DD/MM/AAAA).",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        # Normalizar fecha_exp al formato DD/MM/AAAA que espera el form
        fecha_norm = _normalize_date(fecha_exp)
        if not fecha_norm:
            return Hit(self.name, False, "",
                       notice=f"fecha_exp '{fecha_exp}' no se pudo "
                              f"normalizar a DD/MM/AAAA.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)

        # Construir solver local con proxy residencial (use_webshare=True).
        # NO confiamos en el solver pasado al fetch() — el solver global
        # puede ser NoOp o trivia. Aún si fuera 2captcha, queremos
        # garantizar que use proxy para mejorar el rate de éxito contra
        # el WAF de Policía (que rechaza tokens desde IPs datacenter).
        local_solver = _build_solver()
        solver_to_use = local_solver if (
            local_solver and local_solver.is_available()) else solver
        if not solver_to_use or not solver_to_use.is_available():
            return Hit(self.name, False, "",
                       notice="Requiere solver de reCAPTCHA Enterprise. "
                              "Configurar 2captcha (api_key en "
                              "config.yaml).",
                       captcha_required=True,
                       evidence_urls=[POLICIA_INHAB_URL],
                       elapsed_s=time.time()-t0)
        # Anunciar qué solver estamos usando (diagnóstico)
        solver_name = getattr(solver_to_use, "name", "?")
        proxy_url = getattr(solver_to_use, "_proxy_url", None)
        proxy_info = f"proxy={proxy_url[:50]}..." if proxy_url else "no-proxy"
        try:
            results: dict = {"attempts": 0, "error": None,
                             "solver": solver_name,
                             "proxy_info": proxy_info}

            def _do_inhab(page):
                page.set_default_timeout(30000)
                # 1) Cargar página
                page.goto(POLICIA_INHAB_URL, wait_until="domcontentloaded",
                          timeout=30000)
                # 2) Esperar a que cargue reCAPTCHA (carga diferida)
                page.wait_for_timeout(PAGE_LOAD_WAIT_MS)
                # 3) Detectar sitekey (varios métodos)
                sitekey = None
                try:
                    sitekey = page.evaluate(DETECT_SITEKEY_JS)
                except Exception:
                    pass
                if not sitekey:
                    html_now = page.content()
                    m = re.search(
                        r'data-sitekey=["\']([A-Za-z0-9_-]{20,80})',
                        html_now)
                    if m:
                        sitekey = m.group(1)
                if not sitekey:
                    m = re.search(
                        r'(6L[A-Za-z0-9_-]{30,80})', html_now or "")
                    if m:
                        sitekey = m.group(1)
                if not sitekey:
                    sitekey = POLICIA_INHAB_RECAPTCHA_SITEKEY
                results["sitekey"] = sitekey
                # 4) Seleccionar "Cédula de ciudadanía" en Tipo de Documento
                try:
                    select = page.locator("select#tipo, select[name='tipo']").first
                    select.wait_for(timeout=8000)
                    select.select_option(value="CC")
                except Exception:
                    try:
                        page.locator("select").first.select_option(value="CC")
                    except Exception:
                        pass
                page.wait_for_timeout(600)
                # 5) Llenar los 4 inputs del cliente
                _fill_input(page,
                            "input#nuip, input[name='nuip']",
                            cedula)
                page.wait_for_timeout(300)
                _fill_input(page,
                            "input#fechaExpNuip, input[name='fechaExpNuip']",
                            fecha_norm)
                page.wait_for_timeout(300)
                _fill_input(page,
                            "input#nombreEmpresa, "
                            "input[name='nombreEmpresa']",
                            CLIENTE_EMPRESA)
                page.wait_for_timeout(300)
                _fill_input(page,
                            "input#nitEmpresa, input[name='nitEmpresa']",
                            CLIENTE_NIT)
                page.wait_for_timeout(500)
                # 6) Marcar checkbox de términos.
                # El input #cbCondiciones está oculto con
                # `position: absolute; opacity: 0` (patrón
                # Bootstrap custom-control), por lo que .check() y
                # .click() sobre el <label> NO togglean el checked en
                # headless Playwright. Solución robusta: forzar
                # `cb.checked = true` + dispatchEvent('change'). Esto
                # es lo único que activa la habilitación del botón
                # "Consultar".
                try:
                    page.evaluate("""
                        () => {
                          const cb = document.getElementById(
                            'cbCondiciones') ||
                            document.querySelector(
                              "input[type='checkbox']");
                          if (cb && !cb.checked) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('click', {
                              bubbles: true}));
                            cb.dispatchEvent(new Event('change', {
                              bubbles: true}));
                            cb.dispatchEvent(new Event('input', {
                              bubbles: true}));
                          }
                        }
                    """)
                except Exception:
                    pass
                page.wait_for_timeout(300)
                page.wait_for_timeout(500)
                # 7) Screenshot del form lleno (pre-captcha)
                shot_form = _shot_path("policia_inhab_form", cedula)
                try:
                    page.screenshot(path=str(DATA / shot_form),
                                    full_page=False, timeout=15000)
                except Exception:
                    shot_form = None
                results["shot_form"] = shot_form
                results["page_url"] = page.url

                # 8) Loop con hasta MAX_CAPTCHA_ATTEMPTS intentos
                fetch_start = time.time()
                final_html = ""
                summary = "Sin respuesta"
                matched = False
                details: list[dict] = []
                shot_result = None
                last_token = None
                # Apretar el timeout interno del solver
                orig_solver_timeout = getattr(solver_to_use, "timeout", 180)
                for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
                    elapsed = time.time() - fetch_start
                    remaining = HARD_TOTAL_BUDGET_S - elapsed
                    if remaining < RESERVED_FOR_REST_S + 2:
                        results["error"] = (
                            f"Presupuesto agotado ({elapsed:.0f}s) antes de "
                            f"intento {attempt}")
                        break
                    results["attempts"] = attempt
                    # 2captcha con proxy tarda ~40-90s (variable
                    # según carga). Apretamos el timeout interno del
                    # solver a CAPTCHA_SOLVE_TIMEOUT_S para que falle
                    # temprano si el servicio está sobrecargado, en
                    # lugar de esperar el default 180s que arruinaría
                    # el budget.
                    solver_to_use.timeout = min(
                        orig_solver_timeout, CAPTCHA_SOLVE_TIMEOUT_S)
                    # 8a) Resolver reCAPTCHA Enterprise vía 2captcha
                    #     con PROXY RESIDENCIAL (use_webshare=True)
                    try:
                        token = solver_to_use.solve_recaptcha_v2(
                            sitekey=sitekey,
                            page_url=page.url,
                            enterprise=True,
                        )
                    except Exception as e:
                        results["error"] = (
                            f"2captcha excepcion: {type(e).__name__}: {e}")
                        token = None
                    if not token:
                        results["error"] = "2captcha no retorno token"
                        continue
                    last_token = token
                    results["token_len"] = len(token)
                    # 8b) Inyectar token y disparar callback
                    try:
                        inject_info = page.evaluate(INJECT_AND_FIRE_JS, token)
                    except Exception as e:
                        inject_info = {
                            "error": f"{type(e).__name__}: {e}"}
                    results["inject_info"] = inject_info
                    # 8b.1) Esperar a que grecaptcha.getResponse() retorne
                    #       un valor no vacío (estado interno del widget
                    #       = "armed" con el token). Esto cubre el caso
                    #       en que grecaptcha.execute() retornó una Promise
                    #       que se está resolviendo async.
                    getresp_ok = False
                    try:
                        page.wait_for_function(
                            "() => { try { return typeof grecaptcha "
                            "!== 'undefined' && grecaptcha.getResponse "
                            "&& grecaptcha.getResponse().length > 0; }"
                            " catch(e) { return false; } }",
                            timeout=GRECAPTCHA_GETRESPONSE_WAIT_MS)
                        getresp_ok = True
                    except Exception:
                        getresp_ok = False
                    results["getresp_ok"] = getresp_ok
                    page.wait_for_timeout(600)
                    # 8c) Hook network listener ANTES del submit
                    post_seen: list = []
                    resp_seen: list = []
                    def on_req(req):
                        try:
                            if req.method == "POST" and (
                                "inhabilidades.policia" in
                                (req.url or "").lower() or
                                "policia.gov.co:8080" in
                                (req.url or "").lower()
                            ):
                                post_seen.append({
                                    "url": req.url[:200],
                                    "ts": time.time(),
                                })
                        except Exception:
                            pass
                    def on_resp(resp):
                        try:
                            if (resp.request.method == "POST" and
                                ("inhabilidades.policia" in
                                 (resp.url or "").lower() or
                                 "policia.gov.co:8080" in
                                 (resp.url or "").lower())):
                                resp_seen.append({
                                    "url": resp.url[:200],
                                    "status": resp.status,
                                    "ts": time.time(),
                                })
                        except Exception:
                            pass
                    page.on("request", on_req)
                    page.on("response", on_resp)
                    # 8d) Submit en cascada. Ejecutar los 3 métodos
                    # en orden (button.click() → jQuery('#form').submit()
                    # → form.submit()), NO detenerse en el primero.
                    # Razón: en este site con reCAPTCHA Enterprise, el
                    # botón puede estar disabled si grecaptcha.getResponse()
                    # no devolvió nada (el validador jQuery lo bloquea),
                    # pero también podemos forzar form.submit() vía
                    # jQuery, que el validador sí acepta si los
                    # hidden inputs están completos:
                    submit_info = None
                    try:
                        submit_info = page.evaluate("""
                            () => {
                              const out = {methods: []};
                              const btn = document.getElementById(
                                'btnConsultar') ||
                                document.querySelector(
                                  "button[type=submit]");
                              if (!btn) { out.methods.push('no-button');
                                          return out; }
                              const wasDisabled = btn.disabled;
                              out.wasDisabled = wasDisabled;
                              // 1) Habilitar y click
                              try {
                                btn.disabled = false;
                                btn.click();
                                out.methods.push('click-unlocked');
                              } catch (e) {
                                out.methods.push('click-fail: ' + e.message);
                              }
                              // 2) jQuery submit si está disponible
                              try {
                                if (typeof jQuery !== 'undefined' ||
                                    typeof $ !== 'undefined') {
                                  const $f = (window.jQuery ||
                                              window.$)('#frmCons');
                                  if ($f && $f.length) {
                                    $f.submit();
                                    out.methods.push('jquery-submit');
                                  }
                                }
                              } catch (e) {
                                out.methods.push('jquery-fail: ' + e.message);
                              }
                              // 3) form.submit() directo (bypass validator)
                              try {
                                const f = document.getElementById('frmCons') ||
                                          document.querySelector('form');
                                if (f) {
                                  f.submit();
                                  out.methods.push('form-submit');
                                }
                              } catch (e) {
                                out.methods.push('form-fail: ' + e.message);
                              }
                              out.finalBtnDisabled = btn.disabled;
                              return out;
                            }
                        """)
                    except Exception as e:
                        submit_info = {
                            "error": f"{type(e).__name__}: {e}"}
                    results["submit_info"] = submit_info
                    # 8e) Esperar a que la red se calme (nav_changed
                    # o POST visible). Si nada pasa, el submit falló
                    # silenciosamente.
                    nav_changed = False
                    try:
                        url_before = page.url
                        try:
                            page.wait_for_load_state(
                                "networkidle", timeout=5000)
                        except Exception:
                            pass
                        page.wait_for_timeout(500)
                        nav_changed = (page.url != url_before)
                    except Exception:
                        pass
                    # Quitar los listeners
                    try:
                        page.remove_listener("request", on_req)
                        page.remove_listener("response", on_resp)
                    except Exception:
                        pass
                    results["post_seen"] = post_seen[:5]
                    results["resp_seen"] = resp_seen[:5]
                    results["nav_changed"] = nav_changed
                    # 8f) Polling del resultado
                    html = _wait_for_result_text(page)
                    final_html = html
                    # 8g) Parsear resultado
                    summary, matched, details = _parse_resultado(html)
                    results["summary"] = summary
                    if matched or "NO REGISTRA" in summary.upper():
                        # Esperar a que el JS de "Imprimir" + texto
                        # se rendericen visiblemente antes del
                        # screenshot. La página pone un spinner
                        # (#pb_loader) que SI BIEN visibility:hidden
                        # + opacity:0, Playwright lo capturaba
                        # durante la transición; lo removemos
                        # explícitamente para garantizar screenshot
                        # limpio del contenido del resultado.
                        try:
                            page.wait_for_function(
                                "() => !document.body.innerText"
                                ".includes('Cargando')",
                                timeout=5000)
                        except Exception:
                            pass
                        page.wait_for_timeout(1500)
                        # Forzar remoción del loader overlay
                        try:
                            page.evaluate("""
                                () => {
                                  const ld = document.getElementById(
                                    'pb_loader');
                                  if (ld) ld.remove();
                                }
                            """)
                        except Exception:
                            pass
                        shot_result = _shot_path("policia_inhab_result",
                                                 cedula)
                        try:
                            page.screenshot(
                                path=str(DATA / shot_result),
                                full_page=True, timeout=15000)
                        except Exception:
                            shot_result = shot_form
                        results["shot_result"] = shot_result
                        results["details"] = details
                        break
                    # Si llegamos aquí, el captcha probablemente no
                    # marcó correctamente. Screenshot diagnóstico.
                    shot_diag = _shot_path(
                        f"policia_inhab_attempt{attempt}", cedula)
                    try:
                        page.screenshot(path=str(DATA / shot_diag),
                                        full_page=False, timeout=8000)
                    except Exception:
                        pass
                    results[f"shot_attempt{attempt}"] = shot_diag
                    page.wait_for_timeout(1200)

                # Si no detectamos resultado, capturar screenshot del
                # estado final para evidencia
                if shot_result is None:
                    try:
                        page.wait_for_function(
                            "() => !document.body.innerText"
                            ".includes('Cargando')",
                            timeout=3000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1000)
                    # Forzar remoción del loader overlay (ver comentario
                    # en el path matched)
                    try:
                        page.evaluate("""
                            () => {
                              const ld = document.getElementById(
                                'pb_loader');
                              if (ld) ld.remove();
                            }
                        """)
                    except Exception:
                        pass
                    shot_result = _shot_path("policia_inhab_result", cedula)
                    try:
                        page.screenshot(path=str(DATA / shot_result),
                                        full_page=True, timeout=15000)
                    except Exception:
                        shot_result = shot_form
                    results["shot_result"] = shot_result
                    results["details"] = details

                # 9) Restaurar el timeout del solver
                try:
                    solver_to_use.timeout = orig_solver_timeout
                except Exception:
                    pass

                results["final_html_len"] = len(final_html or "")

            _run_in_fresh_browser(_do_inhab)

            # Si NO hubo POST, mejorar el summary con un notice honesto
            summary = results.get("summary", "Sin respuesta")
            nav_changed = results.get("nav_changed")
            post_seen = results.get("post_seen") or []
            token_len = results.get("token_len", 0) or 0
            if (not post_seen) and (not nav_changed) and (
                "no se obtuvo" in summary.lower() or
                "sin respuesta" in summary.lower()
            ):
                if token_len > 0:
                    summary = (
                        "No se obtuvo respuesta del servidor: el token "
                        "reCAPTCHA Enterprise fue inyectado "
                        f"(token_len={token_len}) pero el submit no "
                        "produjo POST visible (submit silenciosamente "
                        "fallido, o el form requiere validacion adicional)."
                    )
                else:
                    summary = (
                        "No se obtuvo respuesta del servidor: el captcha "
                        "reCAPTCHA Enterprise no resolvio (2captcha no "
                        "retorno token). El submit no se intento."
                    )
            elif post_seen and (
                "no se obtuvo" in summary.lower() and
                "re-render" not in summary.lower()
            ):
                summary = (
                    summary.rstrip(".") +
                    f" (POST observed: {len(post_seen)} request(s), "
                    f"nav_changed={nav_changed})."
                )

            if results.get("error") and not results.get("summary"):
                err_details = [{
                    "cedula": cedula,
                    "fecha_exp": fecha_norm,
                    "error": results["error"],
                    "sitekey": (results.get("sitekey") or "")[:24],
                    "captcha_attempts": results.get("attempts", 0),
                    "token_len": results.get("token_len", 0),
                    "inject_info": results.get("inject_info", {}),
                    "submit_info": results.get("submit_info", {}),
                    "post_seen": post_seen,
                    "nav_changed": nav_changed,
                    "solver": results.get("solver"),
                    "proxy_info": results.get("proxy_info"),
                }]
                return Hit(
                    self.name, False, summary,
                    details=err_details,
                    notice=(
                        f"Policia Inhabilidades: {results['error']} "
                        f"(sitekey={results.get('sitekey','?')[:24]}...)"
                    ),
                    captcha_required=True,
                    evidence_urls=[POLICIA_INHAB_URL],
                    download_url=results.get("shot_result")
                                 or results.get("shot_form"),
                    elapsed_s=time.time()-t0,
                )

            matched = "REGISTR" in summary.upper() and \
                      "INHAB" in summary.upper() and \
                      "NO REGISTRA" not in summary.upper()
            details = results.get("details", [])
            return Hit(
                self.name, matched, summary, details,
                evidence_urls=[POLICIA_INHAB_URL],
                download_url=results.get("shot_result")
                             or results.get("shot_form"),
                elapsed_s=time.time()-t0,
            )
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Policia Inhabilidades error: "
                              f"{type(e).__name__}: {e}.",
                       captcha_required=True,
                       evidence_urls=[POLICIA_INHAB_URL],
                       elapsed_s=time.time()-t0)


def _normalize_date(s: str) -> str | None:
    """Acepta varios formatos comunes y devuelve DD/MM/AAAA.
    Retorna None si no se puede parsear."""
    if not s:
        return None
    s = s.strip()
    # DD/MM/AAAA
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        if 1 <= int(d) <= 31 and 1 <= int(mo) <= 12 and 1900 <= int(y) <= 2100:
            return f"{int(d):02d}/{int(m.group(2)):02d}/{int(y):04d}"
    # AAAA-MM-DD (ISO)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(d) <= 31 and 1 <= int(mo) <= 12 and 1900 <= int(y) <= 2100:
            return f"{int(d):02d}/{int(mo):02d}/{int(y):04d}"
    return None
