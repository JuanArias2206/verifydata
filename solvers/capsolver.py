"""
solvers/capsolver.py — Solver CapSolver.com (respaldo de 2captcha).

Docs: https://docs.capsolver.com/en/guide/getting-started/
  - createTask:    POST https://api.capsolver.com/createTask
  - getTaskResult: POST https://api.capsolver.com/getTaskResult
  - getBalance:    POST https://api.capsolver.com/getBalance

Tipos de tarea usados:
  * ReCaptchaV2TaskProxyLess            (reCAPTCHA v2 sin proxy)
  * ReCaptchaV2EnterpriseTaskProxyLess  (reCAPTCHA v2 Enterprise sin proxy)
  * ReCaptchaV2Task / ReCaptchaV2EnterpriseTask  (con proxy)
  * ReCaptchaV3TaskProxyLess            (reCAPTCHA v3)
  * HCaptchaTaskProxyLess               (hCaptcha)
  * ImageToTextTask                     (captcha de imagen)

Interfaz idéntica a TwoCaptchaSolver para que ambos sean intercambiables
dentro de FallbackSolver (solvers/chain.py).

Formato de proxy CapSolver: "http:ip:port:user:pass". Aceptamos la URL
estándar del proyecto (http://user:pass@ip:port) y la convertimos.
"""
from __future__ import annotations
import time
import base64
import requests
from urllib.parse import urlparse
from .base import CaptchaSolver, CaptchaUnsolved


def _to_capsolver_proxy(proxy_url: str | None) -> str | None:
    """Convierte http://user:pass@ip:port → http:ip:port:user:pass."""
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)
        scheme = (p.scheme or "http").replace("https", "http")
        host = p.hostname or ""
        port = p.port or ""
        user = p.username or ""
        pwd = p.password or ""
        if not (host and port):
            return None
        if user or pwd:
            return f"{scheme}:{host}:{port}:{user}:{pwd}"
        return f"{scheme}:{host}:{port}"
    except Exception:
        return None


class CapSolver(CaptchaSolver):
    API_URL = "https://api.capsolver.com"
    DEFAULT_TIMEOUT = 180
    POLL_INTERVAL = 3

    def __init__(self, api_key: str = "", timeout: int = DEFAULT_TIMEOUT,
                 proxy_url: str | None = None, use_webshare: bool = False):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._session = requests.Session()
        self._proxy_url: str | None = proxy_url
        self._webshare_proxy: str | None = proxy_url
        # CapSolver rinde mejor proxyless (usa sus propios proxies de alta
        # calidad). Por eso ignora proxies externos (webshare) que le
        # inyecte la cadena; solo usa proxy si se construyó con uno explícito.
        self.accepts_external_proxy = False
        if use_webshare and not proxy_url:
            try:
                from .webshare import pick_proxy
                self._proxy_url = pick_proxy()
                self._webshare_proxy = self._proxy_url
            except Exception:
                pass

    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def name(self) -> str:
        return "CapSolver"

    def set_proxy(self, proxy_url: str | None) -> None:
        self._proxy_url = proxy_url
        self._webshare_proxy = proxy_url

    # ---------- API (createTask / getTaskResult) ----------
    def _create_task(self, task: dict) -> str:
        payload = {"clientKey": self.api_key, "task": task}
        r = self._session.post(f"{self.API_URL}/createTask",
                               json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errorId"):
            raise CaptchaUnsolved(
                f"capsolver createTask error: {data.get('errorCode')}: "
                f"{data.get('errorDescription')}")
        task_id = data.get("taskId")
        if not task_id:
            raise CaptchaUnsolved("capsolver createTask sin taskId")
        return task_id

    def _get_task_result(self, task_id: str) -> dict:
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
                    f"capsolver getTaskResult error: {data.get('errorCode')}: "
                    f"{data.get('errorDescription')}")
            if data.get("status") == "ready":
                return data.get("solution", {}) or {}
            # status == "processing" → seguir esperando
        raise CaptchaUnsolved(
            f"capsolver timeout ({self.timeout}s) sin resolver captcha")

    def get_balance(self) -> float:
        r = self._session.post(f"{self.API_URL}/getBalance",
                               json={"clientKey": self.api_key}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errorId"):
            raise CaptchaUnsolved(
                f"capsolver getBalance error: {data.get('errorCode')}")
        return float(data.get("balance", 0.0))

    # ---------- Métodos públicos ----------
    def solve_image(self, png_bytes, **kwargs) -> str:
        if not self.is_available():
            raise CaptchaUnsolved("CapSolver no configurado.")
        task = {
            "type": "ImageToTextTask",
            "module": kwargs.get("module", "common"),
            "body": base64.b64encode(png_bytes).decode(),
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("text", "")

    def solve_recaptcha_v2(self, sitekey: str, page_url: str,
                           enterprise: bool = False, **kwargs) -> str:
        if not self.is_available():
            raise CaptchaUnsolved("CapSolver no configurado.")
        cap_proxy = _to_capsolver_proxy(self._proxy_url)
        if cap_proxy:
            task_type = ("ReCaptchaV2EnterpriseTask" if enterprise
                         else "ReCaptchaV2Task")
            task = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": sitekey,
                "proxy": cap_proxy,
            }
        else:
            task_type = ("ReCaptchaV2EnterpriseTaskProxyLess" if enterprise
                         else "ReCaptchaV2TaskProxyLess")
            task = {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": sitekey,
            }
        action = kwargs.get("action")
        if action and enterprise:
            task["enterprisePayload"] = {"s": action}
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_recaptcha_v3(self, sitekey: str, page_url: str,
                           action: str = "verify", min_score: float = 0.3,
                           **kwargs) -> str:
        if not self.is_available():
            raise CaptchaUnsolved("CapSolver no configurado.")
        task = {
            "type": "ReCaptchaV3TaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "pageAction": action,
            "minScore": min_score,
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_hcaptcha(self, sitekey: str, page_url: str, **kwargs) -> str:
        if not self.is_available():
            raise CaptchaUnsolved("CapSolver no configurado.")
        task = {
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task)
        sol = self._get_task_result(task_id)
        return sol.get("gRecaptchaResponse", "")

    def solve_trivia(self, question: str) -> str:
        raise CaptchaUnsolved(
            "CapSolver no resuelve trivia. Use TriviaSolver.")
