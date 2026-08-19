"""
maintenance.py — Higiene del directorio de datos para el servicio en producción.

  - ensure_data_dirs(): crea las carpetas que las fuentes esperan encontrar
    (data/, data/screenshots/, data/certs/, data/lists/) en un clon/deploy
    nuevo, de modo que la primera búsqueda no falle por rutas inexistentes.

  - start_retention_janitor(): hilo daemon que borra periódicamente capturas y
    certificados más antiguos que N horas. En un servicio público, cada
    búsqueda genera PNGs/PDFs de evidencia; sin limpieza, el disco se llena.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

# Subcarpetas efímeras que se limpian por antigüedad.
_EPHEMERAL_SUBDIRS = ("screenshots", "certs")
# Subcarpetas que deben existir siempre (aunque no se limpien).
_REQUIRED_SUBDIRS = ("screenshots", "certs", "lists", "browser")


def ensure_data_dirs(data_dir: Path) -> None:
    """Crea data/ y sus subcarpetas requeridas si no existen. Idempotente."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        for sub in _REQUIRED_SUBDIRS:
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _sweep_once(data_dir: Path, max_age_s: float) -> int:
    """Borra archivos más antiguos que max_age_s en las subcarpetas efímeras.
    Devuelve cuántos borró. No lanza excepciones."""
    removed = 0
    now = time.time()
    for sub in _EPHEMERAL_SUBDIRS:
        d = data_dir / sub
        if not d.is_dir():
            continue
        try:
            for f in d.iterdir():
                if not f.is_file():
                    continue
                try:
                    if (now - f.stat().st_mtime) > max_age_s:
                        f.unlink()
                        removed += 1
                except Exception:
                    continue
        except Exception:
            continue
    return removed


def _purge_audit_once(audit_retention_days: int) -> None:
    """Purga audit_log según retención (best-effort). AML: por defecto 0 (nunca
    purgar) para no borrar registros de compliance por accidente."""
    if not audit_retention_days or audit_retention_days <= 0:
        return
    try:
        from db import audit_purge
        audit_purge(int(audit_retention_days))
    except Exception:
        pass


def start_retention_janitor(data_dir: Path, retention_hours: float,
                            sweep_minutes: float = 60.0,
                            audit_retention_days: int = 0
                            ) -> threading.Thread | None:
    """Arranca un hilo daemon que limpia evidencias antiguas cada
    `sweep_minutes` y, si `audit_retention_days` > 0, purga audit_log.
    Si retention_hours <= 0 y no hay retención de auditoría, no hace nada.
    Hace una primera pasada inmediata al arrancar."""
    files_on = bool(retention_hours and retention_hours > 0)
    audit_on = bool(audit_retention_days and audit_retention_days > 0)
    if not files_on and not audit_on:
        return None
    max_age_s = float(retention_hours) * 3600.0 if files_on else 0.0
    interval_s = max(60.0, float(sweep_minutes) * 60.0)

    def _loop():
        # Primera pasada al arrancar (limpia lo acumulado entre reinicios).
        if files_on:
            _sweep_once(data_dir, max_age_s)
        _purge_audit_once(audit_retention_days)
        while True:
            time.sleep(interval_s)
            try:
                if files_on:
                    _sweep_once(data_dir, max_age_s)
                _purge_audit_once(audit_retention_days)
            except Exception:
                pass

    t = threading.Thread(target=_loop, name="retention-janitor", daemon=True)
    t.start()
    return t
