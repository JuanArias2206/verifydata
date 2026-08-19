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
import tempfile
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
_certs = _tmp_data / "certs"
_screenshots = _tmp_data / "screenshots"
for _d in (_tmp_data, _certs, _screenshots):
    _d.mkdir(parents=True, exist_ok=True)

# ── Import and create the Flask app (cached at module level) ───────
from config import load_config
CFG = load_config()

# Override DB path for serverless env
CFG["database"]["path"] = os.environ.get("VERIFYDATA_DB_PATH", str(_tmp_db))

from db import init_db, set_db_path
DB_PATH = Path(CFG["database"]["path"])
set_db_path(DB_PATH)
init_db(DB_PATH)

from sources import registry
from solvers import get_default_solver
SOLVER = get_default_solver()

import ui_theme
from flask import Flask

app = Flask(__name__)

_session_secret = os.environ.get("SESSION_SECRET", "")
if not _session_secret:
    import secrets
    _session_secret = secrets.token_hex(32)
app.secret_key = _session_secret

# ── Maintenance ────────────────────────────────────────────────────
DATA = _tmp_data
try:
    from maintenance import ensure_data_dirs
    ensure_data_dirs(DATA)
except Exception:
    pass

# ── API REST ───────────────────────────────────────────────────────
from api import register_api as _register_api
_register_api(app, CFG, SOLVER, DATA)

# ── Auth ───────────────────────────────────────────────────────────
import auth as _auth
app.register_blueprint(_auth.auth_bp)

# ── Import app routes (they use @app.route) ───────────────────────
# Las rutas de app.py se definen en __main__, por eso importamos el
# módulo como side-effect.
import app as _app_module

# ── Vercel handler: expone `app` para @vercel/python ──────────────
# No se necesita main — Vercel llama a app directamente.
