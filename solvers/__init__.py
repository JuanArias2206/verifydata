"""solvers — Servicios de resolución de captcha."""
from .base import CaptchaSolver, CaptchaUnsolved
from .noop import NoOpSolver, get_default_solver
from .trivia import TriviaSolver
from .chain import FallbackSolver

__all__ = ["CaptchaSolver", "CaptchaUnsolved", "NoOpSolver",
           "TriviaSolver", "FallbackSolver", "get_default_solver"]


def get_solver_from_config() -> CaptchaSolver:
    """Recarga config y devuelve el solver configurado."""
    import solvers.noop as _n
    _n._default = None
    return _n.get_default_solver()
