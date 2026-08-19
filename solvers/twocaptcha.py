"""
solvers/twocaptcha.py — Solver 2captcha.com.

API v2 docs: https://2captcha.com/api-docs
  - createTask: https://2captcha.com/api-docs/create-task
  - getTaskResult: https://2captcha.com/api-docs/get-task-result
  - Tipos de tarea:
    * RecaptchaV2TaskProxyless   (reCAPTCHA v2 sin proxy)
    * RecaptchaV2EnterpriseTaskProxyless   (reCAPTCHA v2 Enterprise)
    * RecaptchaV3TaskProxyless   (reCAPTCHA v3)
    * HCaptchaTaskProxyless      (hCaptcha)
    * ImageToTextTask            (captcha de imagen)
  - Con proxy (rotating residential) — usar RecaptchaV2Task + proxy en lugar
    de RecaptchaV2TaskProxyless. Esto reduce rechazos server-side en
    WAFs estrictos (Contraloría, Procuraduría, Policía).

Configuración (config.yaml):
  captcha:
    solver: "twocaptcha"
    twocaptcha:
      api_key: "39678a755a8df343ddfa075c132e4202"
      proxy:
        enabled: true              # usar proxy residencial para WAFs estrictos
        url: "http://user:pass@host:port"   # o None para auto-fetch de webshare

Uso:
  from solvers.twocaptcha import TwoCaptchaSolver
  s = TwoCaptchaSolver(api_key="...", proxy_url="http://...")
  token = s.solve_recaptcha_v2(sitekey, page_url)
"""
from __future__ import annotations
import time
import requests
from .base import CaptchaSolver, CaptchaUnsolved
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


def _fetch_webshare_proxy(api_key: str | None = None) -> str | None:
    """Devuelve UN proxy webshare al azar en formato
    http://user:pass@host:port (apto para 2captcha).

    Delega en solvers/webshare.py, que usa la API v2 de webshare con
    autenticación por token (`Authorization: Token <api_key>`) y filtra
    proxies inválidos. La api_key se lee de config.yaml si no se pasa."""
    try:
        from .webshare import pick_proxy
        return pick_proxy(api_key)
    except Exception as e:
        print(f"  [webshare] error fetching proxy: {e}", flush=True)
        return None


class TwoCaptchaSolver(CaptchaSolver):
    API_URL = "https://api.2captcha.com"
    LEGACY_URL = "https://2captcha.com"
    DEFAULT_TIMEOUT = 180   # 3 minutos
    POLL_INTERVAL = 5

    def __init__(self, api_key: str = "", timeout: int = DEFAULT_TIMEOUT,
                 proxy_url: str | None = None,
                 use_webshare: bool = False):
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        # Proxy: si use_webshare=True, auto-fetch de webshare; sino usar
        # proxy_url explícito. None = no proxy (Proxyless).
        self._proxy_url: str | None = proxy_url
        if use_webshare and not proxy_url:
            self._proxy_url = _fetch_webshare_proxy()

    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def name(self) -> str:
        return "TwoCaptchaSolver"

    # ---------- API v2 (createTask / getTaskResult) ----------
    def _create_task(self, task: dict) -> str:
        """API v2: crea una tarea y devuelve taskId."""
        payload = {"clientKey": self.api_key, "task": task}
        r = self._session.post(f"{self.API_URL}/createTask",
                               json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errorId"):
            raise CaptchaUnsolved(
                f"2captcha createTask error: {data.get('errorCode')}: "
                f"{data.get('errorDescription')}")
        return data["taskId"]

    def _get_task_result(self, task_id: str) -> dict:
        """API v2: poll hasta status=ready. Retorna la solución completa."""
        payload = {"clientKey": self.api_key, "taskId": task_id}
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            r = self._session.post(f"{self.API_URL}/getTaskResult",
                                   json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("errorId"):
                raise CaptchaUnsolved(
                    f"2captcha getTaskResult error: {data.get('errorCode')}: "
                    f"{data.get('errorDescription')}")
            if data.get("status") == "ready":
                return data.get("solution", {})
        raise CaptchaUnsolved(
            f"2captcha timeout ({self.timeout}s) sin resolver captcha")

    # ---------- API legacy (in.php / res.php) ----------
    def _legacy_in(self, params: dict) -> str:
        params["key"] = self.api_key
        r = self._session.post(f"{self.LEGACY_URL}/in.php",
                                data=params, timeout=30)
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("OK|"):
            return text.split("|", 1)[1]
        raise CaptchaUnsolved(f"2captcha in.php error: {text}")

    def _legacy_res(self, captcha_id: str) -> str:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            r = self._session.get(
                f"{self.LEGACY_URL}/res.php",
                params={"key": self.api_key, "action": "get",
                        "id": captcha_id, "json": 0},
                timeout=30)
            r.raise_for_status()
            text = r.text.strip()
            if text.startswith("OK|"):
                return text.split("|", 1)[1]
            if text == "CAPCHA_NOT_READY":
                continue
            raise CaptchaUnsolved(f"2captcha res.php error: {text}")
        raise CaptchaUnsolved(f"2captcha timeout ({self.timeout}s)")

    def get_balance(self) -> float:
        """Consulta el balance de la cuenta (útil para debug)."""
        r = self._session.get(
            f"{self.LEGACY_URL}/res.php",
            params={"key": self.api_key, "action": "getbalance", "json": 0},
            timeout=30)
        r.raise_for_status()
        text = r.text.strip()
        try:
            return float(text)
        except ValueError:
            raise CaptchaUnsolved(f"2captcha getbalance: {text}")

    # ---------- Métodos públicos ----------
    def solve_image(self, png_bytes, **kwargs) -> str:
        """Resuelve un captcha de imagen (texto/imagen) — usa API v2."""
        if not self.is_available():
            raise CaptchaUnsolved("TwoCaptchaSolver no configurado.")
        import base64
        task = {
            "type": "ImageToTextTask",
            "body": base64.b64encode(png_bytes).decode(),
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("text", "")

    def solve_recaptcha_v2(self, sitekey: str, page_url: str,
                           enterprise: bool = False, **kwargs) -> str:
        """Resuelve un reCAPTCHA v2 (normal o Enterprise).
        Retorna el token g-recaptcha-response listo para inyectar.
        Si hay proxy configurado, usa RecaptchaV2Task (con proxy) en
        lugar de RecaptchaV2TaskProxyless — necesario para WAFs
        estrictos (Contraloría, Procuraduría, Policía) que rechazan
        tokens resueltos desde IPs datacenter.
        """
        if not self.is_available():
            raise CaptchaUnsolved("TwoCaptchaSolver no configurado.")
        if self._proxy_url:
            # Con proxy: usar RecaptchaV2Task (o Enterprise) + proxyLogin/proxyPassword/proxyAddress/proxyPort
            if enterprise:
                task_type = "RecaptchaV2EnterpriseTask"
            else:
                task_type = "RecaptchaV2Task"
            # Parsear http://user:pass@host:port
            try:
                from urllib.parse import urlparse
                p = urlparse(self._proxy_url)
                proxy_user = p.username or ""
                proxy_pass = p.password or ""
                proxy_host = p.hostname or ""
                proxy_port = p.port or 0
            except Exception as e:
                raise CaptchaUnsolved(f"2captcha proxy URL inválida: {e}")
            task = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": sitekey,
                "proxyType": "http",
                "proxyAddress": proxy_host,
                "proxyPort": proxy_port,
                "proxyLogin": proxy_user,
                "proxyPassword": proxy_pass,
            }
        else:
            if enterprise:
                task_type = "RecaptchaV2EnterpriseTaskProxyless"
            else:
                task_type = "RecaptchaV2TaskProxyless"
            task = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": sitekey,
            }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_recaptcha_v3(self, sitekey: str, page_url: str,
                           action: str = "verify", min_score: float = 0.3,
                           **kwargs) -> str:
        """Resuelve un reCAPTCHA v3."""
        if not self.is_available():
            raise CaptchaUnsolved("TwoCaptchaSolver no configurado.")
        task = {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "minScore": min_score,
            "pageAction": action,
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_hcaptcha(self, sitekey: str, page_url: str, **kwargs) -> str:
        """Resuelve un hCaptcha."""
        if not self.is_available():
            raise CaptchaUnsolved("TwoCaptchaSolver no configurado.")
        task = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_trivia(self, question: str) -> str:
        raise CaptchaUnsolved(
            "TwoCaptchaSolver no resuelve trivia. Use TriviaSolver.")
