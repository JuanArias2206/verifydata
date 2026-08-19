"""
solvers/factory.py — Construcción centralizada de la cadena de solvers.

`build_chain()` lee config.yaml y arma un FallbackSolver con el orden
de preferencia configurado (por defecto: CapSolver → 2captcha). Esto es
la única fuente de verdad para "qué solver se usa y con qué respaldo".

Estrategia de proxy:
  - CapSolver se usa SIEMPRE en modo proxyless (es su punto fuerte para
    reCAPTCHA Enterprise; además evita quemar proxies webshare muertos).
  - 2captcha usa proxy residencial webshare cuando `use_proxy=True`
    (necesario para WAFs estrictos: Contraloría, Policía).

IMPORTANTE (2026-07-02): cuando `use_proxy=True`, los solvers con
`accepts_external_proxy=False` (CapSolver) se EXCLUYEN de la cadena
porque resuelven desde IP datacenter y Google reCAPTCHA rechaza el
token server-side cuando el navegador navega con proxy residencial
(inconsistencia de IP entre el "solver" y el "navegador"). El token
proxyless de CapSolver solo sirve cuando el navegador también navega
sin proxy (modo proxyless end-to-end), lo cual no aplica a Contraloría
ni Policía (que SIEMPRE necesitan proxy residencial para pasar el WAF).
"""
from __future__ import annotations
from .base import CaptchaSolver
from .chain import FallbackSolver
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


def _make_capsolver(cfg: dict, timeout: int):
    from .capsolver import CapSolver
    cap = cfg.get("captcha", {}).get("capsolver", {})
    key = cap.get("api_key", "") or ""
    return CapSolver(api_key=key.strip(),
                     timeout=cap.get("default_timeout", timeout))


def _make_twocaptcha(cfg: dict, timeout: int, use_proxy: bool):
    from .twocaptcha import TwoCaptchaSolver
    tc = cfg.get("captcha", {}).get("twocaptcha", {})
    key = tc.get("api_key", "") or ""
    proxy_url = None
    if use_proxy:
        try:
            from .webshare import pick_proxy
            proxy_url = pick_proxy()
        except Exception:
            proxy_url = None
    return TwoCaptchaSolver(api_key=key.strip(),
                            timeout=tc.get("default_timeout", timeout),
                            proxy_url=proxy_url)


_BUILDERS = {
    "capsolver": lambda cfg, t, up: _make_capsolver(cfg, t),
    "capsolver_proxy": lambda cfg, t, up: _make_capsolver(cfg, t),
    "twocaptcha": lambda cfg, t, up: _make_twocaptcha(cfg, t, up),
    "2captcha": lambda cfg, t, up: _make_twocaptcha(cfg, t, up),
}


def build_chain(cfg: dict | None = None, use_proxy: bool | None = None,
                timeout: int = 180) -> FallbackSolver:
    """Construye un FallbackSolver según config.yaml.

    Args:
        cfg: config ya cargada (si None, se carga).
        use_proxy: forzar proxy en 2captcha. Si None, lee
            captcha.twocaptcha.proxy.enabled.
        timeout: timeout por defecto de cada solver.

    Comportamiento con `use_proxy=True` (2026-07-02):
        Cuando `use_proxy=True` Y la cadena viene en el orden por defecto
        `["capsolver", "twocaptcha"]`, el ORDEN SE INVIERTE automáticamente
        a `["twocaptcha", "capsolver"]`. Razón: CapSolver (proxyless)
        genera tokens desde IP datacenter que Google reCAPTCHA rechaza
        server-side cuando el navegador navega con proxy residencial
        (WAFs estrictos como Contraloría, Policía). 2captcha con
        proxy webshare genera el token desde la misma IP residencial
        que el navegador, evitando el rechazo.

        Si `use_proxy=False`, se respeta el orden original
        (`capsolver → twocaptcha` por defecto, porque CapSolver es
        generalmente más rápido y barato para reCAPTCHA sin WAF).
    """
    if cfg is None:
        from config import load_config
        cfg = load_config()
    cap_cfg = cfg.get("captcha", {})
    if use_proxy is None:
        use_proxy = bool(cap_cfg.get("twocaptcha", {})
                                .get("proxy", {})
                                .get("enabled", False))
    order = cap_cfg.get("order") or ["capsolver", "twocaptcha"]
    # Si use_proxy=True y el orden empieza con capsolver, invertir.
    # Esto pone 2captcha con proxy primero (que es lo que queremos
    # para Contraloría/Policía con WAF estricto).
    if use_proxy and order and order[0] == "capsolver":
        # Mover 'twocaptcha' (o '2captcha') al inicio, manteniendo el
        # orden relativo del resto.
        proxy_solvers = [k for k in order
                         if k in ("twocaptcha", "2captcha")]
        non_proxy_solvers = [k for k in order
                             if k not in ("twocaptcha", "2captcha")]
        order = proxy_solvers + non_proxy_solvers
        print(f"  [factory] use_proxy=True: orden invertido a {order} "
              f"(2captcha con proxy primero para evitar rechazo "
              f"server-side por inconsistencia de IP)", flush=True)

    solvers = []
    for kind in order:
        builder = _BUILDERS.get(str(kind).lower())
        if not builder:
            continue
        try:
            s = builder(cfg, timeout, use_proxy)
            if s is not None and s.is_available():
                solvers.append(s)
        except Exception as e:
            print(f"  [factory] no se pudo construir '{kind}': {e}",
                  flush=True)
    return FallbackSolver(solvers, timeout=timeout)
