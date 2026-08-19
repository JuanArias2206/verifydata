"""
logging_config.py — Logging estructurado (JSON) y auditoría para VerifyData.

Reemplaza los `print()` sueltos por logging configurable y añade un canal de
AUDITORÍA para compliance AML (Ley 1581 / Superintendencia): quién consulta qué
cédula/nombre y cuándo, logins, cambios de RBAC, descargas de PDF y refresh de
listas.

Uso:
    from logging_config import setup_logging, audit, get_logger
    setup_logging()                       # una vez, al arrancar la app
    log = get_logger(__name__)
    log.info("mensaje")
    audit("search", user="a@b.com", cedula="123", sources="all")

Diseño
------
  - Formato JSON por línea (una entrada = un objeto) → fácil de ingerir en un
    colector (Loki, ELK, CloudWatch) sin parsear texto libre.
  - Salida a stdout (systemd/Docker la capturan) y, si se define
    VERIFYDATA_LOG_DIR, también a `audit.log` y `app.log` rotados.
  - REGLA: nunca registrar el OTP, el código, tokens de sesión ni secretos.
    Las funciones de auditoría reciben solo campos ya seguros.
  - La persistencia en tabla `audit_log` (con retención) llega con la migración
    a PostgreSQL (Bloque B); este módulo deja el evento listo para volcarse ahí.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_LOGGER_NAME = "verifydata.audit"
_configured = False


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Campos extra estructurados (audit(...) los inyecta en record.fields).
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """Configura el logging raíz. Idempotente (seguro de llamar más de una vez).

    - Nivel: argumento `level` > env VERIFYDATA_LOG_LEVEL > "INFO".
    - Si VERIFYDATA_LOG_DIR está definido, escribe también a ficheros rotados.
    """
    global _configured
    if _configured:
        return

    lvl_name = (level or os.environ.get("VERIFYDATA_LOG_LEVEL", "INFO")).upper()
    lvl = getattr(logging, lvl_name, logging.INFO)

    fmt = JsonFormatter()
    root = logging.getLogger()
    root.setLevel(lvl)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    log_dir = os.environ.get("VERIFYDATA_LOG_DIR")
    if log_dir:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        app_fh = logging.handlers.RotatingFileHandler(
            d / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5,
            encoding="utf-8")
        app_fh.setFormatter(fmt)
        root.addHandler(app_fh)

        # La auditoría, además, a su propio fichero (retención independiente).
        audit_fh = logging.handlers.RotatingFileHandler(
            d / "audit.log", maxBytes=10 * 1024 * 1024, backupCount=20,
            encoding="utf-8")
        audit_fh.setFormatter(fmt)
        audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
        audit_logger.addHandler(audit_fh)
        # El evento también sube al root (stdout); no duplicar en fichero app.
        audit_logger.propagate = True

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def route_print_to_logger(name: str):
    """Devuelve un reemplazo drop-in de `print()` que enruta la salida por
    logging (nivel INFO), ignorando de forma segura `flush`/`end`/`sep`/`file`.

    Uso (una línea al inicio del módulo, A8):
        from logging_config import route_print_to_logger
        print = route_print_to_logger(__name__)

    Permite migrar módulos con muchos print() de diagnóstico (p.ej. los scrapers
    de sources/, con prints multilínea) a logging SIN reescribir cada llamada ni
    arriesgar romper su lógica. La salida deja de ir a stdout crudo y pasa a los
    handlers configurados (JSON, ficheros)."""
    lg = logging.getLogger(name)

    def _print(*args: Any, sep: str = " ", **_ignored: Any) -> None:
        lg.info(sep.join(str(a) for a in args))

    return _print


def audit(event: str, **fields: Any) -> None:
    """Registra un evento de auditoría (compliance AML).

    `event` es un identificador estable ('search', 'login_ok', 'login_fail',
    'pdf_download', 'rbac_change', 'lists_refresh', 'otp_request'). Los `fields`
    deben venir YA saneados: NUNCA pasar el OTP, el código, tokens de sesión ni
    contraseñas.

    Se registra en dos sitios:
      1. Log estructurado (stdout / fichero) — observabilidad.
      2. Tabla `audit_log` (persistente, con retención) — compliance ante la
         Superintendencia. La escritura en BD es best-effort: si falla, el
         evento igual queda en el log y NO se rompe la petición.
    """
    logging.getLogger(AUDIT_LOGGER_NAME).info(
        event, extra={"fields": {"event": event, **fields}})
    try:
        from db import audit_write
        actor = fields.get("user") or fields.get("by") or fields.get("email")
        audit_write(event, actor, fields)
    except Exception:  # noqa: BLE001
        logging.getLogger(AUDIT_LOGGER_NAME).warning(
            "no se pudo persistir el evento de auditoría en audit_log",
            exc_info=True)
