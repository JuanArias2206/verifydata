"""
solvers/noop.py — Solver que no resuelve nada.

Lee config.yaml para elegir el solver real. Cuando se activa uno
con API key, las fuentes con captcha se resuelven automáticamente.

Opciones:
  - noop: NoOpSolver
  - trivia: TriviaSolver local (preguntas matemáticas)
  - twocaptcha: TwoCaptchaSolver (requiere api_key)
  - anticaptcha: AntiCaptchaSolver (requiere api_key)
  - anthropic: AnthropicSolver con Claude Haiku 4.5 (requiere api_key)
"""
from __future__ import annotations
from .base import CaptchaSolver, CaptchaUnsolved


class NoOpSolver(CaptchaSolver):
    def solve_image(self, png_bytes, **kwargs) -> str:
        raise CaptchaUnsolved("NoOpSolver no resuelve captcha.")

    def solve_recaptcha_v2(self, sitekey, page_url, **kwargs) -> str:
        raise CaptchaUnsolved("NoOpSolver no resuelve recaptcha_v2.")

    def solve_hcaptcha(self, sitekey, page_url, **kwargs) -> str:
        raise CaptchaUnsolved("NoOpSolver no resuelve hcaptcha.")

    def is_available(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "NoOpSolver"


_default: CaptchaSolver | None = None


def get_default_solver() -> CaptchaSolver:
    """Devuelve el solver configurado (lee config.yaml)."""
    global _default
    if _default is not None:
        return _default
    try:
        from config import load_config
        cfg = load_config()
        kind = cfg.get("captcha", {}).get("solver", "noop").lower()
        if kind in ("chain", "auto", "fallback"):
            from .factory import build_chain
            _default = build_chain(cfg)
        elif kind == "capsolver":
            from .capsolver import CapSolver
            cap = cfg.get("captcha", {}).get("capsolver", {})
            _default = CapSolver(api_key=cap.get("api_key", ""),
                                 timeout=cap.get("default_timeout", 180))
        elif kind == "trivia":
            from .trivia import TriviaSolver
            tri_cfg = cfg.get("captcha", {}).get("trivia", {})
            _default = TriviaSolver(
                anthropic_api_key=tri_cfg.get("anthropic_api_key", ""),
                model=tri_cfg.get("anthropic_model", "claude-haiku-4-5"),
            )
        elif kind in ("twocaptcha", "2captcha"):
            from .twocaptcha import TwoCaptchaSolver
            api_key = cfg.get("captcha", {}).get("twocaptcha", {}).get("api_key", "")
            proxy_cfg = cfg.get("captcha", {}).get("twocaptcha", {}).get("proxy", {})
            use_proxy = bool(proxy_cfg.get("enabled", False))
            _default = TwoCaptchaSolver(api_key=api_key, use_webshare=use_proxy)
        elif kind == "anticaptcha":
            from .anticaptcha import AntiCaptchaSolver
            api_key = cfg.get("captcha", {}).get("anticaptcha", {}).get("api_key", "")
            _default = AntiCaptchaSolver(api_key=api_key)
        elif kind == "anthropic":
            from .anthropic import AnthropicSolver
            api_key = cfg.get("captcha", {}).get("anthropic", {}).get("api_key", "")
            model = cfg.get("captcha", {}).get("anthropic", {}).get(
                "model", "claude-haiku-4-5")
            _default = AnthropicSolver(api_key=api_key, model=model)
        else:
            _default = NoOpSolver()
    except Exception:
        _default = NoOpSolver()
    return _default
