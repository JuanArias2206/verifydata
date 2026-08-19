"""
api/index.py — Vercel serverless entry point for VerifyData Flask app.

En Vercel, cada request invoca esta función serverless.
La app de Flask se crea una sola vez por cold-start (caching del módulo).
Playwright NO está disponible en Vercel serverless; las fuentes
que requieren navegador devolverán error gracefully.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Ensure project root is in sys.path ────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Vercel-specific env overrides ──────────────────────────────────
os.environ.setdefault("VERIFYDATA_ENV", "production")
os.environ.setdefault("LOGIN_DISABLED", "1")
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("PORT", "8080")

# Usar /tmp para SQLite (único directorio escribible en Vercel serverless)
_tmp_db = Path("/tmp/verifydata.db")
os.environ.setdefault("VERIFYDATA_DB_PATH", str(_tmp_db))

# ── Asegurar directorios mínimos ───────────────────────────────────
_tmp_data = Path("/tmp/data")
for _d in (_tmp_data, _tmp_data / "certs", _tmp_data / "screenshots"):
    _d.mkdir(parents=True, exist_ok=True)

# ── Import app module (this does EVERYTHING: config, db, routes, api, auth) ──
import app as _app_module

# ── Get the Flask app from app.py ──────────────────────────────────
app = _app_module.app

# ── Vercel handler: expone `app` para @vercel/python ──────────────
# No se necesita main — Vercel llama a app directamente.
