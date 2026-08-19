#!/usr/bin/env python3
"""
api.py — API REST pública de VerifyData (Blueprint montado en /api/v1).

Expone el motor de búsqueda (runs.py + sources/registry) como un servicio
JSON limpio, versionado y documentado, SIN tocar la UI HTML de app.py.

Diseño
------
  - Autenticación por API key (header `X-API-Key` o `Authorization: Bearer`).
    Configurable en config.yaml → api.keys. Lista vacía = API abierta (dev).
  - Dos modos de interacción:
      * Asíncrono:  POST /api/v1/searches          → 202 { token, links }
                    GET  /api/v1/searches/{token}   → estado + resultados
      * Síncrono:   POST /api/v1/searches/sync      → bloquea hasta terminar
                    (con timeout; si expira devuelve el token para polling)
  - Descubrimiento:   GET /api/v1/sources           → catálogo de fuentes
  - Reporte:          GET /api/v1/searches/{token}/report → PDF
  - Salud:            GET /api/v1/health
  - Documentación:    GET /api/v1/docs (Swagger UI) · /api/v1/openapi.json

Registro (desde app.py):
    from api import register_api
    register_api(app, CFG, SOLVER, DATA)
"""
from __future__ import annotations

import os
import re
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

api_bp = Blueprint("api_v1", __name__)

API_VERSION = "1.0.0"

# Estado inyectado en register_api() para no reconstruirlo por request.
_STATE: dict[str, Any] = {
    "cfg": {},           # sub-config de config.yaml → api
    "solver": None,      # solver de captcha (compartido con la webapp)
    "data_dir": None,    # Path a demo/data
    "cat_map": {},       # name -> category
    "url_map": {},       # name -> source_url
    "source_meta": [],   # catálogo serializable de fuentes
    "source_names": set(),
}


# ==============================================================
#  Helpers de respuesta / errores (envelope JSON consistente)
# ==============================================================

def _error(status: int, code: str, message: str, **extra) -> Response:
    """Envelope de error uniforme para toda la API."""
    body = {"error": {"code": code, "message": message}}
    if extra:
        body["error"].update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


# ==============================================================
#  Autenticación por API key
# ==============================================================

def _is_dev_env() -> bool:
    """True solo si VERIFYDATA_ENV indica desarrollo. Por defecto (variable
    ausente) se asume PRODUCCIÓN: el modo abierto no debe activarse por olvido
    de configurar una variable."""
    return os.environ.get("VERIFYDATA_ENV", "").strip().lower() in (
        "dev", "development", "local")


def _check_key() -> Response | None:
    """Devuelve None si la petición está autorizada, o un 401 si no.

    Sin claves configuradas (api.keys vacío) la API SOLO queda abierta en
    entorno de desarrollo (VERIFYDATA_ENV=dev). En producción, la ausencia de
    claves cierra el acceso (fail-closed) en vez de exponer datos personales."""
    keys = _STATE["cfg"].get("keys") or []
    if not keys:
        if _is_dev_env():
            return None  # modo abierto (solo desarrollo)
        return _error(
            401, "unauthorized",
            "La API no tiene claves configuradas. Defina VERIFYDATA_API_KEYS "
            "(o api.keys en config.yaml) para habilitar el acceso en "
            "producción.")
    provided = request.headers.get("X-API-Key")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[7:].strip()
    if provided and provided in keys:
        return None
    return _error(
        401, "unauthorized",
        "API key ausente o inválida. Envíe el header 'X-API-Key: <clave>' "
        "o 'Authorization: Bearer <clave>'.")


def require_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        err = _check_key()
        if err is not None:
            return err
        return fn(*args, **kwargs)
    return wrapper


# ==============================================================
#  CORS
# ==============================================================

@api_bp.after_request
def _add_cors_headers(resp: Response) -> Response:
    origins = _STATE["cfg"].get("cors_origins", "*")
    if origins:
        resp.headers["Access-Control-Allow-Origin"] = origins
        resp.headers["Access-Control-Allow-Headers"] = \
            "Content-Type, X-API-Key, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ==============================================================
#  Serialización de resultados
# ==============================================================

def _to_fetch_url(x: Any) -> Any:
    """Convierte una referencia de evidencia en una URL descargable por un
    cliente externo. Las rutas locales relativas a data/ (p. ej.
    'screenshots/foo.png', 'certs/bar.pdf') se sirven por la ruta pública
    /download/<path>. Las URLs http(s) o ya absolutas se dejan intactas."""
    if not x or not isinstance(x, str):
        return x
    if x.startswith(("http://", "https://", "/")):
        return x
    return "/download/" + x.lstrip("/")


def _normalize_state(h: dict) -> str:
    """Estado normalizado y estable por fuente para consumidores de la API.

    Prioridad: captcha > error > status fino explícito > match/no_match."""
    if h.get("captcha_required"):
        return "captcha_required"
    if h.get("error"):
        return "error"
    st = h.get("status")
    if st:
        return st
    return "match" if h.get("matched") else "no_match"


def _serialize_result(name: str, h: dict) -> dict:
    return {
        "source": name,
        "category": _STATE["cat_map"].get(name, "Otras"),
        "source_url": h.get("source_url") or _STATE["url_map"].get(name, ""),
        "matched": bool(h.get("matched")),
        "state": _normalize_state(h),
        "status": h.get("status"),
        "confidence": h.get("confidence"),
        "summary": h.get("summary") or None,
        "matched_name": h.get("matched_name"),
        "matched_document": h.get("matched_document"),
        "role": h.get("role"),
        "case_number": h.get("case_number"),
        "dataset_version": h.get("dataset_version"),
        "dataset_records": h.get("dataset_records") or 0,
        "error": h.get("error"),
        "error_type": h.get("error_type"),
        "notice": h.get("notice"),
        "requires_manual_review": bool(h.get("requires_manual_review")),
        "notes": h.get("notes"),
        "evidence_urls": [_to_fetch_url(u) for u in (h.get("evidence_urls") or [])],
        "download_url": _to_fetch_url(h.get("download_url")),
        "elapsed_s": round(float(h.get("elapsed_s") or 0.0), 2),
        "details": h.get("details") or [],
    }


def _serialize_run(d: dict, include_results: bool = True) -> dict:
    token = d.get("token")
    sources = d.get("sources") or {}
    results = [
        _serialize_result(name, h)
        for name, h in sources.items()
        if isinstance(h, dict)
    ]
    results.sort(key=lambda r: r["source"].lower())
    total = int(d.get("total", 0) or 0)
    done = len(results)
    resp = {
        "token": token,
        "status": "completed" if d.get("completed") else "running",
        "query": d.get("query") or {},
        "started_at": d.get("started_at"),
        "progress": {
            "total": total,
            "completed": done,
            "pending": max(0, total - done),
        },
        "summary": {
            "sources_total": total,
            "matches": int(d.get("matched", 0) or 0),
            "captcha_blocked": int(d.get("captcha", 0) or 0),
            "errors": int(d.get("error", 0) or 0),
        },
        "links": {
            "self": f"/api/v1/searches/{token}",
            "report_pdf": f"/api/v1/searches/{token}/report",
        },
    }
    if include_results:
        resp["results"] = results
    return resp


# ==============================================================
#  Parsing / validación del cuerpo de búsqueda
# ==============================================================

def _parse_search_body() -> tuple[dict | None, Response | None]:
    """Extrae y valida el cuerpo de una petición de búsqueda.

    Acepta JSON (preferido) o form-urlencoded. Devuelve (query, None) si es
    válido, o (None, error_response) si no."""
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict() if request.form else {}
    if not isinstance(data, dict):
        return None, _error(400, "invalid_body",
                            "El cuerpo debe ser un objeto JSON.")

    nombre = (data.get("nombre") or "").strip()
    cedula = (data.get("cedula") or "").strip()
    fecha_exp = (data.get("fecha_exp") or "").strip()

    if not nombre and not cedula:
        return None, _error(
            422, "missing_query",
            "Debe indicar al menos 'nombre' o 'cedula'.")

    # sources: "all" (default) | "featured" | lista de nombres exactos.
    sources = data.get("sources", "all")
    if isinstance(sources, str):
        sources_mode: Any = sources.strip().lower() or "all"
        if sources_mode not in ("all", "featured"):
            return None, _error(
                422, "invalid_sources",
                "'sources' como texto solo admite 'all' o 'featured'. "
                "Para un subconjunto, envíe una lista de nombres exactos "
                "(ver GET /api/v1/sources).")
    elif isinstance(sources, list):
        names = [str(x) for x in sources if str(x).strip()]
        if not names:
            return None, _error(422, "invalid_sources",
                                "La lista 'sources' está vacía.")
        unknown = [n for n in names if n not in _STATE["source_names"]]
        if unknown:
            return None, _error(
                422, "unknown_sources",
                "Hay nombres de fuente no reconocidos.",
                unknown=unknown)
        sources_mode = names
    else:
        return None, _error(422, "invalid_sources",
                            "'sources' debe ser 'all', 'featured' o una lista.")

    query = {
        "nombre": nombre,
        "cedula": cedula,
        "fecha_exp": fecha_exp,
        "__sources": sources_mode,
    }
    return query, None


def _launch(query: dict) -> str:
    import os
    _is_vercel = os.environ.get("VERIFYDATA_ENV") == "production"
    if _is_vercel:
        from runs import run_search_progressive_inline
        run_fn = run_search_progressive_inline
    else:
        from runs import run_search_progressive
        run_fn = run_search_progressive
    from logging_config import audit
    token = run_fn(
        query, query.get("cedula") or None,
        query.get("fecha_exp") or None,
        _STATE["solver"], skip_browser=False)
    # Auditoría AML de las búsquedas por API (quién=cliente API, qué, cuándo).
    audit("search", via="api", cedula=query.get("cedula") or None,
          nombre=query.get("nombre") or None,
          sources=query.get("__sources"), token=token)
    return token


# ==============================================================
#  Endpoints
# ==============================================================

@api_bp.route("/health", methods=["GET"])
def health():
    """Sonda de salud. Público (sin API key)."""
    from sources import registry
    solver = _STATE["solver"]
    return jsonify({
        "status": "ok",
        "service": "verifydata-api",
        "version": API_VERSION,
        "sources_registered": len(registry.all_sources()),
        "captcha_solver": getattr(solver, "name", None),
        "captcha_available": bool(
            solver.is_available()) if solver is not None else False,
        "auth_required": bool(_STATE["cfg"].get("keys")),
    })


@api_bp.route("/sources", methods=["GET"])
def list_sources():
    """Catálogo de fuentes disponibles. Público (metadatos, sin ejecutar).

    Filtros opcionales: ?category=<cat>  ?captcha=true|false"""
    meta = _STATE["source_meta"]
    cat = request.args.get("category")
    if cat:
        meta = [m for m in meta if m["category"].lower() == cat.lower()]
    captcha = request.args.get("captcha")
    if captcha is not None:
        want = captcha.strip().lower() in ("1", "true", "yes")
        meta = [m for m in meta if m["requires_captcha"] == want]
    cats = sorted({m["category"] for m in _STATE["source_meta"]})
    return jsonify({
        "total": len(meta),
        "categories": cats,
        "sources": meta,
    })


@api_bp.route("/searches", methods=["POST"])
@require_key
def create_search():
    """Inicia una búsqueda ASÍNCRONA. Responde 202 con el token para polling."""
    query, err = _parse_search_body()
    if err is not None:
        return err
    token = _launch(query)
    from runs import get_run
    state = get_run(token)
    body = _serialize_run(state.to_dict(), include_results=False) if state else {
        "token": token, "status": "running"}
    resp = jsonify(body)
    resp.status_code = 202
    resp.headers["Location"] = f"/api/v1/searches/{token}"
    resp.headers["Retry-After"] = "3"
    return resp


@api_bp.route("/searches/sync", methods=["POST"])
@require_key
def create_search_sync():
    """Búsqueda SÍNCRONA: bloquea hasta que el run termina o expira el timeout.

    Si el run no acaba en `api.sync_timeout` segundos, responde 200 con
    status='running' y los resultados parciales; el cliente puede seguir por
    polling usando links.self."""
    import time
    query, err = _parse_search_body()
    if err is not None:
        return err
    token = _launch(query)
    from runs import get_run

    timeout = float(_STATE["cfg"].get("sync_timeout", 240) or 240)
    interval = float(_STATE["cfg"].get("sync_poll_interval", 1.0) or 1.0)
    deadline = time.monotonic() + timeout
    state = get_run(token)
    while state is not None and not state.completed:
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
        state = get_run(token)

    if state is None:
        return _error(500, "run_lost", "El run desapareció del estado interno.")
    d = state.to_dict()
    body = _serialize_run(d)
    resp = jsonify(body)
    # 200 si completó; 200 con status=running si expiró el timeout.
    resp.status_code = 200
    if not d.get("completed"):
        resp.headers["Retry-After"] = "5"
    return resp


@api_bp.route("/searches/<token>", methods=["GET"])
@require_key
def get_search(token: str):
    """Estado + resultados de una búsqueda (polling del modo asíncrono)."""
    from runs import get_run
    state = get_run(token)
    if state is None:
        return _error(404, "not_found",
                      f"No existe una búsqueda con token '{token}'.")
    return jsonify(_serialize_run(state.to_dict()))


@api_bp.route("/searches/<token>/report", methods=["GET"])
@require_key
def get_report(token: str):
    """Descarga el reporte PDF de la búsqueda."""
    from runs import get_run
    state = get_run(token)
    if state is None:
        return _error(404, "not_found",
                      f"No existe una búsqueda con token '{token}'.")
    if not state.sources:
        resp = _error(202, "run_in_progress",
                      "La búsqueda aún no tiene resultados; reintente en unos "
                      "segundos.", token=token)
        resp.headers["Retry-After"] = "5"
        return resp
    from report import generate_pdf
    from sources import registry, Hit
    url_map = {s.name: s.source_url for s in registry.all_sources()}
    hits = []
    for name, d in state.sources.items():
        try:
            h = Hit(**{k: v for k, v in d.items() if k != "source_url"})
            h.source_url = url_map.get(name, "")
            hits.append(h)
        except Exception:
            continue
    pdf_bytes = generate_pdf(state.query, hits)
    # No incluir la cédula (dato personal, Ley 1581) en el nombre del archivo.
    # Se usa el token (no personal), sanitizado para evitar header injection.
    safe_token = re.sub(r"[^0-9A-Za-z_-]", "", token)[:32] or "reporte"
    fname = f"verifydata_{safe_token}.pdf"
    from logging_config import audit
    ced = state.query.get("cedula") if isinstance(state.query, dict) else None
    audit("pdf_download", via="api", token=token, cedula=ced or None)
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename=\"{fname}\"; "
                             f"filename*=UTF-8''{fname}"})


# ==============================================================
#  Documentación (OpenAPI + Swagger UI)
# ==============================================================

@api_bp.route("/openapi.json", methods=["GET"])
def openapi_json():
    from openapi_spec import build_spec
    return jsonify(build_spec(_STATE["source_meta"], API_VERSION))


@api_bp.route("/docs", methods=["GET"])
def docs():
    """Swagger UI (carga la spec desde /api/v1/openapi.json)."""
    html = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>VerifyData API — Documentación</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet"
        href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #fafafa; }
    .topbar { display: none; }
    #brand { padding: 14px 22px; background: #0A1929; color: #fff;
             font-family: -apple-system, Segoe UI, sans-serif; }
    #brand b { color: #6941F4; letter-spacing: 3px; }
  </style>
</head>
<body>
  <div id="brand"><b>VERIFYDATA</b> &nbsp;·&nbsp; API v1</div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout"
    });
  </script>
</body>
</html>"""
    return Response(html, mimetype="text/html")


@api_bp.route("/searches/sync", methods=["OPTIONS"])
@api_bp.route("/searches", methods=["OPTIONS"])
@api_bp.route("/searches/<token>", methods=["OPTIONS"])
def _cors_preflight(token: str | None = None):
    return ("", 204)


# ==============================================================
#  Registro
# ==============================================================

def register_api(app, cfg: dict, solver, data_dir: Path):
    """Monta el Blueprint en /api/v1 y precalcula el catálogo de fuentes."""
    _STATE["cfg"] = (cfg or {}).get("api", {}) or {}
    _STATE["solver"] = solver
    _STATE["data_dir"] = data_dir

    from sources import registry
    srcs = registry.all_sources()
    _STATE["cat_map"] = {s.name: getattr(s, "category", "Otras") for s in srcs}
    _STATE["url_map"] = {s.name: getattr(s, "source_url", "") for s in srcs}
    _STATE["source_names"] = {s.name for s in srcs}
    _STATE["source_meta"] = sorted((
        {
            "name": s.name,
            "category": getattr(s, "category", "Otras"),
            "source_url": getattr(s, "source_url", ""),
            "requires_captcha": bool(getattr(s, "requires_captcha", False)),
            "captcha_type": getattr(s, "captcha_type", None),
        }
        for s in srcs
    ), key=lambda m: (m["category"].lower(), m["name"].lower()))

    if not _STATE["cfg"].get("enabled", True):
        return app

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    return app
