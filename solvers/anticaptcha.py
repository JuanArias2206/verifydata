"""
solvers/anticaptcha.py — Solver anti-captcha.com.

Documentación API: https://anti-captcha.com/apidoc

Uso:
  En config.yaml:
    captcha:
      solver: "anticaptcha"
      anticaptcha:
        api_key: "tu-api-key"

  Programáticamente:
    from solvers.anticaptcha import AntiCaptchaSolver
    s = AntiCaptchaSolver(api_key="tu-api-key")
    if s.is_available():
        answer = s.solve_image(png_bytes)
        token = s.solve_recaptcha_v2(sitekey, page_url)
"""
from __future__ import annotations
import time
import requests
from .base import CaptchaSolver, CaptchaUnsolved


class AntiCaptchaSolver(CaptchaSolver):
    API_URL = "https://api.anti-captcha.com"
    DEFAULT_TIMEOUT = 180
    POLL_INTERVAL = 5

    def __init__(self, api_key: str = "", timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()

    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def name(self) -> str:
        return "AntiCaptchaSolver"

    def _create_task(self, task: dict) -> int:
        """Crea una tarea en anti-captcha. Retorna task_id."""
        r = self._session.post(f"{self.API_URL}/createTask",
                                json={"clientKey": self.api_key, "task": task},
                                timeout=30)
        r.raise_for_status()
        d = r.json()
        if d.get("errorId"):
            raise CaptchaUnsolved(f"anti-captcha createTask error: {d.get('errorDescription')}")
        return d["taskId"]

    def _get_result(self, task_id: int) -> str:
        """Consulta el resultado. Retorna la respuesta cuando esté lista."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            r = self._session.post(f"{self.API_URL}/getTaskResult",
                                    json={"clientKey": self.api_key,
                                          "taskId": task_id},
                                    timeout=30)
            r.raise_for_status()
            d = r.json()
            if d.get("errorId"):
                raise CaptchaUnsolved(f"anti-captcha getTaskResult error: {d.get('errorDescription')}")
            if d.get("status") == "ready":
                sol = d.get("solution", {})
                return sol.get("text") or sol.get("gRecaptchaResponse") or ""
            # "processing" — seguir esperando
        raise CaptchaUnsolved(f"anti-captcha timeout después de {self.timeout}s")

    def solve_image(self, png_bytes, **kwargs) -> str:
        """Resuelve un captcha de imagen."""
        if not self.is_available():
            raise CaptchaUnsolved("AntiCaptchaSolver no configurado.")
        task = {
            "type": "ImageToTextTask",
            "body": __import__("base64").b64encode(png_bytes).decode(),
            "phrase": False,
            "case": True,
            "numeric": 0,
            "math": False,
            "minLength": 4,
            "maxLength": 20,
        }
        task_id = self._create_task(task)
        return self._get_result(task_id)

    def solve_recaptcha_v2(self, sitekey: str, page_url: str, **kwargs) -> str:
        """Resuelve un reCAPTCHA v2 (no proxy)."""
        if not self.is_available():
            raise CaptchaUnsolved("AntiCaptchaSolver no configurado.")
        task = {
            "type": "NoCaptchaTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task)
        return self._get_result(task_id)

    def solve_hcaptcha(self, sitekey: str, page_url: str, **kwargs) -> str:
        """Resuelve un hCaptcha (no proxy)."""
        if not self.is_available():
            raise CaptchaUnsolved("AntiCaptchaSolver no configurado.")
        task = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        task_id = self._create_task(task)
        return self._get_result(task_id)
