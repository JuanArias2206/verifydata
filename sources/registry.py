"""
sources/registry.py — Registro central de fuentes.

Uso:
    from sources import registry
    from sources.base import Source, Hit

    @registry.register
    class MySource:
        name = "Mi Fuente"
        source_url = "https://..."
        category = "..."
        requires_captcha = False
        captcha_type = None
        def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
            return Hit(source=self.name, matched=True, summary="...",
                       details=[...])

    for src in registry.all_sources():
        print(src.name)
"""
from __future__ import annotations
from typing import Any, Callable
from .base import Hit, safe_fetch, Source
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)

_REGISTRY: list[Source] = []


def register(cls: type) -> type:
    """Decorador que añade una clase al registry. La clase NO se instancia
    aquí; cada source se instancia por llamada para tener estado fresco.
    Se valida que tenga los atributos mínimos."""
    required = ("name", "source_url", "category")
    for attr in required:
        if not hasattr(cls, attr):
            raise TypeError(
                f"Source {cls.__name__} missing required attribute '{attr}'")
    if not hasattr(cls, "fetch"):
        raise TypeError(f"Source {cls.__name__} missing method 'fetch'")
    # defaults
    if not hasattr(cls, "requires_captcha"): cls.requires_captcha = False
    if not hasattr(cls, "captcha_type"): cls.captcha_type = None
    _REGISTRY.append(cls)
    return cls


def all_sources() -> list[Source]:
    """Devuelve instancias frescas de todas las fuentes registradas."""
    return [cls() for cls in _REGISTRY]


def sources_by_category() -> dict[str, list[Source]]:
    """Agrupa las fuentes por categoría."""
    out: dict[str, list[Source]] = {}
    for s in all_sources():
        out.setdefault(s.category, []).append(s)
    return out


def run_all(nombre: str, cedula: str | None,
            fecha_exp: str | None = None,
            solver: Any = None,
            skip_browser: bool = False) -> list[Hit]:
    """Ejecuta todas las fuentes en paralelo y devuelve sus Hits.

    Si skip_browser=True, excluye las fuentes que requieren Playwright
    (más lentas). Útil para tests y respuestas rápidas."""
    import concurrent.futures as cf
    hits: list[Hit] = []
    sources = all_sources()
    if skip_browser:
        # Excluir fuentes cuyo nombre contiene "(browser)"
        sources = [s for s in sources if "(browser)" not in s.name]
    with cf.ThreadPoolExecutor(max_workers=min(20, len(sources) or 1)) as ex:
        futs = {ex.submit(safe_fetch, s, nombre, cedula, fecha_exp, solver): s
                for s in sources}
        for fut in cf.as_completed(futs):
            s = futs[fut]
            try:
                h = fut.result()
                h.source_url = s.source_url
                hits.append(h)
            except Exception as e:
                hits.append(Hit(source=s.name, matched=False, summary="",
                                error=f"{type(e).__name__}: {e}",
                                source_url=s.source_url))
    return hits


def clear() -> None:
    """Limpia el registry (útil para tests)."""
    _REGISTRY.clear()
