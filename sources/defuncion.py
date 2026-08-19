"""
sources/defuncion.py — Registraduría: Consulta de Defunciones por cédula.

URL: https://defunciones.registraduria.gov.co/
Form: 1 input con placeholder "1192722347" + 1 botón "Buscar" (azul).
Sin captcha visible (público).

El sitio es un Angular SPA: la página inicial solo contiene
<app-root></app-root> y los bundles JS (runtime/polyfills/main).
El form aparece después de la hidratación JS, por lo que hay que
hacer polling del input hasta que aparezca.

Resultado posible:
  - "NO SE ENCONTRO REGISTRO" (o variantes): la persona NO registra
    defunción en la base de la Registraduría.
  - Datos del fallecido (nombre, fecha, lugar, etc.): REGISTRA defunción.
  - Página en blanco / error de carga: sitio no respondió.

Estrategia (post-fix 2026-06-12):
  1) goto + wait domcontentloaded, con retries generosos:
     - 3 intentos a 45s c/u, backoff (2, 5, 10)s
     - Si los 3 fallan, un 4° intento a 60s (última oportunidad, con 15s
       de espera previa para dar tiempo a que el sitio se recupere).
     - Todo respetando un soft wall-clock budget de 90s desde t0
       (medido entre intentos, no por intent).
  2) Polling del input de cédula (1.5s × 20 = 30s)
  3) fill(cedula) + click en Buscar
  4) Espera ~5s para que el SPA renderice el resultado
  5) Parsea body.innerText buscando "NO REGISTRA" o "REGISTRA"
  6) Screenshot final como evidencia (download_url)

Justificación del 4° intento: el sitio de Defunciones es un SPA
Angular servido por infraestructura pública inestable. Hemos visto
que responde 200 OK en browser normal pero el GET inicial a veces
falla con ERR_CONNECTION_RESET o devuelve la página <app-root> vacía.
Si los primeros 3 intentos fallan por timeout, dar una última
oportunidad más larga suele bastar para confirmar si el sitio está
"muerto" o solo se está recuperando.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from .base import Hit
from .registry import register
from ._browser_helper import goto_with_retry
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


DATA = Path(__file__).parent.parent / "data"
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)


# ---------- selectores del sitio ----------
# El placeholder del input es literalmente "1192722347" (un ejemplo de
# cédula de 10 dígitos). Esto es un selector MUY estable porque el sitio
# lo expone como hint. Adicionalmente probamos selectores de respaldo por
# si el sitio cambia.
CEDULA_INPUT_SELECTORS = [
    'input[placeholder*="1192"]',          # placeholder "1192722347" (visto
                                           # en la captura original)
    'input[placeholder*="cedula" i]',      # "Buscar Cédula..."
    'input[placeholder*="cédula" i]',
    'input[placeholder*="documento" i]',
    'input[name*="Nuip" i]',
    'input[id*="Nuip" i]',
    'input[name*="Cedula" i]',
    'input[id*="Cedula" i]',
    'input[name*="cedula" i]',
    'input[id*="cedula" i]',
    'input[type="text"]',                  # fallback final (no password/email)
]

BUSCAR_BUTTON_SELECTORS = [
    'button:has-text("Buscar")',
    'input[value="Buscar"]',
    'input[type="submit"][value="Buscar"]',
    'a:has-text("Buscar")',
    'button[type="submit"]',
]


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _save_shot(page, cedula: str, tag: str = "") -> str | None:
    """Guarda screenshot de la página actual y devuelve ruta relativa."""
    safe = re.sub(r"[^\w-]", "_", cedula)[:30]
    suffix = f"_{tag}" if tag else ""
    fname = f"screenshots/defuncion_{safe}{suffix}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(DATA / fname), full_page=False, timeout=15000)
        return fname
    except Exception as e:
        print(f"  [defuncion] screenshot fail: {e}", flush=True)
        return None


def _find_cedula_input(page, timeout_s: float = 15.0):
    """Hace polling buscando el input de cédula.

    Devuelve (locator, frame) o (None, None) si no aparece en timeout.
    Busca en la página principal y en iframes.
    """
    deadline = time.time() + timeout_s
    poll_interval = 1.5
    selectors = CEDULA_INPUT_SELECTORS
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    # Verificar que sea visible
                    try:
                        if loc.is_visible(timeout=200):
                            return loc, page
                    except Exception:
                        # Si is_visible falla por timeout/cross-origin, pero
                        # el locator existe, devolverlo igual.
                        return loc, page
            except Exception:
                continue
        # También buscar en iframes (por si la SPA mete un iframe anidado
        # con el form)
        try:
            for fr in page.frames:
                if fr == page.main_frame:
                    continue
                for sel in selectors:
                    try:
                        loc = fr.locator(sel).first
                        if loc.count() > 0:
                            return loc, fr
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(poll_interval)
    return None, None


def _find_buscar_button(page):
    """Localiza el botón 'Buscar' en la página o en iframes."""
    # Primero en la página principal
    for sel in BUSCAR_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0:
                try:
                    if btn.is_visible(timeout=500):
                        return btn
                except Exception:
                    return btn
        except Exception:
            continue
    # Si no, en iframes
    try:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            for sel in BUSCAR_BUTTON_SELECTORS:
                try:
                    btn = fr.locator(sel).first
                    if btn.count() > 0:
                        return btn
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _run_in_fresh_browser(fn):
    """Crea un Playwright sync NUEVO por llamada (evita bug de threads)."""
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
                # Timeouts razonables para un SPA Angular
                page.set_default_timeout(20000)
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
class DefuncionRegistraduriaSource:
    name = "Registraduría — Defunciones (estado de la cédula)"
    source_url = "https://defunciones.registraduria.gov.co/"
    category = "Identidad y registros básicos"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()

        if not cedula:
            return Hit(
                source=self.name, matched=False, summary="",
                notice="Requiere cedula.",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        if not _have_browser():
            return Hit(
                source=self.name, matched=False, summary="",
                notice="Playwright no instalado. Click 'abrir fuente' "
                       "para buscar manualmente.",
                captcha_required=True,
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )

        def _do(page):
            # 1) Navegar. La página es Angular SPA; usamos domcontentloaded
            # para no esperar networkidle (puede no llegar nunca con
            # conexiones a CDN de Google Fonts).
            #
            # El sitio de Defunciones de la Registraduría es muy
            # inestable: responde con ERR_CONNECTION_RESET intermitente
            # y la primera respuesta puede tardar 10-20s (SPA Angular).
            #
            # Estrategia de retries (post-fix 2026-06-12):
            #   Bloque 1: 3 intentos a 45s c/u, backoff (2s, 5s, 10s).
            #   Bloque 2 (sólo si Bloque 1 falla completamente): 1 intento
            #     "de último recurso" a 60s, con 15s de espera previa para
            #     dar tiempo a que el sitio se recupere.
            #
            # Todo respetando un soft wall-clock budget de 90s desde t0
            # (medido entre fases, no por intent). Si el budget está
            # consumido, cortamos y devolvemos evidencia.
            SOFT_BUDGET_S = 90.0
            goto_ok = False
            last_goto_exc: Exception | None = None
            attempt_count = 0
            try:
                # ¿Nos queda budget para el bloque 1? Necesitamos al
                # menos 5s para que un intento corto tenga sentido.
                if time.time() - t0 > SOFT_BUDGET_S - 5:
                    raise RuntimeError(
                        "Soft wall-clock budget agotado antes de "
                        "intentar goto (t0→goto).")
                goto_with_retry(
                    page, self.source_url,
                    wait_until="domcontentloaded",
                    timeout=45000, max_attempts=3,
                    backoff_s=(2.0, 5.0, 10.0),
                    on_retry=lambda attempt, exc, sleep_s: print(
                        f"  [defuncion] goto retry {attempt}/3 en "
                        f"{sleep_s:.0f}s "
                        f"({type(exc).__name__}: {str(exc)[:60]})",
                        flush=True),
                )
                attempt_count = 3  # los 3 intentos del bloque 1
                goto_ok = True
            except Exception as exc1:
                last_goto_exc = exc1
                # Bloque 1 agotado. Probar el 4° intento de último
                # recurso a 60s, pero SOLO si aún queda budget.
                elapsed = time.time() - t0
                remaining = SOFT_BUDGET_S - elapsed
                # Necesitamos 15s de espera previa + 60s de intento = 75s,
                # o un poco menos si recortamos. Solo lo intentamos si
                # quedan >= 30s (espera reducida a 10s + intento 60s = 70s,
                # o nada).
                if remaining < 30:
                    # Sin budget para el 4° intento; caer al fail honesto.
                    print(
                        f"  [defuncion] sin budget para 4° intento "
                        f"(elapsed={elapsed:.1f}s, remaining="
                        f"{remaining:.1f}s)", flush=True)
                else:
                    # Espera previa adaptada al budget. Mínimo 5s,
                    # máximo 15s.
                    pre_wait = max(5.0, min(15.0, remaining - 60.0))
                    print(
                        f"  [defuncion] 3 intentos agotados; "
                        f"esperando {pre_wait:.0f}s y probando "
                        f"4° intento a 60s (elapsed={elapsed:.1f}s, "
                        f"remaining={remaining:.1f}s)", flush=True)
                    time.sleep(pre_wait)
                    try:
                        page.goto(self.source_url,
                                  wait_until="domcontentloaded",
                                  timeout=60000)
                        goto_ok = True
                        attempt_count = 4  # 3 + 1 del último recurso
                        print(
                            "  [defuncion] 4° intento exitoso",
                            flush=True)
                    except Exception as exc2:
                        last_goto_exc = exc2
                        print(
                            f"  [defuncion] 4° intento también falló: "
                            f"{type(exc2).__name__}: {str(exc2)[:60]}",
                            flush=True)

            if not goto_ok:
                # 4 intentos agotados (o budget consumido). Devolver
                # notice honesto + screenshot de evidencia del último
                # estado del browser (puede estar en blanco si la página
                # nunca cargo, eso es OK y es información útil).
                msg = (str(last_goto_exc) if last_goto_exc is not None
                       else "")
                etype = (type(last_goto_exc).__name__
                         if last_goto_exc is not None else "Unknown")
                net_err = ""
                for pat in ("ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
                            "ERR_CONNECTION_ABORTED", "ERR_TIMED_OUT",
                            "ERR_NAME_NOT_RESOLVED", "ERR_NETWORK_CHANGED"):
                    if pat in msg:
                        net_err = pat
                        break
                elapsed = time.time() - t0
                if net_err:
                    notice = (
                        f"Sitio Registraduria Defunciones no disponible "
                        f"(4 reintentos fallaron con {net_err}, "
                        f"elapsed={elapsed:.1f}s). "
                        f"El sitio pudo haber caido temporalmente."
                    )
                else:
                    notice = (
                        f"Sitio Defunciones no responde tras 4 intentos "
                        f"({etype}: {msg[:80]}, "
                        f"elapsed={elapsed:.1f}s). "
                        f"Reintentar mas tarde."
                    )
                # Capturar evidencia del estado actual del browser. Si
                # la página está completamente en blanco (caso típico
                # cuando goto falló con ERR_CONNECTION_RESET a nivel
                # TCP), el screenshot de about:blank sería un PNG de
                # ~5KB totalmente blanco — inútil para diagnóstico.
                # Inyectamos un HTML de error con la info del fallo
                # ANTES del screenshot para que la evidencia muestre
                # QUÉ pasó y no solo "blanco".
                shot = None
                try:
                    err_html = (
                        "<!doctype html><html><head><meta charset='utf-8'>"
                        "<title>Defunciones — error de conexion</title>"
                        "<style>"
                        "body{font-family:-apple-system,Segoe UI,sans-serif;"
                        "padding:32px;color:#1a202c;background:#fff;}"
                        "h1{color:#c53030;font-size:18px;margin:0 0 16px;}"
                        "pre{background:#f7fafc;border:1px solid #e2e8f0;"
                        "padding:12px;border-radius:6px;font-size:12px;"
                        "white-space:pre-wrap;word-break:break-word;}"
                        ".meta{color:#718096;font-size:12px;margin-top:24px;}"
                        "</style></head><body>"
                        f"<h1>No se pudo conectar a {self.source_url}</h1>"
                        "<p>El navegador intento cargar la pagina "
                        f"{attempt_count} veces y todas "
                        "fallaron. La conexion fue rechazada o cerro "
                        "antes de que el servidor respondiera "
                        "(ERR_CONNECTION_RESET es un error a nivel "
                        "TCP — el sitio no envio NINGUNA respuesta).</p>"
                        f"<pre>{etype}: {msg[:300]}</pre>"
                        f"<div class='meta'>cedula={cedula} | "
                        f"elapsed={elapsed:.1f}s | "
                        f"timestamp={int(time.time())}</div>"
                        "</body></html>")
                    try:
                        page.set_content(err_html, timeout=5000)
                        print(
                            "  [defuncion] error HTML inyectado "
                            "para screenshot", flush=True)
                    except Exception as e_set:
                        print(
                            f"  [defuncion] set_content falló "
                            f"({type(e_set).__name__}: "
                            f"{str(e_set)[:60]}); "
                            f"screenshot será about:blank", flush=True)
                    shot = _save_shot(page, cedula, tag="goto_fail")
                except Exception as e_shot:
                    print(
                        f"  [defuncion] screenshot fail: "
                        f"{type(e_shot).__name__}: "
                        f"{str(e_shot)[:60]}", flush=True)
                return Hit(
                    self.name, False, "Sitio no respondio",
                    notice=notice,
                    evidence_urls=[self.source_url],
                    download_url=shot,
                    elapsed_s=elapsed,
                )

            # 2) Polling del input de cédula. El form aparece después de
            # la hidratación de Angular; puede tardar 5-30s. 1.5s × 20
            # = 30s (aumentado desde 15s post-fix para tolerar SPA
            # lentos sin descartar respuestas válidas).
            cedula_input, cedula_frame = _find_cedula_input(page, timeout_s=30.0)
            if cedula_input is None:
                # Screenshot OBLIGATORIO en este punto: el sitio CARGÓ
                # (el goto fue OK) pero el form no hidrató. El screenshot
                # muestra el estado del DOM tras 30s de espera, que es
                # evidencia útil para diagnosticar si el sitio devolvió
                # una página de error 502/503 o un SPA con JS roto.
                shot = _save_shot(page, cedula, tag="no_input")
                # Verificar outage
                try:
                    body = page.evaluate("() => document.body.innerText")
                except Exception:
                    body = ""
                elapsed = time.time() - t0
                if ("no disponible" in body.lower() or
                        "no se encuentra disponible" in body.lower() or
                        "service unavailable" in body.lower()):
                    return Hit(
                        self.name, False, "Sitio no respondio",
                        evidence_urls=[self.source_url],
                        download_url=shot,
                        elapsed_s=elapsed,
                    )
                return Hit(
                    self.name, False,
                    "Sitio cargo pero el form no se hidrato en 30s",
                    notice=("El goto fue OK pero el input de cédula no "
                            f"apareció tras 30s de polling (elapsed="
                            f"{elapsed:.1f}s). El SPA puede haber "
                            "roto su hidratación o cambiado su DOM. "
                            "Ver screenshot para diagnóstico."),
                    evidence_urls=[self.source_url],
                    download_url=shot,
                    elapsed_s=elapsed,
                )

            # 3) Llenar la cédula. Intentar con fill() y, si no se
            # mantiene el valor, fallback a JS evaluate (dispatchEvent).
            try:
                cedula_input.click(timeout=5000)
                cedula_input.fill("")
                try:
                    cedula_input.fill(cedula)
                except Exception:
                    # Fallback: type por teclado
                    try:
                        cedula_input.type(cedula, delay=20)
                    except Exception:
                        pass
            except Exception as e:
                # Fallback JS
                try:
                    target = cedula_frame or page
                    target.evaluate(
                        "(c) => {"
                        "  const inputs = document.querySelectorAll('input');"
                        "  for (const i of inputs) {"
                        "    if (i.offsetParent === null) continue;"
                        "    if (i.type === 'hidden' || i.type === 'submit' ||"
                        "        i.type === 'button' || i.type === 'checkbox' ||"
                        "        i.type === 'radio') continue;"
                        "    if (i.placeholder && i.placeholder.includes('1192')) {"
                        "      i.focus(); i.value = c;"
                        "      i.dispatchEvent(new Event('input', {bubbles: true}));"
                        "      i.dispatchEvent(new Event('change', {bubbles: true}));"
                        "      return true;"
                        "    }"
                        "  }"
                        "  return false;"
                        "}",
                        cedula)
                except Exception:
                    pass

            # Verificar que el valor realmente quedó
            filled = False
            try:
                actual = cedula_input.input_value(timeout=2000)
                if actual and cedula in actual:
                    filled = True
            except Exception:
                filled = True   # asumir OK si no se puede leer
            if not filled:
                # Segundo intento: dispatchEvent sobre el input
                try:
                    target = cedula_frame or page
                    target.evaluate(
                        "(c) => {"
                        "  const inputs = document.querySelectorAll('input');"
                        "  for (const i of inputs) {"
                        "    if (i.offsetParent === null) continue;"
                        "    if (i.type === 'hidden' || i.type === 'submit' ||"
                        "        i.type === 'button' || i.type === 'checkbox' ||"
                        "        i.type === 'radio') continue;"
                        "    i.focus(); i.value = c;"
                        "    i.dispatchEvent(new Event('input', {bubbles: true}));"
                        "    i.dispatchEvent(new Event('change', {bubbles: true}));"
                        "    i.dispatchEvent(new Event('blur', {bubbles: true}));"
                        "  }"
                        "}",
                        cedula)
                except Exception:
                    pass

            # 4) Click en "Buscar"
            clicked = False
            try:
                btn = _find_buscar_button(page)
                if btn is not None:
                    btn.click(timeout=5000, force=True)
                    clicked = True
            except Exception:
                pass
            if not clicked:
                # Fallback: presionar Enter sobre el input
                try:
                    cedula_input.press("Enter", timeout=5000)
                    clicked = True
                except Exception:
                    pass

            # 5) Esperar 5s para que el SPA renderice el resultado
            try:
                page.wait_for_timeout(5000)
            except Exception:
                pass

            # 6) Parsear el resultado
            try:
                body_text = page.evaluate("() => document.body.innerText") or ""
            except Exception:
                body_text = ""

            # También leer HTML por si el innerText viene vacío
            if not body_text or len(body_text.strip()) < 5:
                try:
                    html = page.content() or ""
                    cleaned = re.sub(r"<script[^>]*>.*?</script>", "", html,
                                     flags=re.S)
                    cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned,
                                     flags=re.S)
                    body_text = re.sub(r"<[^>]+>", " ", cleaned)
                    body_text = re.sub(r"\s+", " ", body_text)
                except Exception:
                    pass

            pt_up = body_text.upper()
            pt_low = body_text.lower()

            # 7) Screenshot final (SIEMPRE, como evidencia)
            shot_final = _save_shot(page, cedula, tag="final")

            # 8) Clasificar resultado
            # Outage
            if ("no disponible" in pt_low or
                    "no se encuentra disponible" in pt_low or
                    "service unavailable" in pt_low or
                    "502 bad gateway" in pt_low or
                    "503 service" in pt_low or
                    "504 gateway" in pt_low):
                return Hit(
                    self.name, False, "Sitio no respondio",
                    evidence_urls=[self.source_url],
                    download_url=shot_final,
                    elapsed_s=time.time() - t0,
                )

            # NO REGISTRA defunción. El sitio reporta explícitamente que
            # el documento está "Vigente (Vivo)" en el archivo nacional
            # de identificación — lo que equivale a "no hay registro de
            # defunción" para esta consulta.
            no_registra_signals = [
                "NO SE ENCONTRO REGISTRO",
                "NO SE ENCONTRÓ REGISTRO",
                "NO SE ENCONTRARON REGISTROS",
                "NO EXISTE REGISTRO",
                "NO REGISTRA DEFUNCION",
                "NO REGISTRA DEFUNCIÓN",
                "SIN REGISTRO DE DEFUNCION",
                "SIN REGISTRO DE DEFUNCIÓN",
                "NO HAY REGISTRO",
                "VIGENTE (VIVO)",            # visto en pruebas reales
                "ESTADO VIGENTE",
            ]
            if any(sig in pt_up for sig in no_registra_signals):
                return Hit(
                    self.name, False,
                    "NO REGISTRA en Registraduria Defunciones",
                    evidence_urls=[self.source_url],
                    download_url=shot_final,
                    elapsed_s=time.time() - t0,
                )

            # REGISTRA defunción — buscar datos típicos (nombres, fechas,
            # lugar de defunción, etc.) o estado "Fallecido".
            registra_signals = [
                "REGISTRA DEFUNCION",
                "REGISTRA DEFUNCIÓN",
                "DEFUNCION REGISTRADA",
                "DEFUNCIÓN REGISTRADA",
                "CERTIFICADO DE DEFUNCION",
                "CERTIFICADO DE DEFUNCIÓN",
                "FECHA DE DEFUNCION",
                "FECHA DE DEFUNCIÓN",
                "PARTIDA DE DEFUNCION",
                "PARTIDA DE DEFUNCIÓN",
                "ESTADO FALLECIDO",
                "FALLECIDO",
            ]
            if any(sig in pt_up for sig in registra_signals):
                # Extraer detalles: pares label:valor de la tabla de resultado
                details = _extract_defuncion_details(body_text)
                return Hit(
                    self.name, True,
                    "REGISTRA en Registraduria Defunciones",
                    details=details,
                    evidence_urls=[self.source_url],
                    download_url=shot_final,
                    elapsed_s=time.time() - t0,
                )

            # El form fue enviado pero la página no muestra un mensaje
            # claro de resultado. Ser honestos con el operador.
            if clicked and len(body_text.strip()) > 50:
                return Hit(
                    self.name, False,
                    f"Búsqueda ejecutada — resultado no clasificable "
                    f"automáticamente. Ver screenshot. Texto relevante: "
                    f"{body_text[:200]!r}",
                    evidence_urls=[self.source_url],
                    download_url=shot_final,
                    elapsed_s=time.time() - t0,
                )

            # No se pudo hacer click o el sitio no cambió
            return Hit(
                self.name, False,
                "Sitio no respondio",
                notice="No se hizo click efectivo en Buscar o el sitio no "
                       "cambió tras 5s. Ver screenshot para diagnóstico.",
                evidence_urls=[self.source_url],
                download_url=shot_final,
                elapsed_s=time.time() - t0,
            )

        try:
            hit = _run_in_fresh_browser(_do)
            if hit is None:
                return Hit(
                    self.name, False, "",
                    notice="Browser cerró sin devolver resultado.",
                    captcha_required=True,
                    evidence_urls=[self.source_url],
                    elapsed_s=time.time() - t0,
                )
            return hit
        except Exception as e:
            return Hit(
                self.name, False, "",
                notice=f"Defuncion error: {type(e).__name__}: {e}",
                captcha_required=True,
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )


def _extract_defuncion_details(text: str) -> list[dict]:
    """Extrae pares label:valor del texto de resultado, formato libre."""
    details: list[dict] = []
    # Patrón 1: "LABEL: VALOR" o "LABEL : VALOR"
    for m in re.finditer(
            r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40}?)\s*:\s*"
            r"([^\n\r|]{3,150})", text):
        label = m.group(1).strip()
        value = m.group(2).strip()
        if label and value and len(label) < 50:
            details.append({"campo": label, "valor": value})
            if len(details) >= 20:
                break
    # Patrón 2: tabla simple (líneas con pipes o tabs)
    if not details:
        for line in text.splitlines():
            line = line.strip()
            if "|" in line and len(line) > 10:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) >= 2:
                    details.append({"campo": cells[0], "valor": " | ".join(cells[1:])})
                    if len(details) >= 20:
                        break
    return details
