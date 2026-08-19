"""
solvers/anthropic.py — Solver de captcha usando Anthropic Claude (Haiku 4.5).

Ideal para captchas complejos:
  - Trivia matemática (alternativa a TriviaSolver)
  - reCAPTCHA v2 (con image)
  - Preguntas en lenguaje natural
  - Cualquier captcha visual interpretable

Requiere la variable de entorno ANTHROPIC_API_KEY (o api_key en config).

Uso en config.yaml:
    captcha:
      solver: "anthropic"
      anthropic:
        api_key: "sk-ant-..."   # o usar ANTHROPIC_API_KEY del entorno
        model: "claude-haiku-4-5"
"""
from __future__ import annotations
import os
import base64
import time
from .base import CaptchaSolver, CaptchaUnsolved


class AnthropicSolver(CaptchaSolver):
    """Solver basado en Claude Haiku 4.5.

    Para captchas de trivia/text, usa el modelo directamente.
    Para captchas visuales, le envía la imagen como base64.
    """

    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(self, api_key: str = "", model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def name(self) -> str:
        return "AnthropicSolver"

    def _client(self):
        if not self.is_available():
            raise CaptchaUnsolved("AnthropicSolver no configurado (api_key vacío).")
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise CaptchaUnsolved(
                "anthropic SDK no instalado. pip install anthropic") from e
        return Anthropic(api_key=self.api_key)

    def _ask(self, prompt: str, image_bytes: bytes | None = None) -> str:
        """Consulta a Claude. Devuelve la respuesta en texto plano."""
        c = self._client()
        content = []
        if image_bytes:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image_bytes).decode(),
                },
            })
        content.append({"type": "text", "text": prompt})
        try:
            r = c.messages.create(
                model=self.model, max_tokens=200,
                messages=[{"role": "user", "content": content}])
        except Exception as e:
            raise CaptchaUnsolved(f"Anthropic API error: {e}")
        # Extraer texto
        for block in r.content:
            if hasattr(block, "text"):
                return block.text.strip()
        raise CaptchaUnsolved("Anthropic devolvió respuesta vacía")

    def solve_trivia(self, question: str) -> str:
        """Resuelve una pregunta de trivia."""
        prompt = (
            f"Esta es una pregunta de captcha que aparece en una página web. "
            f"Responde SOLO con la respuesta en texto plano, sin explicación.\n\n"
            f"Pregunta: {question}"
        )
        return self._ask(prompt)

    def solve_image(self, png_bytes, **kwargs) -> str:
        """Resuelve un captcha visual (lee el texto de la imagen)."""
        prompt = (
            "Esta es una imagen de captcha. Lee los caracteres de la imagen "
            "y responde SOLO con el texto que aparece, sin explicación, sin espacios, "
            "en minúsculas o mayúsculas tal como aparece."
        )
        return self._ask(prompt, image_bytes=png_bytes)

    def solve_recaptcha_v2(self, sitekey: str, page_url: str, **kwargs) -> str:
        """Para reCAPTCHA v2: Claude no puede resolverlo directamente,
        pero podría usarse para analizar la página. Por ahora no implementado."""
        raise CaptchaUnsolved(
            "AnthropicSolver no resuelve reCAPTCHA v2 directamente. "
            "Usa 2captcha o anti-captcha para ese caso.")

    def solve_hcaptcha(self, sitekey: str, page_url: str, **kwargs) -> str:
        raise CaptchaUnsolved("AnthropicSolver no resuelve hCaptcha.")
