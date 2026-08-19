"""
config.py — Carga de configuración desde config.yaml con defaults sensatos.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
DEFAULT_CONFIG = ROOT / "config.yaml"

_DEFAULTS: dict[str, Any] = {
    "http": {
        "user_agent": "VerifyData-Demo/1.0",
        "timeout": 30, "max_retries": 2, "backoff": 0.6,
    },
    "database": {
        # Ruta SQLite (dev/tests). Se ignora si `url` (DATABASE_URL) apunta a
        # PostgreSQL.
        "path": "data/verifydata.db",
        # PostgreSQL (prod): postgresql://user:pass@host:5432/verifydata.
        # Vacío = usar SQLite. Se inyecta por env DATABASE_URL (12-factor).
        "url": "",
        # Pool de conexiones de Postgres. Dimensionar max según la concurrencia
        # esperada: runs.py lanza 16-32 workers por búsqueda que consultan la BD.
        # Ver B5 (prueba de carga) antes de subir a prod.
        "pool_min": 2,
        "pool_max": 16,
        "pool_timeout": 30,
    },
    "captcha": {
        "solver": "noop",
        "twocaptcha": {"api_key": "", "api_url": "https://2captcha.com",
                       "default_timeout": 120},
        "anticaptcha": {"api_key": "", "api_url": "https://api.anti-captcha.com",
                        "default_timeout": 120},
    },
    "browser": {"headless": True, "max_pages": 3,
                "user_agent": "Mozilla/5.0"},
    "lists": {},
    "webapp": {"host": "127.0.0.1", "port": 5080, "debug": False,
               "results_cache_size": 100},
    "data": {
        # Retención de archivos efímeros (capturas de Playwright, certificados
        # rasterizados) para no llenar el disco en un servicio público.
        # 0 = desactivar la limpieza.
        "retention_hours": 72,
        "retention_sweep_minutes": 60,
    },
    "api": {
        # API REST pública (/api/v1). Ver api.py y API.md.
        "enabled": True,
        # Lista de claves válidas para el header X-API-Key. Si está vacía,
        # la API queda ABIERTA (útil en desarrollo; NO recomendado en prod).
        "keys": [],
        # Timeout (segundos) del endpoint síncrono POST /api/v1/searches:sync.
        # Si el run no termina en este tiempo, se responde 200 con
        # status="running" y el token para seguir por polling.
        "sync_timeout": 240,
        # Intervalo de sondeo interno (segundos) del endpoint síncrono.
        "sync_poll_interval": 1.0,
        # CORS: origen(es) permitido(s). Vacío = sin cabeceras CORS (default
        # seguro). En prod, fijar el dominio EXACTO (p. ej.
        # "https://verifydata.example.com"). NUNCA usar "*" con datos
        # personales. Configurable por env VERIFYDATA_CORS_ORIGINS.
        "cors_origins": "",
    },
    "logging": {
        "level": "INFO",
        # Retención de la tabla audit_log en días. 0 = nunca purgar (default
        # AML/SARLAFT). Env: VERIFYDATA_AUDIT_RETENTION_DAYS.
        "audit_retention_days": 0,
    },
}


def _deep_merge(a: dict, b: dict) -> dict:
    """b sobrescribe a."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_dotenv() -> None:
    """Carga ROOT/.env (KEY=VALUE por línea) en os.environ sin sobrescribir
    variables ya definidas. Sin dependencias externas. Ideal para despliegue:
    los secretos viven en .env (git-ignored) o en el entorno del proceso."""
    import os
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def _apply_env_overrides(cfg: dict) -> dict:
    """Aplica secretos y ajustes desde variables de entorno POR ENCIMA del
    YAML. Así el repositorio no contiene claves y el despliegue las inyecta
    por entorno (12-factor). Solo sobreescribe si la variable está definida."""
    import os

    def setenv(path: list[str], envvar: str) -> None:
        v = os.environ.get(envvar)
        if not v:
            return
        node = cfg
        for k in path[:-1]:
            node = node.setdefault(k, {})
        node[path[-1]] = v

    # --- Secretos de captcha / LLM ---
    setenv(["captcha", "anthropic", "api_key"], "ANTHROPIC_API_KEY")
    setenv(["captcha", "trivia", "anthropic_api_key"], "ANTHROPIC_API_KEY")
    setenv(["captcha", "capsolver", "api_key"], "CAPSOLVER_API_KEY")
    setenv(["captcha", "twocaptcha", "api_key"], "TWOCAPTCHA_API_KEY")
    setenv(["captcha", "anticaptcha", "api_key"], "ANTICAPTCHA_API_KEY")
    setenv(["captcha", "twocaptcha", "proxy", "webshare_api_key"],
           "WEBSHARE_API_KEY")

    # --- Retención de datos efímeros ---
    rh = os.environ.get("VERIFYDATA_RETENTION_HOURS")
    if rh is not None:
        try:
            cfg.setdefault("data", {})["retention_hours"] = int(rh)
        except ValueError:
            pass

    # --- Base de datos ---
    setenv(["database", "path"], "VERIFYDATA_DB_PATH")
    # DATABASE_URL: si apunta a PostgreSQL, db.py usa PG+pool en vez de SQLite.
    setenv(["database", "url"], "DATABASE_URL")
    for envvar, key in (("VERIFYDATA_PG_POOL_MIN", "pool_min"),
                        ("VERIFYDATA_PG_POOL_MAX", "pool_max"),
                        ("VERIFYDATA_PG_POOL_TIMEOUT", "pool_timeout")):
        v = os.environ.get(envvar)
        if v:
            try:
                cfg.setdefault("database", {})[key] = int(float(v))
            except ValueError:
                pass

    # --- Rutas / servidor ---
    setenv(["webapp", "host"], "HOST")
    if os.environ.get("PORT"):
        try:
            cfg.setdefault("webapp", {})["port"] = int(os.environ["PORT"])
        except ValueError:
            pass

    # --- API keys del servicio (lista separada por comas) ---
    api_keys = os.environ.get("VERIFYDATA_API_KEYS")
    if api_keys:
        cfg.setdefault("api", {})["keys"] = [
            k.strip() for k in api_keys.split(",") if k.strip()]

    # --- CORS: origen permitido en prod (no usar "*" con datos personales) ---
    setenv(["api", "cors_origins"], "VERIFYDATA_CORS_ORIGINS")

    # --- Retención de auditoría (días) ---
    ard = os.environ.get("VERIFYDATA_AUDIT_RETENTION_DAYS")
    if ard is not None:
        try:
            cfg.setdefault("logging", {})["audit_retention_days"] = int(ard)
        except ValueError:
            pass

    return cfg


def _resolve_paths(cfg: dict) -> dict:
    """Convierte rutas relativas en absolutas RELATIVAS AL PROYECTO (ROOT),
    no al directorio de trabajo del proceso. Evita que systemd/gunicorn con
    otro WorkingDirectory apunten la BD a un lugar equivocado."""
    db_path = cfg.get("database", {}).get("path", "data/verifydata.db")
    p = Path(db_path)
    if not p.is_absolute():
        p = ROOT / p
    cfg["database"]["path"] = str(p)
    return cfg


def load_config(path: Path | str | None = None) -> dict:
    """Carga config.yaml, lo combina con defaults, aplica overrides de entorno
    (.env + variables de proceso) y resuelve rutas contra el proyecto."""
    import copy
    _load_dotenv()
    cfg = copy.deepcopy(_DEFAULTS)
    p = Path(path) if path else DEFAULT_CONFIG
    if p.exists():
        try:
            import yaml
            with open(p) as f:
                data = yaml.safe_load(f) or {}
            cfg = _deep_merge(cfg, data)
        except Exception:
            pass
    cfg = _apply_env_overrides(cfg)
    cfg = _resolve_paths(cfg)
    return cfg


if __name__ == "__main__":
    import json
    print(json.dumps(load_config(), indent=2, ensure_ascii=False))
