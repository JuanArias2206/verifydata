"""
solvers/webshare.py — Fuente única de proxies residenciales/datacenter
Webshare para los solvers de captcha (2captcha, capsolver).

Usa la **API v2** de Webshare con autenticación por token
(`Authorization: Token <api_key>`), NO el "download URL" con token
embebido (que queda obsoleto cuando se rota la API key de la cuenta).

Docs: https://apidocs.webshare.io/  → Proxy List:
    GET https://proxy.webshare.io/api/v2/proxy/list/?mode=direct

Devuelve proxies en formato `http://user:pass@ip:port`, listo para
pasar a `TwoCaptchaSolver` / `CapSolver`. Filtra los proxies marcados
`valid=false` por Webshare para no gastar intentos en proxies muertos.

Cache en proceso (TTL 10 min) para no golpear la API en cada retry
(Webshare rate-limitea con HTTP 429).
"""
from __future__ import annotations
import time
import random
import requests
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)

WEBSHARE_LIST_URL = "https://proxy.webshare.io/api/v2/proxy/list/"

_CACHE: list[str] | None = None
_CACHE_TS: float = 0.0
_CACHE_TTL_S = 600  # 10 min


def _api_key_from_config() -> str:
    """Lee la API key de webshare desde config.yaml
    (captcha.twocaptcha.proxy.webshare_api_key)."""
    try:
        from config import load_config
        cfg = load_config()
        return (cfg.get("captcha", {})
                   .get("twocaptcha", {})
                   .get("proxy", {})
                   .get("webshare_api_key", "") or "")
    except Exception:
        return ""


def list_proxies(api_key: str | None = None, force: bool = False,
                 only_valid: bool = True, page_size: int = 100) -> list[str]:
    """Devuelve una lista de proxies webshare en formato
    `http://user:pass@ip:port`. Usa cache en proceso (TTL 10 min).

    Si `only_valid=True` (default) descarta proxies con `valid=false`
    reportado por webshare. Si no queda ninguno válido, cae a devolver
    todos (mejor un proxy dudoso que ninguno)."""
    global _CACHE, _CACHE_TS
    now = time.time()
    if (not force and _CACHE is not None
            and (now - _CACHE_TS) < _CACHE_TTL_S):
        return _CACHE
    key = api_key or _api_key_from_config()
    if not key:
        return _CACHE or []
    try:
        r = requests.get(
            WEBSHARE_LIST_URL,
            params={"mode": "direct", "page": 1, "page_size": page_size},
            headers={"Authorization": f"Token {key}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", []) or []
        valid, all_proxies = [], []
        for p in results:
            user = p.get("username", "")
            pwd = p.get("password", "")
            ip = p.get("proxy_address", "")
            port = p.get("port", "")
            if not (ip and port):
                continue
            url = f"http://{user}:{pwd}@{ip}:{port}"
            all_proxies.append(url)
            if p.get("valid", True):
                valid.append(url)
        chosen = valid if (only_valid and valid) else all_proxies
        if chosen:
            _CACHE = chosen
            _CACHE_TS = now
            return chosen
        return _CACHE or []
    except Exception as e:
        print(f"  [webshare] list fetch fail: {e}", flush=True)
        return _CACHE or []


def pick_proxy(api_key: str | None = None,
               avoid: str | None = None) -> str | None:
    """Elige un proxy al azar, intentando NO repetir `avoid`."""
    proxies = list_proxies(api_key)
    if not proxies:
        return None
    if avoid and len(proxies) > 1:
        pool = [p for p in proxies if p != avoid]
        if pool:
            return random.choice(pool)
    return random.choice(proxies)
