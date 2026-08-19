"""
solvers/chain.py — FallbackSolver: cadena de solvers con respaldo.

Envuelve una lista de solvers (p.ej. [CapSolver, TwoCaptchaSolver]) y,
para CADA método de resolución, los prueba EN ORDEN hasta que uno
devuelve un resultado no vacío. Si un solver lanza excepción o devuelve
vacío, pasa al siguiente. Si todos fallan, lanza CaptchaUnsolved con el
detalle combinado.

Esto implementa el requisito del negocio: "si no funciona un resolvedor
de captcha, usa el otro" — sin tener que tocar cada fuente.

Compatibilidad: expone `.timeout`, `_proxy_url`, `set_proxy()` y los
propaga a todos los hijos, para que el código existente que hace
`solver.timeout = X` o rota proxies siga funcionando.
"""
from __future__ import annotations
from .base import CaptchaSolver, CaptchaUnsolved
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


class FallbackSolver(CaptchaSolver):
    def __init__(self, solvers: list, timeout: int | None = None):
        # Solo conservar solvers disponibles (con API key)
        self._solvers = [s for s in solvers if s is not None]
        self._timeout = timeout
        self.last_used: str | None = None
        if timeout is not None:
            self.timeout = timeout   # propaga a hijos vía setter

    # ---------- timeout propagado ----------
    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value
        for s in self._solvers:
            try:
                s.timeout = value
            except Exception:
                pass

    # ---------- proxy propagado ----------
    @property
    def _proxy_url(self):
        for s in self._solvers:
            p = getattr(s, "_proxy_url", None)
            if p:
                return p
        return None

    @_proxy_url.setter
    def _proxy_url(self, value):
        self.set_proxy(value)

    def set_proxy(self, proxy_url):
        for s in self._solvers:
            # Respetar solvers que prefieren proxyless (p.ej. CapSolver)
            if not getattr(s, "accepts_external_proxy", True):
                continue
            if hasattr(s, "set_proxy"):
                try:
                    s.set_proxy(proxy_url)
                    continue
                except Exception:
                    pass
            try:
                s._proxy_url = proxy_url
                s._webshare_proxy = proxy_url
            except Exception:
                pass

    def is_available(self) -> bool:
        return any(s.is_available() for s in self._solvers)

    @property
    def name(self) -> str:
        inner = "+".join(s.name for s in self._solvers) or "vacío"
        return f"Fallback[{inner}]"

    @property
    def solvers(self) -> list:
        return list(self._solvers)

    # ---------- helper genérico ----------
    def _try_all(self, method: str, *args, **kwargs) -> str:
        errors = []
        available = [s for s in self._solvers if s.is_available()]
        if not available:
            raise CaptchaUnsolved(
                f"FallbackSolver: ningún solver disponible para {method}")
        for s in available:
            fn = getattr(s, method, None)
            if fn is None:
                continue
            try:
                result = fn(*args, **kwargs)
                if result:
                    self.last_used = s.name
                    print(f"  [fallback] {method} resuelto por {s.name}",
                          flush=True)
                    return result
                errors.append(f"{s.name}: resultado vacío")
            except CaptchaUnsolved as e:
                errors.append(f"{s.name}: {e}")
                print(f"  [fallback] {s.name} falló ({method}): {e}; "
                      f"probando siguiente…", flush=True)
            except Exception as e:
                errors.append(f"{s.name}: {type(e).__name__}: {e}")
                print(f"  [fallback] {s.name} excepción ({method}): {e}; "
                      f"probando siguiente…", flush=True)
        raise CaptchaUnsolved(
            f"FallbackSolver: todos los solvers fallaron en {method}. "
            + " | ".join(errors))

    # ---------- métodos públicos ----------
    def solve_image(self, png_bytes, **kwargs) -> str:
        return self._try_all("solve_image", png_bytes, **kwargs)

    def solve_recaptcha_v2(self, sitekey, page_url, **kwargs) -> str:
        return self._try_all("solve_recaptcha_v2", sitekey, page_url, **kwargs)

    def solve_recaptcha_v3(self, sitekey, page_url, **kwargs) -> str:
        return self._try_all("solve_recaptcha_v3", sitekey, page_url, **kwargs)

    def solve_hcaptcha(self, sitekey, page_url, **kwargs) -> str:
        return self._try_all("solve_hcaptcha", sitekey, page_url, **kwargs)

    def solve_trivia(self, question: str) -> str:
        return self._try_all("solve_trivia", question)

    def get_balance(self) -> dict:
        out = {}
        for s in self._solvers:
            try:
                out[s.name] = s.get_balance()
            except Exception as e:
                out[s.name] = f"error: {e}"
        return out
