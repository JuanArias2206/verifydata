"""
solvers/base.py — Interfaz abstracta para servicios de resolución de captcha.

Las fuentes que requieren captcha reciben un `solver` en su método
`fetch()`. Si el solver es `NoOpSolver` (default), las fuentes devolverán
un Hit con `captcha_required=True` y `notice="..."` en vez de intentar
resolver.

Cuando llegue el servicio real (2captcha, anti-captcha, etc), crear
`solvers/twocaptcha.py` con la implementación y registrar.
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class CaptchaSolver(ABC):
    """Interfaz que todo solver debe implementar."""

    @abstractmethod
    def solve_image(self, png_bytes: bytes, **kwargs) -> str:
        """Resuelve un captcha de imagen. Devuelve el texto. Lanza
        CaptchaUnsolved si no puede."""

    def solve_recaptcha_v2(self, sitekey: str, page_url: str,
                           **kwargs) -> str:
        raise NotImplementedError

    def solve_hcaptcha(self, sitekey: str, page_url: str,
                       **kwargs) -> str:
        raise NotImplementedError

    def is_available(self) -> bool:
        """True si el solver está configurado y listo."""
        return False

    @property
    def name(self) -> str:
        return self.__class__.__name__


# Excepción re-exportada para que las fuentes no tengan que importar
# de sources.base
class CaptchaUnsolved(Exception):
    pass
