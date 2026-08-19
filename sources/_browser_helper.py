"""
sources/_browser_helper.py — Helpers compartidos para fuentes con Playwright.

Cada fuente que use Playwright sync API necesita crear su propio
playwright instance en el mismo thread que las llamadas. Esto es
porque sync_playwright() ata el greenlet al thread.

Usar _run_in_fresh_browser(fn) que crea y destruye un browser NUEVO
por llamada (en el thread que la llama). Esto evita el bug
"Cannot switch to a different thread" cuando se ejecuta desde
un ThreadPoolExecutor.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from typing import Callable


# ---------- Retry de page.goto() para errores de red transitorios ----------
# Patrones de error que indican fallo de red (no del sitio). Estos se
# RETRYAN; el resto (errores del servidor, timeouts, etc.) NO se reintenta
# porque no son transitorios.
_NET_ERROR_PATTERNS = (
    "ERR_CONNECTION_RESET",       # conexión TCP cerrada por peer (común en
                                  # Registraduría: sitio público inestable)
    "ERR_CONNECTION_REFUSED",     # nadie escuchando en el puerto
    "ERR_CONNECTION_ABORTED",     # conexión abortada a mitad de carga
    "ERR_CONNECTION_CLOSED",      # cierre limpio pero prematuro
    "ERR_NETWORK_CHANGED",        # cambio de red (WiFi→Ethernet, etc.)
    "ERR_INTERNET_DISCONNECTED",  # el host perdió internet
    "ERR_TIMED_OUT",              # timeout TCP (puede ser transitorio)
    "ERR_NAME_NOT_RESOLVED",      # DNS falló (puede ser transitorio si
                                  # el resolver local tuvo un blip)
)


def _is_transient_net_error(exc: BaseException) -> bool:
    """True si la excepción de Playwright parece un error de red
    transitorio (vs. un error del sitio o un bug nuestro)."""
    msg = str(exc) or ""
    # Playwright sync API: las excepciones de red vienen como
    # `playwright.sync_api.Error` con el `net::ERR_*` literal en el message
    return any(pat in msg for pat in _NET_ERROR_PATTERNS)


def goto_with_retry(page, url: str, *, wait_until: str = "domcontentloaded",
                    timeout: int = 30000, max_attempts: int = 3,
                    backoff_s: tuple[float, ...] = (1.0, 3.0, 6.0),
                    on_retry: Callable[[int, BaseException, float], None] = None
                    ):
    """page.goto() con reintentos para errores de red transitorios.

    Args:
        page: instancia de `playwright.sync_api.Page`.
        url: URL a navegar.
        wait_until: igual que en `page.goto()`.
        timeout: timeout por intento (no acumulado) en ms.
        max_attempts: número total de intentos (default 3 → 1 inicial + 2 retries).
        backoff_s: tupla de esperas entre intentos. La longitud debe ser
            >= max_attempts - 1. Si max_attempts=3 y backoff=(1, 3, 6), los
            backoff reales aplicados son 1s y 3s (no se usa el 6s porque solo
            hay 2 esperas entre 3 intentos). Si se quieren usar los 3
            backoff, pasar `max_attempts=4` (1 inicial + 3 retries).
        on_retry: callback opcional (intento_numero, excepcion, sleep_s).
            Sirve para logging o para devolver evidencia (p.ej.
            screenshot del último error).

    Returns:
        El `response` que devolvió el último `page.goto()` exitoso
        (o el del último intento si todos fallaron con errores no
        transitorios).

    Raises:
        La última excepción si TODOS los intentos fallaron (incluyendo
        errores de red transitorios que se agotaron, y errores no
        transitorios). El caller debe capturar y convertir a `Hit`
        con un notice honesto.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            last_exc = e
            is_transient = _is_transient_net_error(e)
            # Si NO es transitorio, fallar rápido (no reintentar)
            if not is_transient:
                raise
            # Si era el último intento, propagar la excepción
            if attempt >= max_attempts:
                raise
            # Calcular backoff. backoff_s[i] = espera DESPUÉS del intento i+1
            # (0-indexed). Para max_attempts=3, tenemos 2 esperas (índices 0 y 1).
            sleep_s = backoff_s[min(attempt - 1, len(backoff_s) - 1)]
            if on_retry is not None:
                try:
                    on_retry(attempt, e, sleep_s)
                except Exception:
                    pass  # el callback de logging nunca debe romper el retry
            time.sleep(sleep_s)
    # Defensive: nunca debería llegar aquí, pero por si acaso
    assert last_exc is not None
    raise last_exc


def _run_in_fresh_browser(fn: Callable) -> None:
    """Ejecuta fn(page) en un browser Playwright NUEVO, en el thread
    que la llama. fn debe aceptar un único argumento: page."""
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


def _shot_save(page, source: str, query: str) -> str:
    """Guarda screenshot y devuelve path relativo."""
    DATA = Path(__file__).parent.parent / "data"
    (DATA / "screenshots").mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    fname = f"screenshots/{safe}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(DATA / fname),
                      full_page=False, timeout=15000)
    except Exception:
        pass
    return fname
