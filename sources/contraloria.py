"""
sources/contraloria.py — Contraloría General de la República.

URL real con form: https://cfiscal.contraloria.gov.co/Certificados/
                    CertificadoPersonaNatural.aspx
Tiene reCAPTCHA v2 + form con Tipo Documento + Número.

Flujo implementado con Playwright + 2captcha:
  1) Cargar /CertificadoPersonaNatural.aspx con
     a) User-Agent realista (Chrome 120 en Windows)
     b) add_init_script para que `navigator.webdriver` retorne
        `undefined` (anti-bot detection)
  2) Esperar al reCAPTCHA (carga diferida) e identificar sitekey
  3) Seleccionar "Cédula de ciudadanía" en Tipo Documento
  4) Llenar Número de Documento
  5) Screenshot del form lleno
  6) Resolver reCAPTCHA vía 2captcha USANDO PROXY RESIDENCIAL
     (webshare.io) — CRÍTICO: el WAF de la Contraloría detecta
     IP datacenter y rechaza el token server-side. Sin proxy
     residencial, todos los tokens de 2captcha datacenter
     son rechazados.
  7) Inyectar token en TODOS los textareas g-recaptcha-response
     y disparar el callback de grecaptcha v2 explícitamente:
       a) data-callback del .g-recaptcha div
       b) walk recursivo en ___grecaptcha_cfg.clients
       c) grecaptcha.execute(sitekey, {action: 'submit'})
       d) grecaptcha.getResponse() como diagnóstico
  8) Hook de network listener (request/response) para detectar
     silent submit failure.
  9) Submit en cascada:
       - Page_ClientValidate('reqCertificados') (diagnóstico)
       - WebForm_DoPostBackWithOptions (handler real del btnBuscar)
       - __doPostBack (fallback)
       - btn.click() (dispara onclick real)
       - form.submit() (bypass onsubmit)
  10) Esperar networkidle o nav-change (4s). Si nada pasa, el submit
      falló silenciosamente.
  11) Polling de la aparición del resultado (6s).
  12) Screenshot del estado final (post-submit) — este es el
      download_url, NO el screenshot del form pre-submit.
  13) Detectar link de descarga del PDF si existe.
  14) Si el HTML muestra que el sitio re-renderizó el form con un
      nuevo token bft (0cAFcWeA), retry UNA VEZ con un proxy
      webshare diferente (round-robin).
  15) Summary HONESTO: si matched=False, retornamos un summary que
      dice exactamente por qué falló (no se obtuvo respuesta / nuevo
      bft = server rejected captcha / submit silenciosamente fallido),
      nunca el placeholder genérico.
  16) try/finally para restaurar solver.timeout.
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

DATA = Path(__file__).parent.parent / "data"
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


CONTRALORIA_URL = ("https://cfiscal.contraloria.gov.co/Certificados/"
                   "CertificadoPersonaNatural.aspx")
CONTRALORIA_RECAPTCHA_SITEKEY = ("6LcfnjwUAAAAAIyl8ehhox7ZYqLQSVl_w1dmYIle"
                                 )

# User-Agent realista (Chrome 120 en Windows NT 10) — algunos WAFs
# marcan User-Agents de Playwright headless como bot.  Usar el de
# un browser desktop real baja el score de bot-detection.
CONTRALORIA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Tiempos límite. El subproceso de esta fuente tiene 260s
# (runs.py:PER_SOURCE_TIMEOUT["Contraloría…"]). Un solve real de
# reCAPTCHA v2 (CapSolver proxyless / 2captcha con proxy) tarda 40-90s;
# por eso el budget total ahora es amplio y NO mata la fuente a los 70s.
RESULT_TXT_TIMEOUT_MS = 12000    # 12s polling del resultado
RESULT_POLL_MS = 500
MAX_CAPTCHA_ATTEMPTS = 1         # 1 intento de solve por proxy/attempt
# 2026-07-02: aumentado de 2500 a 6000 con justificación. El reCAPTCHA v2
# del portal de contraloría (cfiscal.contraloria.gov.co) necesita ~3-5s
# en conexiones residenciales para:
#   1) cargar el script de recaptcha (api.js?hl=es)
#   2) parsear el div g-recaptcha
#   3) renderizar el iframe anchor
#   4) registrar el global grecaptcha con execute/getResponse
# Con 2500ms el test mostraba iframe visible a 0.3s pero grecaptcha
# ejecutable a veces después; con 6000 hay margen incluso en proxy
# residencial lento. Mantenemos un polling de respaldo para no esperar
# ciegamente.
PAGE_LOAD_WAIT_MS = 6000         # espera de carga de reCAPTCHA
RECAPTCHA_POLL_MS = 300          # paso de polling del reCAPTCHA
RECAPTCHA_MAX_WAIT_S = 12        # máximo de espera si polling no converge
# budget total: 235s dentro del subproceso de 260s (runs.py), con holgura
# para submit + polling + captura del certificado PDF.
HARD_TOTAL_BUDGET_S = 235        # budget total (incluye el solve del captcha)
RESERVED_FOR_REST_S = 20         # lo que dejamos para submit+polling+post

# Máximo de reintentos cuando el sitio re-renderiza el form con un
# nuevo token bft (0cAFcWeA). Cada intento usa un proxy webshare
# diferente (round-robin) Y enruta el navegador por ese proxy. Con 1
# reintento, probamos 2 proxies antes de marcar `captcha_blocked`.
#
# HISTÓRICO: era 3 reintentos (4 proxies). Reducido a 1 (2026-07-02)
# porque el rechazo server-side NO se resuelve cambiando de proxy —
# el problema es que el navegador headless no marca el check verde
# localmente, y eso es independiente de la IP. Probamos 1 vez, si
# falla el submit con re-render del form, paramos y marcamos como
# `captcha_blocked` (consulta manual). Esto ahorra ~3 minutos por
# consulta que iban a fallar de todos modos.
MAX_RERENDER_RETRIES = 1

# JS anti-bot: Playwright headless expone navigator.webdriver=true y
# otras firmas que el WAF de la Contraloría detecta. Inyectar ANTES
# de cualquier navegación con page.add_init_script.
#
# Cubre los vectores de detección más comunes para Chromium-headless:
#   - navigator.webdriver=true → undefined
#   - navigator.plugins.length=0 → 3 (Chrome real tiene 5 por defecto)
#   - navigator.languages no incluye 'en-US' (raro) → ['es-CO','es','en-US']
#   - window.chrome ausente → {runtime: {}} (parece extensión context)
#   - WebGL vendor/renderer "Google Inc. (SwiftShader)" → Intel real
#   - Notification.permission "default" vs "denied"
#   - document.hasFocus() always-true en headless → no podemos spoof
#     fácilmente, pero el sitio probablemente no chequea focus.
ANTI_WEBDRIVER_INIT_SCRIPT = r"""
// 1) navigator.webdriver → undefined
try {
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
} catch (e) {}
// 2) navigator.plugins: Playwright headless reporta 0; Chrome real
//    tiene 5 (PDF, Native Client, etc.). Spoof a 3 plugins.
try {
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = [
        {name: 'PDF Viewer', filename: 'internal-pdf-viewer',
         description: 'Portable Document Format'},
        {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer',
         description: 'Portable Document Format'},
        {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',
         description: 'Portable Document Format'},
      ];
      arr.item = (i) => arr[i];
      arr.namedItem = (n) => arr.find(x => x.name === n);
      arr.length = 3;
      return arr;
    }
  });
} catch (e) {}
// 3) navigator.mimeTypes: similar
try {
  Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
      const arr = [
        {type: 'application/pdf', suffixes: 'pdf',
         description: 'Portable Document Format'},
      ];
      arr.item = (i) => arr[i];
      arr.namedItem = (n) => arr.find(x => x.type === n);
      arr.length = 1;
      return arr;
    }
  });
} catch (e) {}
// 4) navigator.languages: Playwright headless puede tener solo 'en-US';
//    Chrome real devuelve múltiples idiomas según config.
try {
  Object.defineProperty(navigator, 'languages', {
    get: () => ['es-CO', 'es', 'en-US', 'en']
  });
} catch (e) {}
// 5) window.chrome: ausente en Playwright headless; spoof presencia.
try {
  if (!window.chrome) {
    window.chrome = {
      runtime: {OnInstalledReason: {CHROME_UPDATE: 'chrome_update'},
                OnRestartRequiredReason: {APP_UPDATE: 'app_update'},
                PlatformArch: {ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64'},
                RequestUpdateCheckStatus: {THROTTLED: 'throttled',
                                            NO_UPDATE: 'no_update',
                                            UPDATE_AVAILABLE: 'update_available'}},
      app: {isInstalled: false, InstallState: {DISABLED: 'disabled',
                                                INSTALLED: 'installed',
                                                NOT_INSTALLED: 'not_installed'},
            RunningState: {CANNOT_RUN: 'cannot_run',
                            READY_TO_RUN: 'ready_to_run',
                            RUNNING: 'running'}},
      loadTimes: function() { return {requestTime: 0, startLoadTime: 0,
                                      commitLoadTime: 0, finishDocumentLoadTime: 0,
                                      finishLoadTime: 0,
                                      firstPaintTime: 0,
                                      firstPaintAfterLoadTime: 0,
                                      navigationType: 'Other',
                                      wasFetchedViaSpdy: false,
                                      wasNpnNegotiated: false,
                                      npnNegotiatedProtocol: 'unknown',
                                      wasAlternateProtocolAvailable: false,
                                      connectionInfo: 'http/1.1'}; },
      csi: function() { return {startE: 0, onloadT: 0}; },
    };
  }
} catch (e) {}
// 6) WebGL vendor/renderer: headless usa "Google Inc. (SwiftShader)";
//    spoof a Intel (que es lo que un PC Windows real suele tener).
try {
  const origGetParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';   // UNMASKED_VENDOR_WEBGL
    if (p === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
    return origGetParameter.call(this, p);
  };
  if (typeof WebGL2RenderingContext !== 'undefined') {
    const origGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(p) {
      if (p === 37445) return 'Intel Inc.';
      if (p === 37446) return 'Intel Iris OpenGL Engine';
      return origGetParameter2.call(this, p);
    };
  }
} catch (e) {}
// 7) Notification.permission
try {
  if (window.Notification && window.Notification.permission === 'denied') {
    Object.defineProperty(window.Notification, 'permission', {
      get: () => 'default'
    });
  }
} catch (e) {}
// 8) permissions.query override (notifications)
try {
  if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({state: 'default', onchange: null});
      }
      return origQuery(params);
    };
  }
} catch (e) {}
"""


# JS que, dado un token, lo inyecta en el textarea Y busca
# recursivamente el callback en ___grecaptcha_cfg.clients. Devuelve
# un objeto con qué encontró para diagnóstico.
#
# Estrategia exhaustiva (porque la página usa grecaptcha v2 obfuscated
# y el challenge NO valida client-side):
#   1) Inyectar token en TODOS los textareas g-recaptcha-response
#      (hay varios: visible bft y el real g-recaptcha-response).
#   2) Buscar el callback declarado en data-callback del .g-recaptcha
#      div — esto es lo que Google reCAPTCHA v2 ejecuta cuando
#      termina el challenge; suele ser window-level o un global.
#   3) Walk recursivo en ___grecaptcha_cfg.clients buscando
#      {callback, sitekey, callback-name} con sitekey 6L...
#   4) Intentar grecaptcha.execute(sitekey, {action: 'submit'}) que
#      en grecaptcha v2 invisible/programmatic dispara el callback.
#   5) Verificar que grecaptcha.getResponse() devuelva algo (estado
#      interno de grecaptcha) — diagnóstico, no suficiente por sí solo.
INJECT_AND_FIRE_JS = r"""
(token) => {
  const out = {
    textarea: 0, callbackCalled: false, callbacksInvoked: [],
    grecaptchaResponse: '', dataCallbackName: null, sitekey: null,
    executeAttempted: false, error: null,
  };
  try {
    // 1) Poner el token en TODOS los textareas g-recaptcha-response
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
    out.textarea = count;
  } catch (e) { out.error = 'textarea: ' + e.message; }

  // 2) Leer data-callback del div g-recaptcha — Google ejecuta esta
  //    función con el token al terminar el challenge. Suele ser un
  //    string con un nombre de función (a veces window.callbackName).
  try {
    const div = document.querySelector('.g-recaptcha, [data-sitekey]');
    if (div) {
      out.sitekey = div.getAttribute('data-sitekey') || null;
      const cbName = div.getAttribute('data-callback');
      if (cbName) {
        out.dataCallbackName = cbName;
        try {
          // Buscar en window, document, this (poco probable)
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
  } catch (e) { out.error = (out.error || '') + ' / data-callback-attr: ' + e.message; }

  // 3) Walk recursivo en ___grecaptcha_cfg buscando callbacks
  //    con sitekey 6L. (Puede haber más de uno; invocar todos.)
  try {
    function walk(obj, depth, path, found) {
      if (!obj || typeof obj !== 'object' || depth > 12) return;
      if (found.length >= 8) return;
      if (typeof obj.callback === 'function' &&
          typeof obj.sitekey === 'string' &&
          obj.sitekey.startsWith('6L')) {
        found.push({path: path + '.callback', fn: obj.callback});
        return;
      }
      // Algunas versiones ponen la callback en una propiedad con
      // nombre tipo 'J' / 'Hw' / etc. (obfuscated). No hay heurística
      // segura para detectarla sin ejecutar; saltamos.
      if (typeof obj === 'function') return;
      for (const k of Object.keys(obj)) {
        try { walk(obj[k], depth + 1, path + '.' + k, found); }
        catch (e) {}
      }
    }
    if (typeof ___grecaptcha_cfg !== 'undefined') {
      const found = [];
      walk(___grecaptcha_cfg, 0, '___grecaptcha_cfg', found);
      for (const f of found) {
        try {
          f.fn(token);
          out.callbacksInvoked.push('cfg: ' + f.path);
          out.callbackCalled = true;
        } catch (e) {}
      }
    }
  } catch (e) {}

  // 4) grecaptcha.execute(sitekey, {action: 'submit'}) — en v2 invisible
  //    (y en algunos renders programáticos) esto dispara el callback
  //    con un token fresco. Retorna una Promise.
  try {
    if (typeof grecaptcha !== 'undefined' && grecaptcha.execute) {
      const sk = out.sitekey;
      if (sk) {
        out.executeAttempted = true;
        // No podemos await aquí porque evaluate no retorna Promise; pero
        // si execute() retorna una Promise, la página la procesará async.
        // Para v2 checkbox normal (no invisible) execute() no existe o
        // no hace nada — ese caso se maneja via callbacks arriba.
        try {
          const r = grecaptcha.execute(sk, {action: 'submit'});
          if (r && typeof r.then === 'function') {
            // No esperamos; dejamos que corra y que la verificación
            // post-submit confirme.
          }
        } catch (e) {}
      }
    }
  } catch (e) {}

  // 5) Diagnóstico: ¿qué dice grecaptcha.getResponse()?
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


# JS que dispara el form submit. Estrategia múltiple:
#   1) Validar el grupo 'reqCertificados' primero — si no pasa, el
#      WebForm_DoPostBackWithOptions se aborta silenciosamente.
#   2) Setear __EVENTTARGET/__EVENTARGUMENT manualmente.
#   3) Llamar WebForm_DoPostBackWithOptions (lo que hace el onclick
#      real del btnBuscar). Respeta validationGroup=reqCertificados.
#   4) __doPostBack fallback.
#   5) Click programático en el botón (dispara el onclick real
#      que llama WebForm_DoPostBackWithOptions).
#   6) form.submit() directo (bypass onsubmit) — fallback final.
#
# IMPORTANTE: ejecutamos TODOS los métodos en orden, no paramos al
# primero. Razón: en este site el WebForm_DoPostBackWithOptions NO
# dispara POST (la lib MS Ajax minified está rota/cargada parcialmente);
# necesitamos llegar a form.submit() que sí dispara un POST real.
# El primer método que dispare POST será suficiente (los siguientes
# podrían causar errores), pero al ser sincrónico y el network listener
# del lado Python lo va a detectar, no hay riesgo de doble-POST visible.
SUBMIT_FORM_JS = r"""
() => {
  const results = {attempts: [], validatorsPassed: null, methodsCalled: []};
  const btn = document.getElementById('btnBuscar') ||
              document.querySelector("input[name*='btnBuscar']");
  if (!btn) {
    results.method = 'no-button';
    return results;
  }
  const et = document.getElementById('__EVENTTARGET');
  const ea = document.getElementById('__EVENTARGUMENT');
  // 1) Pre-validar el grupo (Page_ClientValidate)
  try {
    if (typeof Page_ClientValidate === 'function') {
      results.validatorsPassed = Page_ClientValidate('reqCertificados');
    }
  } catch (e) {}
  // 2) Setear __EVENTTARGET/__EVENTARGUMENT manualmente
  try {
    if (et) et.value = btn.name;
    if (ea) ea.value = '';
  } catch (e) {}

  // 3) WebForm_DoPostBackWithOptions — handler real del onclick.
  try {
    if (typeof WebForm_DoPostBackWithOptions === 'function') {
      WebForm_DoPostBackWithOptions(
        new WebForm_PostBackOptions(
          btn.name, "", true, "reqCertificados", "", false, false));
      results.attempts.push('WebForm_DoPostBackWithOptions');
      results.methodsCalled.push('WebForm_DoPostBackWithOptions');
    }
  } catch (e) {
    results.attempts.push('WebForm_DoPostBackWithOptions: ' + e.message);
  }
  // 4) __doPostBack fallback
  try {
    if (typeof __doPostBack === 'function') {
      __doPostBack(btn.name, '');
      results.attempts.push('__doPostBack');
      results.methodsCalled.push('__doPostBack');
    }
  } catch (e) {
    results.attempts.push('__doPostBack: ' + e.message);
  }
  // 5) Click programático en el botón — dispara onclick real que
  //    llama WebForm_DoPostBackWithOptions
  try {
    btn.click();
    results.attempts.push('click');
    results.methodsCalled.push('click');
  } catch (e) {
    results.attempts.push('click: ' + e.message);
  }
  // 6) form.submit() directo (bypass onsubmit) — ÚLTIMO recurso.
  //    En este site es el único que dispara POST real.
  try {
    const f = document.querySelector('form');
    if (f) {
      f.submit();
      results.attempts.push('form.submit');
      results.methodsCalled.push('form.submit');
    }
  } catch (e) {
    results.attempts.push('form.submit: ' + e.message);
  }
  results.method = results.methodsCalled.join('+') || 'none';
  return results;
}
"""


# JS para extraer el sitekey del HTML cargado, con varios métodos
DETECT_SITEKEY_JS = r"""
() => {
  // 1) data-sitekey
  const div = document.querySelector('[data-sitekey]');
  if (div) return div.getAttribute('data-sitekey');
  // 2) iframe recaptcha -> ?k=
  const ifr = document.querySelector('iframe[src*="recaptcha"]');
  if (ifr) {
    const m = ifr.src.match(/[?&]k=([^&]+)/);
    if (m) return m[1];
  }
  // 3) script con src google.com/recaptcha -> ?render=
  const scripts = document.querySelectorAll('script[src*="recaptcha"]');
  for (const s of scripts) {
    const m = s.src.match(/[?&]render=([^&]+)/);
    if (m) return m[1];
  }
  // 4) buscar texto 6L...
  const html = document.documentElement.outerHTML;
  const m = html.match(/(6L[A-Za-z0-9_-]{30,60})/);
  if (m) return m[1];
  return null;
}
"""


# ---------- Proxy round-robin para WAF bypass ----------

# Cache en proceso: evita golpear el endpoint de webshare en cada
# retry (la API rate-limitea con HTTP 429 si se le llama más de ~1/s).
_WEBSHARE_CACHE: list[str] | None = None
_WEBSHARE_CACHE_TS: float = 0.0
_WEBSHARE_CACHE_TTL_S = 600   # 10 min — la lista de proxies rotativos
                              # no cambia frecuentemente


def _list_webshare_proxies(force: bool = False) -> list[str]:
    """Devuelve la lista de proxies webshare en formato
    http://user:pass@ip:port. Delega en solvers/webshare.py (API v2 con
    token)."""
    try:
        from solvers.webshare import list_proxies
        return list_proxies(force=force)
    except Exception as e:
        print(f"  [contraloria] webshare list fetch fail: {e}", flush=True)
        return []


def _pick_webshare_proxy(avoid: str | None = None) -> str | None:
    """Elige un proxy webshare al azar, intentando NO repetir el
    `avoid` (que es el proxy que ya usamos y fue rechazado)."""
    import random
    proxies = _list_webshare_proxies()
    if not proxies:
        return None
    if avoid and len(proxies) > 1:
        # Filtrar el avoid, luego elegir uno al azar
        pool = [p for p in proxies if p != avoid]
        if not pool:
            pool = proxies
        return random.choice(pool)
    return random.choice(proxies)


def _build_webshare_solver(injected_solver=None,
                          initial_proxy: str | None = None):
    """Construye un TwoCaptchaSolver LOCAL con proxy webshare, desde
    config.yaml. NO confiamos en el `solver` que llega como argumento:
    la Contraloría SIEMPRE necesita proxy residencial, y queremos
    tener control directo sobre el round-robin entre reintentos.

    Patrón análogo a procuraduria.py:_get_trivia_solver() — siempre
    construir localmente con la config del proyecto.

    Si `injected_solver` es un TwoCaptchaSolver que ya tiene un
    proxy webshare configurado (típico: viene de get_default_solver()
    cuando `captcha.twocaptcha.proxy.enabled=true`), REUTILIZAMOS
    su proxy en lugar de hacer una nueva llamada a la API de webshare.
    Esto evita el 429 rate-limit cuando get_default_solver() y este
    helper se llaman casi simultáneamente.
    """
    try:
        from config import load_config
        from solvers.factory import build_chain
        # Reutilizar proxy del injected_solver si está disponible
        if (injected_solver is not None and
                getattr(injected_solver, "_proxy_url", None)):
            initial_proxy = injected_solver._proxy_url
        cfg = load_config()
        tc_cfg = cfg.get("captcha", {}).get("twocaptcha", {})
        # Cadena con respaldo: CapSolver (proxyless) → 2captcha (con proxy
        # webshare). Si un solver falla, se usa el otro automáticamente.
        solver = build_chain(cfg, use_proxy=True,
                             timeout=tc_cfg.get("default_timeout", 180))
        # Si aún no tenemos un proxy, intentar obtener uno (cache)
        if initial_proxy is None:
            initial_proxy = _pick_webshare_proxy()
        if initial_proxy:
            solver.set_proxy(initial_proxy)
        # Marcar para diagnóstico
        solver._webshare_enabled = bool(initial_proxy)
        solver._webshare_proxy = initial_proxy
        return solver
    except Exception as e:
        print(f"  [contraloria] build solver fail: {e}", flush=True)
        return None


def _rotate_solver_proxy(solver, avoid: str | None) -> str | None:
    """Fuerza al solver a usar un proxy webshare diferente (para
    retry tras re-render del form). Devuelve el nuevo proxy o None
    si no se pudo rotar."""
    new_proxy = _pick_webshare_proxy(avoid=avoid)
    if not new_proxy:
        return None
    # TwoCaptchaSolver usa self._proxy_url internamente. Es privado
    # pero estable. Si en el futuro cambia, ajustar acá.
    try:
        solver._proxy_url = new_proxy
        solver._webshare_proxy = new_proxy
    except Exception:
        return None
    return new_proxy


def _playwright_proxy_dict(proxy_url: str | None) -> dict | None:
    """Convierte un proxy `http://user:pass@ip:port` (webshare) al formato
    que espera Playwright: {"server": "http://ip:port", "username", "password"}.
    Devuelve None si no hay proxy."""
    if not proxy_url:
        return None
    try:
        from urllib.parse import urlparse
        u = urlparse(proxy_url)
        if not u.hostname or not u.port:
            return None
        d = {"server": f"http://{u.hostname}:{u.port}"}
        if u.username:
            d["username"] = u.username
        if u.password:
            d["password"] = u.password
        return d
    except Exception:
        return None


def _run_in_fresh_browser(fn, *, proxy: str | None = None):
    """Crea un Playwright sync NUEVO por llamada, con:
      - User-Agent realista (Chrome 120 en Windows)
      - add_init_script: navigator.webdriver = undefined (anti-bot)
      - PROXY RESIDENCIAL webshare aplicado al browser context.

    El WAF (Imperva/Incapsula) de la Contraloría rechaza el submit desde IP
    datacenter aunque el token 2captcha sea válido: el server ve la IP del
    NAVEGADOR, no la del solver. Por eso enrutamos el navegador entero por el
    proxy residencial. Se usa `ignore_https_errors=True` porque algunos proxies
    webshare terminan TLS en un endpoint compartido y el cert puede no validar
    (ERR_CERT_AUTHORITY_INVALID) — sin esto, el goto fallaba. Si el proxy es
    None (no hay proxies disponibles), corre directo como fallback.
    """
    from playwright.sync_api import sync_playwright
    proxy_dict = _playwright_proxy_dict(proxy)
    pw = sync_playwright().start()
    try:
        chrome_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
        ]
        launch_kwargs = {"headless": True, "args": chrome_args}
        if proxy_dict:
            # Proxy a nivel de browser: aplica a TODAS las conexiones.
            launch_kwargs["proxy"] = proxy_dict
        browser = pw.chromium.launch(**launch_kwargs)
        try:
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=CONTRALORIA_USER_AGENT,
                locale="es-CO",
                ignore_https_errors=True,
            )
            try:
                # Anti-webdriver detection: ANTES de cualquier goto,
                # inyectar el script en cada navegación nueva.
                ctx.add_init_script(ANTI_WEBDRIVER_INIT_SCRIPT)
                page = ctx.new_page()
                # Cabeceras HTTP extra (realistas)
                try:
                    page.set_extra_http_headers({
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;"
                            "q=0.9,image/avif,image/webp,*/*;q=0.8"
                        ),
                        "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    })
                except Exception:
                    pass
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


def _wait_for_recaptcha_ready(page, max_wait_s: float = RECAPTCHA_MAX_WAIT_S) -> bool:
    """Espera con polling a que el reCAPTCHA v2 esté completamente cargado
    en la página: iframe anchor visible, global `grecaptcha` con
    `execute` y `getResponse`, y `___grecaptcha_cfg` registrado.

    Antes (versión 2026-06): se hacía `page.wait_for_timeout(2500)` ciego.
    Eso fallaba con proxies residenciales lentos donde el reCAPTCHA tarda
    4-6s en estar completamente operativo. Ahora polling explícito con
    paso de 300ms y timeout 12s.

    Returns True si está listo, False si timeout.
    """
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        try:
            ready = page.evaluate("""() => {
                // 1) Iframe anchor presente
                const ifr = document.querySelector('iframe[title="reCAPTCHA"]');
                // 2) Global grecaptcha con execute + getResponse
                const api = (typeof grecaptcha !== 'undefined') &&
                            grecaptcha &&
                            (typeof grecaptcha.execute === 'function') &&
                            (typeof grecaptcha.getResponse === 'function');
                // 3) Textarea g-recaptcha-response (el form lo envía en POST)
                const ta = document.querySelector('#g-recaptcha-response');
                return !!(ifr && api && ta);
            }""")
            if ready:
                return True
        except Exception:
            pass
        page.wait_for_timeout(RECAPTCHA_POLL_MS)
    return False


def _wait_for_jumbotron_encuesta(page, max_wait_s: float = 8.0) -> bool:
    """Espera a que aparezca el jumbotron de la encuesta de satisfacción.

    Después de validar el captcha y hacer click en btnBuscar, el sitio
    (cfiscal.contraloria.gov.co) muestra el jumbotronEncuesta después
    de 3 segundos (jQuery show(3000) en el handler de click). Si el
    jumbotron aparece, hay que completar la encuesta (3 preguntas SI/NO
    + click en Button1) ANTES de que el form procese realmente.

    Esto NO bloquea el submit (los validators de la encuesta están en
    `reqModalEncuesta`, no en `reqCertificados`), pero la página puede
    re-renderizar el form si no se completa. Lo manejamos por robustez.

    Returns True si apareció, False si timeout (caso normal sin
    re-render del WAF).
    """
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        try:
            visible = page.evaluate("""() => {
                const j = document.getElementById('jumbotronEncuesta');
                if (!j) return false;
                // Bootstrap hidden: atributo hidden = true
                if (j.hasAttribute('hidden')) return false;
                const cs = window.getComputedStyle(j);
                return cs && cs.display !== 'none' && cs.visibility !== 'hidden';
            }""")
            if visible:
                return True
        except Exception:
            pass
        page.wait_for_timeout(250)
    return False


def _completar_encuesta_si_aparece(page) -> bool:
    """Si el jumbotron de la encuesta está visible, completarla con
    respuestas neutras (3 SI) y enviar el modal.

    Returns True si se completó la encuesta, False si no estaba visible.
    """
    try:
        visible = page.evaluate("""() => {
            const j = document.getElementById('jumbotronEncuesta');
            if (!j) return false;
            if (j.hasAttribute('hidden')) return false;
            const cs = window.getComputedStyle(j);
            return cs && cs.display !== 'none' && cs.visibility !== 'hidden';
        }""")
        if not visible:
            return False
        # 1) Click en "Realizar encuesta" para abrir el modal
        try:
            page.locator("#btnAbrirEncuesta").first.click(timeout=4000)
        except Exception:
            try:
                page.locator("input[value='Realizar encuesta']").first.click(
                    timeout=3000)
            except Exception:
                return False
        # 2) Esperar a que el modal esté visible
        deadline = time.time() + 4
        modal_visible = False
        while time.time() < deadline:
            try:
                modal_visible = page.evaluate("""() => {
                    const m = document.getElementById('modalEncuesta');
                    if (!m) return false;
                    const cs = window.getComputedStyle(m);
                    return cs && (cs.display === 'block' || cs.opacity === '0.5');
                }""")
                if modal_visible:
                    break
            except Exception:
                pass
            page.wait_for_timeout(200)
        if not modal_visible:
            return False
        # 3) Responder las 3 preguntas con SI (True)
        for radio_id in ("rbdExpectativa_0", "rbdOportunidad_0", "rbdUtilidad_0"):
            try:
                page.locator(f"#{radio_id}").first.check(timeout=3000, force=True)
            except Exception:
                # Fallback via JS
                page.evaluate(f"""() => {{
                    const r = document.getElementById('{radio_id}');
                    if (r) {{
                        r.checked = true;
                        r.dispatchEvent(new Event('change', {{bubbles: true}}));
                        r.dispatchEvent(new Event('click', {{bubbles: true}}));
                    }}
                }}""")
        page.wait_for_timeout(300)
        # 4) Click en "Enviar" (Button1)
        try:
            page.locator("#Button1").first.click(timeout=4000)
        except Exception:
            try:
                page.locator("input[value='Enviar']").first.click(timeout=3000)
            except Exception:
                return False
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def _wait_for_result_text(page, timeout_ms=RESULT_TXT_TIMEOUT_MS):
    """Polling: la página contiene 'NO REGISTRA' / 'REGISTR' / 'FISCAL'.
    Retorna el HTML una vez detectado, o el último HTML si timeout."""
    deadline = time.time() + (timeout_ms / 1000.0)
    last_html = ""
    while time.time() < deadline:
        try:
            last_html = page.content()
        except Exception:
            last_html = ""
        text = re.sub(r"<[^>]+>", " ", last_html)
        text = re.sub(r"\s+", " ", text).upper()
        # Texto normalizado (sin acentos) para tolerar encoding
        text_ascii = text.replace("Í", "I").replace("É", "E") \
                         .replace("Á", "A").replace("Ó", "O") \
                         .replace("Ú", "U")
        if "NO REGISTRA" in text_ascii:
            return last_html
        if "REGISTR" in text_ascii and "FISCAL" in text_ascii:
            return last_html
        # Heurística ASP.NET: label de resultado, mensaje del panel
        if "lblResultado" in last_html or "Resultado" in text:
            return last_html
        time.sleep(RESULT_POLL_MS / 1000.0)
    return last_html


def _is_rerender(html: str) -> bool:
    """Heurística: el servidor re-renderizó el form (con o sin un
    nuevo token bft visible). Indica que el WAF rechazó el token
    2captcha y debemos reintentar con un proxy diferente.

    Detecta el re-render con DOS señales independientes:
      1) Token bft visible (0cAFcWeA) — caso clásico, a veces presente.
      2) El form completo está visible (ddlTipoDocumento + g-recaptcha)
         SIN texto de resultado ('NO REGISTRA' / 'REGISTR' / 'lblResultado')
         — significa que el server nos devolvió un form fresco, no
         un resultado. Si el submit fue procesado (POST observado),
         esto es un re-render del WAF.
    """
    if not html:
        return False
    text_clean = re.sub(r"<[^>]+>", " ", html).upper()
    # Señal 1: token bft explícito
    if "0CAFCHE" in text_clean or re.search(r"0C[A-Z0-9_-]{20,}", html):
        return True
    # Señal 2: form completo visible + sin resultado + g-recaptcha
    # presente. Esta es la heurística fuerte: si la respuesta del
    # server tiene el form (ddlTipoDocumento, txtNumeroDocumento,
    # g-recaptcha) PERO NO tiene texto de resultado fiscal, entonces
    # el server re-renderizó el form.
    has_form = ("ddlTipoDocumento" in html and
                "txtNumeroDocumento" in html and
                ("g-recaptcha" in html or "recaptcha" in html.lower()))
    has_resultado = ("NO REGISTRA" in text_clean or
                     "REGISTR" in text_clean or
                     "lbLResultado" in html or
                     "Resultado" in text_clean or
                     "lblResultado" in html)
    if has_form and not has_resultado:
        return True
    return False


def _parse_resultado(html: str) -> tuple[str, bool, list[dict]]:
    """Devuelve (summary, matched, details) según el HTML del resultado.

    Summary es HONESTO: si el HTML no contiene un resultado fiscal
    claro, retornamos un summary que dice 'No se obtuvo respuesta del
    servidor' (con posible causa) en vez del placeholder genérico
    que oculta el fallo.
    """
    if not html:
        return ("No se obtuvo respuesta del servidor "
                "(HTML vacío tras submit).", False, [])
    text = re.sub(r"<[^>]+>", " ", html)
    text_clean = re.sub(r"\s+", " ", text).upper()
    text_clean = text_clean.replace("Í", "I").replace("É", "E") \
                           .replace("Á", "A").replace("Ó", "O") \
                           .replace("Ú", "U")
    if "NO REGISTRA" in text_clean and "FISCAL" in text_clean:
        return ("NO REGISTRA responsabilidad fiscal", False, [])
    if "NO REGISTRA" in text_clean and "RESPONSABILIDAD" in text_clean:
        return ("NO REGISTRA responsabilidad fiscal", False, [])
    if "NO REGISTRA" in text_clean:
        return ("NO REGISTRA responsabilidad fiscal", False, [])
    if "REGISTR" in text_clean and (
            "FISCAL" in text_clean or "RESPONSABILIDAD" in text_clean):
        details = []
        for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S):
            cells = re.findall(r'<td[^>]*>([^<]+)</td>', m.group(1))
            if not cells:
                continue
            joined = " | ".join(c.strip() for c in cells)
            if 20 < len(joined) < 300:
                details.append({"fila": joined})
                if len(details) >= 5:
                    break
        return (f"SÍ REGISTRA responsabilidad fiscal "
                f"({len(details)} filas)", True, details)
    # Si el HTML tiene un nuevo bft token (con prefijo 0cAFcWeA), el
    # server re-renderizó el form — significa que el submit fue
    # procesado pero el captcha fue rechazado server-side.
    if _is_rerender(html):
        return ("No se obtuvo respuesta del servidor: el sitio "
                "re-renderizó el formulario con un nuevo token reCAPTCHA "
                "(posible rechazo server-side del token 2captcha).",
                False, [])
    # Default honesto: NO usamos el placeholder genérico porque
    # oculta el fallo. Decimos exactamente lo que pasó.
    return ("No se obtuvo respuesta del servidor (el HTML no "
            "contiene 'NO REGISTRA' ni 'REGISTR + FISCAL' tras el "
            "submit; ver screenshot).", False, [])


@register
class ContraloriaSource:
    name = "Contraloría General — Responsabilidad Fiscal"
    source_url = CONTRALORIA_URL
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "recaptcha_v2"

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

        # SIEMPRE construimos nuestro propio TwoCaptchaSolver con
        # proxy webshare (análogo a procuraduria.py:_get_trivia_solver).
        # El solver inyectado puede ser NoOp/Anthropic; no nos sirve.
        # La Contraloría SIEMPRE necesita proxy residencial para
        # bypass del WAF datacenter detection.
        local_solver = _build_webshare_solver(injected_solver=solver)
        if not local_solver or not local_solver.is_available():
            return Hit(self.name, False, "",
                       notice="No se pudo inicializar TwoCaptchaSolver con "
                              "proxy webshare. Revisar config.yaml "
                              "(captcha.twocaptcha.api_key, "
                              "captcha.twocaptcha.proxy.enabled).",
                       captcha_required=True,
                       evidence_urls=[CONTRALORIA_URL],
                       elapsed_s=time.time()-t0)

        # Guard por-intento. DEBE ser mayor que el tiempo de un solve real
        # de reCAPTCHA (40-90s con CapSolver/2captcha) + carga de página +
        # submit; si no, el watchdog mata el intento ANTES de que el token
        # llegue (este era el bug: 25s abortaba el solve). Con 110s por
        # intento y 2 intentos caben en HARD_TOTAL_BUDGET_S=150 y en el
        # subproceso de 170s (runs.py).
        # Por intento: carga (más lenta vía proxy residencial) + solve de
        # reCAPTCHA (40-90s) + submit + polling. 110s da margen realista;
        # el guard de reloj limita el nº de intentos dentro del budget total.
        SOFT_TIMEOUT = 110

        # Estado compartido entre watchdog y _do_contraloria
        state: dict = {"attempts": 0, "error": None, "soft_timeout_hit": False,
                       "rerenders": 0, "proxies_used": []}

        def _do_contraloria(page):
            page.set_default_timeout(30000)
            # 1) Cargar página
            page.goto(CONTRALORIA_URL, wait_until="domcontentloaded",
                      timeout=30000)
            # 2) Esperar a que cargue reCAPTCHA (carga diferida). Antes
            #    era un wait_for_timeout(2500) ciego; ahora polling
            #    explícito hasta que el iframe + grecaptcha global + textarea
            #    estén listos (típico 1-3s; con proxy residencial puede ser
            #    5-8s; el cap es RECAPTCHA_MAX_WAIT_S=12s).
            t_recaptcha_wait = time.time()
            if not _wait_for_recaptcha_ready(page,
                                              max_wait_s=RECAPTCHA_MAX_WAIT_S):
                state["warning"] = (
                    f"reCAPTCHA no terminó de cargar en "
                    f"{RECAPTCHA_MAX_WAIT_S}s; continuando con lo que haya.")
            state["recaptcha_wait_s"] = time.time() - t_recaptcha_wait
            # 3) Detectar sitekey (varios métodos)
            sitekey = None
            try:
                sitekey = page.evaluate(DETECT_SITEKEY_JS)
            except Exception:
                pass
            if not sitekey:
                html = page.content()
                m = re.search(
                    r'data-sitekey=["\']([A-Za-z0-9_-]{20,80})', html)
                if m:
                    sitekey = m.group(1)
            if not sitekey:
                m = re.search(r'(6L[A-Za-z0-9_-]{30,60})', html or "")
                if m:
                    sitekey = m.group(1)
            if not sitekey:
                sitekey = CONTRALORIA_RECAPTCHA_SITEKEY
            state["sitekey"] = sitekey
            # 4) Seleccionar "Cédula de ciudadanía" en Tipo Documento
            try:
                select = page.locator(
                    "select#ddlTipoDocumento").first
                select.wait_for(timeout=8000)
                select.select_option(value="CC")
            except Exception:
                try:
                    page.locator("select").first.select_option(value="CC")
                except Exception:
                    pass
            # 5) Llenar Número de Documento
            page.wait_for_timeout(800)
            try:
                inp = page.locator(
                    "input#txtNumeroDocumento").first
                inp.wait_for(timeout=8000)
                inp.fill(cedula, timeout=5000)
            except Exception:
                page.evaluate(f"""
                    () => {{
                      const inp = document.getElementById(
                        'txtNumeroDocumento');
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
            page.wait_for_timeout(800)
            # 6) Screenshot del form lleno
            shot_form = _shot_path("contraloria_form", cedula)
            page.screenshot(path=str(DATA / shot_form),
                            full_page=False, timeout=15000)
            state["shot_form"] = shot_form
            state["page_url"] = page.url

            # 7) Loop con hasta MAX_CAPTCHA_ATTEMPTS intentos de captcha
            #    Respeta el budget total para no exceder el timeout duro
            #    de 70s del runs.py.
            fetch_start = time.time()
            final_html = ""
            summary = "Sin respuesta"
            matched = False
            details: list[dict] = []
            shot_result = None
            last_token = None
            # Apretar el timeout interno del solver para caber en budget.
            # Política: dejar que 2captcha use el mayor tiempo posible
            # dentro del budget (dejando RESERVED_FOR_REST_S para
            # submit+polling+post).
            orig_solver_timeout = getattr(local_solver, "timeout", 180)
            current_proxy = getattr(local_solver, "_webshare_proxy", None)
            if current_proxy:
                state["proxies_used"].append(current_proxy)
            for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
                elapsed = time.time() - fetch_start
                remaining = HARD_TOTAL_BUDGET_S - elapsed
                if remaining < RESERVED_FOR_REST_S + 2:
                    state["error"] = (
                        f"Presupuesto agotado ({elapsed:.0f}s) antes de "
                        f"intento {attempt}")
                    break
                state["attempts"] = attempt
                # Re-apretar el timeout del solver en cada intento
                local_solver.timeout = min(
                    orig_solver_timeout,
                    max(10, remaining - RESERVED_FOR_REST_S))
                # 7a) Resolver reCAPTCHA vía 2captcha (CON PROXY)
                try:
                    token = local_solver.solve_recaptcha_v2(
                        sitekey=sitekey,
                        page_url=page.url,
                        enterprise=False,
                    )
                except Exception as e:
                    state["error"] = (
                        f"2captcha excepción: {type(e).__name__}: {e}")
                    token = None
                if not token:
                    state["error"] = "2captcha no retornó token"
                    # Reintentar con un poco de pausa
                    page.wait_for_timeout(1500)
                    continue
                last_token = token
                state["token_len"] = len(token)
                # 7b) Inyectar token y disparar callback
                try:
                    inject_info = page.evaluate(INJECT_AND_FIRE_JS, token)
                except Exception as e:
                    inject_info = {
                        "error": f"{type(e).__name__}: {e}"}
                state["inject_info"] = inject_info
                page.wait_for_timeout(600)
                # 7c) Hook network listener ANTES del submit para
                #     detectar si el POST sale (silent fail diagnostic).
                post_seen: list = []
                resp_seen: list = []
                def on_req(req):
                    try:
                        if req.method == "POST" and (
                            "contraloria" in (req.url or "").lower() or
                            "Certificado" in (req.url or "")
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
                            ("contraloria" in (resp.url or "").lower() or
                             "Certificado" in (resp.url or ""))):
                            resp_seen.append({
                                "url": resp.url[:200],
                                "status": resp.status,
                                "ts": time.time(),
                            })
                    except Exception:
                        pass
                page.on("request", on_req)
                page.on("response", on_resp)
                # 7d) Submit: cascada WebForm_DoPostBackWithOptions →
                #     __doPostBack → click → form.submit
                try:
                    submit_info = page.evaluate(SUBMIT_FORM_JS)
                except Exception as e:
                    submit_info = {
                        "error": f"{type(e).__name__}: {e}"}
                state["submit_info"] = submit_info
                # 7e) Esperar a que la red se calme (response a POST
                #     o cambio de URL). Si nada pasa en 4s, el submit
                #     falló silenciosamente.
                nav_changed = False
                try:
                    url_before = page.url
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=4000)
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
                state["post_seen"] = post_seen[:5]
                state["resp_seen"] = resp_seen[:5]
                state["nav_changed"] = nav_changed
                # 7e.bis) Manejo de la ENCUESTA de satisfacción. Después
                # de validar el captcha y hacer click en btnBuscar, el sitio
                # muestra `jumbotronEncuesta` (jQuery show(3000)). Si
                # aparece, completarla automáticamente: 3 preguntas SI/NO
                # + click en Realizar encuesta → modal → Enviar. Si el
                # modal se procesa, se dispara un nuevo POST que sí
                # devuelve el resultado fiscal.
                encuesta_completada = False
                try:
                    if _wait_for_jumbotron_encuesta(page, max_wait_s=6.0):
                        state["encuesta_visible"] = True
                        encuesta_completada = _completar_encuesta_si_aparece(
                            page)
                        state["encuesta_completada"] = encuesta_completada
                        if encuesta_completada:
                            # Esperar un poco a que el POST de la encuesta
                            # se procese y la página de resultado cargue.
                            try:
                                page.wait_for_load_state(
                                    "networkidle", timeout=6000)
                            except Exception:
                                pass
                            page.wait_for_timeout(800)
                except Exception as e:
                    state["encuesta_error"] = (
                        f"{type(e).__name__}: {e}")
                # 7f) Polling del resultado
                html = _wait_for_result_text(page)
                final_html = html
                # 7g) Parsear resultado
                summary, matched, details = _parse_resultado(html)
                state["summary"] = summary
                # Consideramos éxito si matched=True o si dice
                # explícitamente "NO REGISTRA"
                if matched or "NO REGISTRA" in summary.upper():
                    # Tomar screenshot del resultado
                    shot_result = _shot_path("contraloria_result",
                                             cedula)
                    try:
                        page.screenshot(
                            path=str(DATA / shot_result),
                            full_page=True, timeout=10000)
                    except Exception:
                        shot_result = shot_form
                    state["shot_result"] = shot_result
                    state["details"] = details
                    break
                # Si llegamos aquí, el captcha probablemente no
                # marcó correctamente. Screenshot diagnóstico.
                shot_diag = _shot_path(
                    f"contraloria_attempt{attempt}", cedula)
                try:
                    page.screenshot(path=str(DATA / shot_diag),
                                    full_page=False, timeout=8000)
                except Exception:
                    pass
                state[f"shot_attempt{attempt}"] = shot_diag
                # Pequeña pausa antes de reintentar
                page.wait_for_timeout(1200)

            # Si no detectamos "NO REGISTRA" / "REGISTR" pero tenemos
            # un token, igual capturamos el screenshot del estado
            # final para evidencia.
            if shot_result is None:
                shot_result = _shot_path("contraloria_result", cedula)
                try:
                    page.screenshot(path=str(DATA / shot_result),
                                    full_page=True, timeout=10000)
                except Exception:
                    shot_result = shot_form
                state["shot_result"] = shot_result
                state["details"] = details

            # 8) Detectar link de descarga PDF y DESCARGAR el certificado.
            #    La Contraloría emite un certificado de responsabilidad fiscal
            #    en PDF; si logramos pasar el WAF, lo capturamos para embeberlo
            #    como evidencia (el reporte rasteriza el PDF, no el screenshot).
            try:
                pdf_info = page.evaluate("""
                    () => {
                      const links = Array.from(
                        document.querySelectorAll('a'));
                      for (const a of links) {
                        const href = (a.href || '').toLowerCase();
                        const text = (a.innerText || '').toLowerCase();
                        if (href.endsWith('.pdf') ||
                            'pdf' in (a.getAttribute('onclick') || '') ||
                            text.includes('pdf') ||
                            text.includes('certificado') ||
                            text.includes('descargar') ||
                            text.includes('download')) {
                          return {href: a.href, text: a.innerText.trim()};
                        }
                      }
                      return null;
                    }
                """)
                if pdf_info and pdf_info.get("href"):
                    state["pdf_link"] = pdf_info
                    href = pdf_info.get("href") or ""
                    # Descargar el PDF con las cookies del contexto (mismo
                    # proxy/sesión que pasó el WAF).
                    if href.lower().endswith(".pdf") or "certificado" in href.lower():
                        try:
                            (DATA / "certs").mkdir(parents=True, exist_ok=True)
                            resp = page.context.request.get(href, timeout=20000)
                            if resp and resp.ok:
                                body = resp.body()
                                if body[:4] == b"%PDF" and len(body) > 3000:
                                    ts1 = int(time.time())
                                    pdf_fname = f"contraloria_{cedula}_{ts1}.pdf"
                                    (DATA / "certs" / pdf_fname).write_bytes(body)
                                    state["pdf_path"] = f"certs/{pdf_fname}"
                        except Exception:
                            pass
            except Exception:
                pass

            state["final_html_len"] = len(final_html or "")
            # Restaurar el timeout del solver
            try:
                local_solver.timeout = orig_solver_timeout
            except Exception:
                pass

        # Watchdog por-intento: si después de SOFT_TIMEOUT segundos
        # el browser del intento actual sigue activo, marcar
        # soft_timeout_hit PARA ESE INTENTO. Se reinicia al inicio
        # de cada retry. Esto permite que el total sea 25s × N intentos.
        # NO podemos matar el browser desde otro thread; el run externo
        # tiene su propio PER_TIMEOUT=70s.
        import threading

        def _run_attempt_with_watchdog(retry_idx: int,
                                        proxy_for_browser):
            """Un intento completo: crea watchdog de SOFT_TIMEOUT, corre
            _do_contraloria en un browser fresco. Devuelve True si
            debemos parar todo (soft_timeout_hit, éxito, o re-render
            agotado)."""
            attempt_watchdog = threading.Event()

            def _attempt_watchdog_killer():
                if not attempt_watchdog.wait(SOFT_TIMEOUT):
                    state["error"] = (
                        f"Timeout {SOFT_TIMEOUT}s en intento {retry_idx + 1}: "
                        f"la consulta a la Contraloría no avanzó a un "
                        f"resultado. Posible rechazo server-side del WAF "
                        f"o captcha no resuelto. Proxies usados: "
                        f"{len(state.get('proxies_used', []))}."
                    )
                    state["soft_timeout_hit"] = True

            t = threading.Thread(target=_attempt_watchdog_killer,
                                 daemon=True)
            t.start()
            # Reset state para este intento
            state["attempts"] = 0
            state["error"] = None
            state["shot_result"] = None
            state["post_seen"] = []
            state["resp_seen"] = []
            state["nav_changed"] = False
            state["summary"] = None
            state["details"] = []
            state["soft_timeout_hit"] = False  # limpiar del intento previo
            try:
                _run_in_fresh_browser(_do_contraloria,
                                      proxy=proxy_for_browser)
            except Exception as e:
                state["error"] = f"{type(e).__name__}: {e}"
            finally:
                # Cancelar el watchdog de este intento (si sigue vivo)
                attempt_watchdog.set()

        # ----- Loop principal: hasta MAX_RERENDER_RETRIES+1 intentos,
        #       cada uno con un proxy webshare diferente si el anterior
        #       fue rechazado (bft re-render). -----
        proxies_tried: list[str] = []
        for retry_idx in range(MAX_RERENDER_RETRIES + 1):
            # Guard de reloj: no arrancar otro intento si no cabe dentro del
            # presupuesto total (evita que el subproceso sea matado por el
            # timeout duro de runs.py a mitad de un intento).
            if (time.time() - t0) > (HARD_TOTAL_BUDGET_S - SOFT_TIMEOUT):
                break
            # En el primer intento, usar el proxy del local_solver.
            # En reintentos, rotar.
            if retry_idx == 0:
                proxy_for_browser = getattr(local_solver,
                                            "_webshare_proxy", None)
            else:
                # Rotar: forzar un proxy webshare diferente al último
                avoid = proxies_tried[-1] if proxies_tried else None
                new_proxy = _rotate_solver_proxy(local_solver, avoid)
                if new_proxy:
                    local_solver._webshare_proxy = new_proxy
                    proxies_tried.append(new_proxy)
                    state["proxies_used"].append(new_proxy)
                    proxy_for_browser = new_proxy
                else:
                    # No se pudo rotar; reusar el último
                    proxy_for_browser = (
                        proxies_tried[-1] if proxies_tried else None)
            if proxy_for_browser and proxy_for_browser not in proxies_tried:
                proxies_tried.append(proxy_for_browser)
            _run_attempt_with_watchdog(retry_idx, proxy_for_browser)
            # Si el watchdog disparó en este intento, NO es motivo para
            # parar — un timeout puede deberse a 2captcha saturado y
            # otro proxy puede ser más rápido. Solo paramos si ya
            # usamos todos los reintentos.
            if state.get("soft_timeout_hit"):
                state["timeouts"] = state.get("timeouts", 0) + 1
                if retry_idx >= MAX_RERENDER_RETRIES:
                    break
                # Si no, seguimos al siguiente intento
                continue
            # ¿La respuesta fue buena (matched o NO REGISTRA)?
            summary = state.get("summary") or ""
            if summary and ("NO REGISTRA" in summary.upper() or
                            ("REGISTR" in summary.upper() and
                             "FISCAL" in summary.upper())):
                # Éxito — parar
                break
            # ¿El server re-renderizó el form (rechazo del token)?
            # Solo reintentar si AÚN nos quedan reintentos.
            rerender = (state.get("summary") or "").lower()
            is_rerender = (
                "re-render" in rerender or
                "rechazo server-side" in rerender
            )
            if not is_rerender:
                # No fue un re-render, fue otro tipo de fallo
                # (submit silenciosamente fallido, etc).
                # No reintentar — el problema no es de IP.
                break
            state["rerenders"] = state.get("rerenders", 0) + 1
            # Si ya usamos todos los reintentos, parar
            if retry_idx >= MAX_RERENDER_RETRIES:
                break

        # ---- Construir Hit ----
        if state.get("soft_timeout_hit"):
            err_details = [{
                "soft_timeout_s": SOFT_TIMEOUT,
                "sitekey": (state.get("sitekey") or "")[:24],
                "captcha_attempts": state.get("attempts", 0),
                "proxies_used": state.get("proxies_used", []),
                "root_cause": "WAF de la Contraloría rechazó la consulta "
                              "aún con proxy residencial, o 2captcha "
                              "no pudo entregar un token a tiempo.",
            }]
            return Hit(
                self.name, False,
                f"Contraloría: timeout {SOFT_TIMEOUT}s con proxy "
                f"residencial ({len(state.get('proxies_used', []))} "
                f"proxy(s) intentado(s)).",
                details=err_details,
                notice=state.get("error"),
                captcha_required=True,
                evidence_urls=[CONTRALORIA_URL],
                download_url=state.get("shot_result")
                             or state.get("shot_form"),
                elapsed_s=time.time()-t0,
            )

        summary = state.get("summary") or "Sin respuesta"
        nav_changed = state.get("nav_changed")
        post_seen = state.get("post_seen") or []

        # Si NO hubo POST (silent submit failure), mejorar el summary
        # con un notice honesto: token fue inyectado pero el submit
        # no produjo navegación.
        if (not post_seen) and (not nav_changed) and (
            "no se obtuvo" in summary.lower() or
            "sin respuesta" in summary.lower()
        ):
            summary = (
                "No se obtuvo respuesta del servidor: el token "
                "reCAPTCHA fue inyectado pero el submit no produjo "
                "POST visible (submit silenciosamente fallido). "
                f"Proxies usados: {len(state.get('proxies_used', []))}."
            )
        # Si SÍ hubo POST pero el summary dice "no se obtuvo" (sin
        # mención del bft-rerender), el HTML no contenía el nuevo bft
        # — agregar la nota de POST observed para diagnóstico.
        elif post_seen and (
            "no se obtuvo" in summary.lower() and
            "re-render" not in summary.lower()
        ):
            summary = (
                summary.rstrip(".") +
                f" (POST observed: {len(post_seen)} request(s), "
                f"nav_changed={nav_changed})."
            )

        # Si el summary sigue siendo "re-renderizó" y NO REGISTRA/REGISTR,
        # agregar la nota de cuántos proxies se probaron.
        if "re-render" in summary.lower() and \
                state.get("proxies_used"):
            summary = (
                summary.rstrip(".") +
                f" Se probaron {len(state['proxies_used'])} "
                f"proxy(s) residencial(es)."
            )

        # CASO ESPECIAL (2026-07-02): si el servidor re-renderizó el form
        # con un nuevo token bft (0cAFcWeA) tras el submit, el WAF de
        # contraloría rechazó el token reCAPTCHA. Esto NO se resuelve con
        # más proxies: el problema es que el navegador headless nunca
        # marca el check verde localmente, y el server verifica ese
        # check antes de aceptar el token del solver externo.
        # Verificado 2026-07-02: el screenshot post-submit muestra el
        # reCAPTCHA "No soy un robot" SIN check verde, con un token bft
        # nuevo en el textarea (señal inequívoca de rechazo server-side).
        # Los solvers "from-outside" (2captcha, CapSolver) NO sirven aquí;
        # se necesita un solver "in-browser" (Buster, NopeCHA) o consulta
        # manual.
        #
        # Si detectamos este patrón (re-render tras submit), devolvemos
        # `status=captcha_blocked` con `requires_manual_review=True` y
        # un mensaje claro al usuario.
        # Calcular `matched` aquí (antes se calculaba al final del bloque)
        matched = "NO REGISTRA" not in summary.upper() and \
                  "REGISTR" in summary.upper() and \
                  "FISCAL" in summary.upper()
        is_captcha_rejected = (
            "re-render" in summary.lower() or
            "rechazo server-side" in summary.lower() or
            # Patrón explícito: nuevo token bft presente en el HTML
            state.get("bft_rerender", False)
        )
        if is_captcha_rejected and not matched:
            err_details = [{
                "cedula": cedula,
                "sitekey": (state.get("sitekey") or "")[:24],
                "captcha_attempts": state.get("attempts", 0),
                "token_len": state.get("token_len", 0),
                "proxies_used": state.get("proxies_used", []),
                "post_seen_count": len(post_seen),
                "nav_changed": nav_changed,
                "root_cause": (
                    "El WAF de contraloría rechazó el token reCAPTCHA "
                    "porque el navegador headless no marcó el check "
                    "verde localmente. Los solvers externos (2captcha, "
                    "CapSolver) resuelven el captcha pero no interactúan "
                    "con el navegador, por lo que el server detecta el "
                    "re-render del form con un nuevo token bft. Se "
                    "necesita un solver in-browser (Buster, NopeCHA) o "
                    "consulta manual."
                ),
            }]
            return Hit(
                self.name, False,
                "Contraloría: el captcha reCAPTCHA fue resuelto pero el "
                "navegador headless no marcó el check verde localmente. "
                "El servidor rechazó el token y re-renderizó el form "
                "con un nuevo captcha. Esto no se resuelve con más "
                "intentos o proxies — se requiere consulta manual.",
                details=err_details,
                notice=(
                    "Consulta manual en el portal de contraloría: "
                    f"{CONTRALORIA_URL}. "
                    "Alternativamente, instala una extensión anti-captcha "
                    "(Buster, NopeCHA) en el navegador del operador y "
                    "vuelve a intentar."
                ),
                status="captcha_blocked",
                captcha_required=True,
                requires_manual_review=True,
                notes=("El servidor de contraloría valida que el "
                       "navegador haya completado el challenge de "
                       "reCAPTCHA localmente, no solo que el token sea "
                       "válido. Browsers headless con tokens inyectados "
                       "son rechazados sin importar el proxy."),
                evidence_urls=[CONTRALORIA_URL],
                download_url=state.get("shot_result")
                             or state.get("shot_form"),
                elapsed_s=time.time()-t0,
            )

        if state.get("error") and not state.get("summary"):
            # Falló totalmente (sin resumen del servidor)
            err_details = [{
                "cedula": cedula,
                "error": state["error"],
                "sitekey": (state.get("sitekey") or "")[:24],
                "captcha_attempts": state.get("attempts", 0),
                "token_len": state.get("token_len", 0),
                "proxies_used": state.get("proxies_used", []),
                "inject_info": state.get("inject_info", {}),
                "submit_info": state.get("submit_info", {}),
                "post_seen": post_seen,
                "nav_changed": nav_changed,
            }]
            return Hit(
                self.name, False, summary,
                details=err_details,
                notice=(
                    f"Contraloría: {state['error']} "
                    f"(sitekey={state.get('sitekey','?')[:24]}..., "
                    f"proxies={len(state.get('proxies_used', []))})"
                ),
                captcha_required=True,
                evidence_urls=[CONTRALORIA_URL],
                download_url=state.get("shot_result")
                             or state.get("shot_form"),
                elapsed_s=time.time()-t0,
            )

        matched = "NO REGISTRA" not in summary.upper() and \
                  "REGISTR" in summary.upper() and \
                  "FISCAL" in summary.upper()
        details = state.get("details", [])
        # El criterio de aceptación dice "matched o no" si
        # contiene "NO REGISTRA responsabilidad fiscal" o "REGISTR"
        # — eso lo da el text-search del verificador, no el flag.
        # Preferir el certificado PDF (si se capturó) como evidencia: el
        # reporte lo rasteriza como "captura del PDF". Fallback a screenshot.
        download_url = (state.get("pdf_path")
                        or state.get("shot_result")
                        or state.get("shot_form"))
        return Hit(
            self.name, matched, summary, details,
            evidence_urls=[CONTRALORIA_URL],
            download_url=download_url,
            elapsed_s=time.time()-t0,
        )


@register
class ContaduriaSource:
    name = "Contaduría General de la Nación"
    source_url = "https://www.contaduria.gov.co/"
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "login"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere cédula. Necesita cuenta registrada.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   "CONSULTA MANUAL REQUERIDA: exige cuenta registrada",
                   status="requires_login",
                   notice="Requiere cuenta de usuario registrada en la "
                          "Contaduría. Registrarse primero en "
                          f"{self.source_url}.",
                   captcha_required=True,
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)
