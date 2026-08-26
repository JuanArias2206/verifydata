#!/usr/bin/env python3
"""
app.py — Flask webapp para VerifyData Demo.

Usa el nuevo sistema de registry:
  - Las fuentes se registran con @register (sources/*.py)
  - Captcha solver se inyecta vía solvers
  - Bulk lists se cachean en SQLite vía lists/manager.py
  - Config se lee de config.yaml

Uso:
  PORT=5070 python3 app.py
  → http://localhost:5070
"""
from __future__ import annotations
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, request, render_template_string, redirect, url_for, send_file, abort

# --- Cargar config primero ---
from config import load_config
CFG = load_config()

# --- Logging estructurado + auditoría (A8) --------------------------------
from logging_config import setup_logging, get_logger, audit
setup_logging(CFG.get("logging", {}).get("level"))
log = get_logger("verifydata.app")

# --- Inicializar registry y solver ---
from sources import registry, Hit
from solvers import get_default_solver
SOLVER = get_default_solver()

# --- Sistema de diseño compartido "VerifyData" ---
import ui_theme

# --- DB ---
# La ruta viene de config ya resuelta a absoluta relativa al proyecto (ver
# config._resolve_paths). set_db_path la fija para init_db() y get_db().
from db import init_db, set_db_path
DB_PATH = Path(CFG["database"]["path"])
set_db_path(DB_PATH)
init_db(DB_PATH)

app = Flask(__name__)
app.config["RESULTS_CACHE_SIZE"] = CFG["webapp"].get("results_cache_size", 100)

# --- Clave para firmar sesiones/cookies de Flask ---------------------------
# Viene de SESSION_SECRET (.env). En dev se genera una efímera para no romper,
# pero eso invalida las sesiones en cada reinicio: define SESSION_SECRET.
import os as _os
_session_secret = _os.environ.get("SESSION_SECRET")
if not _session_secret:
    import secrets as _secrets
    _session_secret = _secrets.token_hex(32)
    log.warning("SESSION_SECRET no definido: usando clave efímera (define "
                "SESSION_SECRET en .env para persistir sesiones).")
app.secret_key = _session_secret

DATA = Path(__file__).parent / "data"
RESULTS_CACHE: dict[str, dict] = {}

# --- Higiene del directorio de datos (deploy/producción) -------------------
# Crea las carpetas que las fuentes esperan y arranca la limpieza periódica de
# capturas/certificados antiguos para no llenar el disco. Ver maintenance.py.
from maintenance import ensure_data_dirs, start_retention_janitor
ensure_data_dirs(DATA)
_data_cfg = CFG.get("data", {})
start_retention_janitor(
    DATA,
    retention_hours=_data_cfg.get("retention_hours", 72),
    sweep_minutes=_data_cfg.get("retention_sweep_minutes", 60),
    # Retención de auditoría (A8): 0 = nunca purgar (default AML). SARLAFT suele
    # exigir años; se configura con VERIFYDATA_AUDIT_RETENTION_DAYS.
    audit_retention_days=CFG.get("logging", {}).get("audit_retention_days", 0))

# --- API REST pública (/api/v1) --------------------------------------------
# Se monta como Blueprint independiente; no altera la UI HTML de esta app.
# Documentación interactiva en /api/v1/docs. Ver api.py y API.md.
from api_routes import register_api
register_api(app, CFG, SOLVER, DATA)

# --- Autenticación (login OTP + Microsoft SSO + RBAC) -----------------------
# Ver auth.py. Regla: solo usuarios en la tabla `users` (activo=1) pueden
# entrar. La API REST (/api/v1) mantiene su propia auth por API-key.
from flask import g
import auth as _auth
app.register_blueprint(_auth.auth_bp)


@app.before_request
def _require_authentication():
    """Autenticación simple: user=naprolab, pass=naprolab por env vars.
    LOGIN_DISABLED=1 desactiva auth completamente (demo)."""
    # Si auth desactivada → usuario demo
    if not os.environ.get("LOGIN_DISABLED"):
        os.environ["LOGIN_DISABLED"] = "1"
    if os.environ.get("LOGIN_DISABLED", "").lower() in ("1", "true", "yes", "si"):
        from flask import g
        g.user = {"email": "demo@verifydata.local",
                  "rol": "admin", "nombre": "Demo"}
        return

    # Auth simple por sesiones
    from flask import session
    _auth_user = session.get("verifydata_user")
    if _auth_user:
        from flask import g
        g.user = _auth_user
        return

    # Rutas públicas (login, estáticos, API v1)
    if _auth.is_public_path(request.path):
        return

    # No autenticado → login
    if request.path.startswith("/api/"):
        from flask import jsonify
        return jsonify(ok=False, error="No autenticado"), 401
    from flask import redirect, url_for
    return redirect(url_for("auth.login", next=request.path))


@app.context_processor
def _inject_current_user():
    """Expone `current_user` a todas las plantillas (para header/menú)."""
    return {"current_user": g.get("user")}


def _audit_user() -> str:
    """Email del usuario en sesión (para auditoría), o 'anon' si no hay."""
    u = g.get("user")
    return (u or {}).get("email", "anon") if isinstance(u, dict) else "anon"


# ═══════════════════════════════════════════════════════════════════
#  LOGIN SIMPLE — user/pass por env vars
# ═══════════════════════════════════════════════════════════════════
LOGIN_TEMPLATE = ui_theme.head_open("VerifyData — Iniciar Sesión") + """
<div style="max-width:400px;margin:120px auto;padding:32px;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.08)">
  <div style="text-align:center;margin-bottom:24px">
    <div style="font-size:24px;font-weight:800;color:var(--violet)">Verify<span style="color:var(--blue)">Data</span></div>
    <p style="color:var(--text-faint);margin:8px 0 0;font-size:13px">Iniciar sesión para continuar</p>
  </div>
  <form method="POST" action="/login" style="display:flex;flex-direction:column;gap:12px">
    <div>
      <label style="font-size:12px;font-weight:600;margin-bottom:4px;display:block">Usuario</label>
      <input name="username" type="text" placeholder="Usuario" required style="width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;font-size:14px">
    </div>
    <div>
      <label style="font-size:12px;font-weight:600;margin-bottom:4px;display:block">Contraseña</label>
      <input name="password" type="password" placeholder="Contraseña" required style="width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;font-size:14px">
    </div>
    <button type="submit" class="btn btn-primary" style="width:100%;padding:12px;font-size:14px;margin-top:8px">Iniciar Sesión</button>
    {% if error %}<p style="color:#dc2626;font-size:13px;text-align:center;margin-top:4px">{{ error }}</p>{% endif %}
  </form>
</div>
""" + ui_theme.SHELL_CLOSE


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """Login simple: user=naprolab, pass=naprolab por env vars."""
    from flask import request, session, redirect, url_for, render_template_string

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        next_url = request.args.get("next", "/")

        expected_user = os.environ.get("VERIFYDATA_USER", "naprolab")
        expected_pass = os.environ.get("VERIFYDATA_PASS", "naprolab")

        if username == expected_user and password == expected_pass:
            from flask import g
            session["verifydata_user"] = {
                "email": f"{username}@verifydata.local",
                "rol": "admin",
                "nombre": username,
            }
            g.user = session["verifydata_user"]
            return redirect(next_url)
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Credenciales incorrectas")

    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route("/logout", methods=["GET", "POST"])
def logout_page():
    """Cierra sesión."""
    from flask import session, redirect, url_for
    session.clear()
    return redirect(url_for("login_page"))


# Content-Security-Policy. Nota: la UI y Swagger usan mucho CSS/JS inline
# (Bloque C: migrar a templates/ y endurecer), por eso se permite
# 'unsafe-inline' de momento. unpkg sirve el bundle de Swagger en /api/v1/docs.
_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "img-src 'self' data: https:",
    "style-src 'self' 'unsafe-inline' https://unpkg.com",
    "script-src 'self' 'unsafe-inline' https://unpkg.com",
    "font-src 'self' data:",
    "connect-src 'self'",
])


@app.after_request
def _security_headers(resp):
    """Cabeceras de seguridad HTTP en TODAS las respuestas (A4)."""
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # HSTS solo bajo HTTPS (detrás de un proxy TLS, vía X-Forwarded-Proto).
    proto = request.headers.get("X-Forwarded-Proto", "")
    if request.is_secure or proto.split(",")[0].strip() == "https":
        resp.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains")
    return resp


# ==============================================================
#  HTML Template (form)
# ==============================================================
TEMPLATE = ui_theme.head_open("VerifyData — Búsqueda automatizada") + \
    ui_theme.shell_open("busqueda", "Búsqueda automatizada",
                        "Elige una ruta de verificación") + """
  <div class="hero-row">
    <div class="menu-hero">
      <p class="eyebrow">Búsqueda automatizada</p>
      <h2>Verifica personas y empresas en segundos</h2>
      <p>Consulta <b style="color:var(--text)">{{ total_sources }} fuentes públicas</b>
         en paralelo — identidad, listas restrictivas, antecedentes, contratación
         y noticias. Elige buscar por <b style="color:var(--text)">persona</b>
         (cédula/nombre) o por <b style="color:var(--text)">empresa</b> (NIT).</p>
    </div>
    <div class="hero-logo-big">
      <div class="wordmark wm-light wm-hero">Verify<span>Data</span></div>
    </div>
  </div>

  <div class="seg" style="margin-bottom:20px">
    <button type="button" id="tab-persona" class="active">Persona</button>
    <button type="button" id="tab-nit">Empresa (NIT)</button>
  </div>

  <div class="form-shell">
    <form class="card pad" method="post" action="/" id="search-form">
      <input type="hidden" name="mode" id="f-mode" value="persona">

      <div id="grid-persona">
        <div class="form-section">
          <h4>Datos de la persona</h4>
          <div class="field-row">
            <div class="field full">
              <label>Nombre completo</label>
              <input name="nombre" id="f-nombre" placeholder="Ej: Juan Camilo Pérez Gómez"
                     value="" autofocus autocomplete="off">
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label>Cédula / Documento</label>
              <input name="cedula" id="f-cedula" placeholder="Ej: 1234567890"
                     value="" inputmode="numeric" autocomplete="off">
            </div>
            <div class="field">
              <label>Fecha de expedición</label>
              <input name="fecha_exp" id="f-fecha" placeholder="dd/mm/aaaa"
                     value="" autocomplete="off">
            </div>
          </div>
        </div>
        <div class="form-footer">
          <a class="btn btn-ghost" href="/nueva" id="clear-btn">Limpiar</a>
          <button type="submit" class="btn btn-primary">Iniciar verificación</button>
        </div>
      </div>

      <div id="grid-nit" style="display:none">
        <div class="form-section">
          <h4>Datos de la empresa</h4>
          <div class="field-row">
            <div class="field full">
              <label>NIT de la empresa</label>
              <input name="nit" id="f-nit"
                     placeholder="Ej: 900123456 (sin dígito de verificación)"
                     value="" inputmode="numeric" autocomplete="off">
            </div>
          </div>
        </div>
        <div class="form-section">
          <h4>Tipo de reporte</h4>
          <div id="nit-mode-opts">
            <label class="radio-opt sel">
              <input type="radio" name="nit_mode" value="empresa" checked>
              <span><span class="t">Información de la empresa</span>
                <span class="d">Consulta a la compañía (razón social + NIT) en
                  sanciones, listas, contratación, judicial, boletines, PEP y
                  noticias.</span></span>
            </label>
            <label class="radio-opt">
              <input type="radio" name="nit_mode" value="reps">
              <span><span class="t">Representantes legales</span>
                <span class="d">Extrae los representantes legales de RUES y corre la
                  búsqueda por persona a cada uno.</span></span>
            </label>
            <label class="radio-opt">
              <input type="radio" name="nit_mode" value="ambas">
              <span><span class="t">Ambas</span>
                <span class="d">Reporte de la empresa + búsqueda de cada representante
                  legal.</span></span>
            </label>
          </div>
        </div>
        <div class="form-footer">
          <a class="btn btn-ghost" href="/nueva">Limpiar</a>
          <button type="submit" class="btn btn-primary">Iniciar verificación</button>
        </div>
      </div>
    </form>

    <aside class="card pad side-help">
      <h4>Cómo funciona</h4>
      <p>Cada verificación consulta las fuentes en paralelo y documenta la evidencia
         de cada resultado.</p>
      <div class="step-mini"><span class="n on">1</span><span class="tx">Ingresa los
        datos del sujeto o el NIT de la empresa.</span></div>
      <div class="step-mini"><span class="n on">2</span><span class="tx">El sistema
        consulta {{ total_sources }} fuentes y resuelve captchas automáticamente.</span></div>
      <div class="step-mini"><span class="n on">3</span><span class="tx">Revisa el
        reporte en pantalla y descarga el PDF con evidencia.</span></div>
      <p style="margin-top:16px"><span class="badge b-violeta"><span class="badge-dot"></span>
        Solver: {{ solver_name }}</span></p>
    </aside>
  </div>

  {{ body | safe }}

<script>
(function(){
  var tp=document.getElementById('tab-persona'), tn=document.getElementById('tab-nit');
  var gp=document.getElementById('grid-persona'), gn=document.getElementById('grid-nit');
  var mode=document.getElementById('f-mode');
  function toPersona(){mode.value='persona';gp.style.display='';gn.style.display='none';
    tp.classList.add('active');tn.classList.remove('active');
    var f=document.getElementById('f-nombre');if(f)f.focus();}
  function toNit(){mode.value='nit';gp.style.display='none';gn.style.display='';
    tn.classList.add('active');tp.classList.remove('active');
    var f=document.getElementById('f-nit');if(f)f.focus();}
  tp.addEventListener('click',toPersona);
  tn.addEventListener('click',toNit);
  // Preselección por ?tab= (nav del sidebar)
  try{var t=new URLSearchParams(location.search).get('tab');
      if(t==='empresa'||t==='nit')toNit();}catch(e){}
})();
(function(){
  var opts=document.querySelectorAll('.nit-mode-opt, #nit-mode-opts .radio-opt');
  function sync(){opts.forEach(function(o){var r=o.querySelector('input[type=radio]');
    o.classList.toggle('sel',!!(r&&r.checked));});}
  opts.forEach(function(o){var r=o.querySelector('input[type=radio]');
    if(r)r.addEventListener('change',sync);});
  sync();
})();
document.getElementById('search-form').addEventListener('submit',function(){
  setTimeout(function(){
    ['f-nombre','f-cedula','f-fecha','f-nit'].forEach(function(id){
      var el=document.getElementById(id); if(el) el.value='';});
  },0);
});
var _cb=document.getElementById('clear-btn');
if(_cb) _cb.addEventListener('click',function(e){
  e.preventDefault();
  ['f-nombre','f-cedula','f-fecha'].forEach(function(id){
    var el=document.getElementById(id); if(el) el.value='';});
  document.getElementById('f-nombre').focus();
});
</script>
""" + ui_theme.SHELL_CLOSE


# ==============================================================
#  HTML Template (results page — magazine-style matching the PDF)
# ==============================================================
TEMPLATE_RESULTS = ui_theme.head_open("VerifyData — Resultado de verificación") + \
    ui_theme.shell_open("persona", "Resultado de verificación",
                        "Verificación · Reporte generado") + """
  {% if query['__nit_token'] %}
  <a class="btn btn-secondary btn-sm" href="/nit/{{ query['__nit_token'] }}"
     style="margin-bottom:16px">&larr; Volver a la empresa{% if query['__empresa'] %} · {{ query['__empresa'] }}{% endif %}</a>
  {% endif %}

  <div class="card res-header">
    <div class="who">
      <div class="wordmark wm-light wm-sm">Verify<span>Data</span></div>
      {% if query['__empresa_mode'] %}
      <p class="kicker" style="margin-bottom:4px">Reporte de verificación · Empresa</p>
      <h3>{{ query.razon_social or query.nombre or '—' }}</h3>
      <div class="meta">
        {% if query.cedula %}<span>NIT <b style="color:var(--text)">{{ query.cedula }}</b></span>{% endif %}
        <span id="status-text">Iniciando búsqueda…</span>
      </div>
      {% else %}
      <p class="kicker" style="margin-bottom:4px">Reporte de verificación · Persona natural</p>
      <h3>{{ query.nombre or query.cedula or 'Consulta' }}</h3>
      <div class="meta">
        {% if query.cedula %}<span>CC <b style="color:var(--text)">{{ query.cedula }}</b></span>{% endif %}
        {% if query.fecha_exp %}<span>Exp. {{ query.fecha_exp }}</span>{% endif %}
        <span id="status-text">Iniciando búsqueda…</span>
      </div>
      {% endif %}
    </div>
    <div class="acts">
      <a class="btn btn-secondary" href="/nueva">Nueva búsqueda</a>
      <a class="btn btn-primary" id="pdf-btn" href="/download/pdf/{{ token }}" target="_blank">Descargar PDF</a>
    </div>
  </div>

  <!-- ======== SECCIÓN RIESGO CREDITICIO (NUEVO) ======== -->
  <div class="card pad" id="credit-risk-sec" style="margin-bottom:20px;display:none">
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;justify-content:space-between">
      <div>
        <div class="section-title" style="margin:0">Perfil crediticio · VerifyData Risk</div>
        <p class="section-sub" style="margin:4px 0 0">Análisis combinado: RSales API + Excel BITACORA + Datacrédito</p>
      </div>
      <div style="display:flex;gap:10px;align-items:center">
        <span id="cr-rsales-badge" style="display:none;font-size:11px;font-weight:700;padding:4px 10px;border-radius:20px;background:rgba(62,122,249,.12);color:#1d4ed8">RSales</span>
        <span id="cr-excel-badge" style="font-size:11px;font-weight:700;padding:4px 10px;border-radius:20px;background:rgba(105,65,244,.12);color:#5b21b6">Excel</span>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:16px">
      <div class="card" style="text-align:center;padding:18px 14px">
        <div id="cr-score" style="font-size:42px;font-weight:800;color:var(--text)">—</div>
        <div style="font-size:12px;color:var(--text-faint);font-weight:600">SCORE / 1000</div>
        <div id="cr-score-bar" style="height:6px;background:#f1f0f6;border-radius:3px;margin-top:10px;overflow:hidden"><i style="display:block;height:100%;background:var(--grad-btn);width:0%;border-radius:3px;transition:width .5s"></i></div>
      </div>
      <div class="card" style="text-align:center;padding:18px 14px">
        <div id="cr-level" style="font-size:20px;font-weight:800;color:var(--text)">—</div>
        <div style="font-size:12px;color:var(--text-faint);font-weight:600">NIVEL DE RIESGO</div>
        <div id="cr-recommendation" style="font-size:13px;font-weight:600;margin-top:8px;color:var(--text-dim)">Consultando…</div>
      </div>
      <div class="card" style="text-align:center;padding:18px 14px">
        <div id="cr-monto" style="font-size:20px;font-weight:800;color:var(--text)">—</div>
        <div style="font-size:12px;color:var(--text-faint);font-weight:600">MONTO MÁX. RECOMENDADO</div>
        <div id="cr-alertas" style="margin-top:8px"></div>
      </div>
    </div>
    <div id="cr-detail" style="margin-top:12px;display:none">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <div style="font-size:11px;font-weight:700;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Factores positivos</div>
          <div id="cr-positivos" style="font-size:12px;color:#15803d"></div>
        </div>
        <div>
          <div style="font-size:11px;font-weight:700;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Factores negativos / Alertas</div>
          <div id="cr-negativos" style="font-size:12px;color:#b91c1c"></div>
        </div>
      </div>
      <div id="cr-rsales-detail" style="display:none;margin-top:12px">
        <div style="font-size:11px;font-weight:700;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Datos RSales</div>
        <div id="cr-rsales-data" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;font-size:12px;color:var(--text)"></div>
      </div>
    </div>
  </div>
  <!-- ======== FIN SECCIÓN CREDITICIA ======== -->

  <div class="res-kpis" style="grid-template-columns:repeat(6,1fr)">
    <div class="card res-kpi"><div class="v" id="st-total">{{ total_sources }}</div><div class="l">Fuentes</div></div>
    <div class="card res-kpi"><div class="v" id="st-match">0</div><div class="l">Coincidencias</div></div>
    <div class="card res-kpi"><div class="v" id="st-notice">0</div><div class="l">Avisos</div></div>
    <div class="card res-kpi"><div class="v" id="st-captcha">0</div><div class="l">Verificar</div></div>
    <div class="card res-kpi"><div class="v" id="st-error">0</div><div class="l">Errores</div></div>
    <div class="card res-kpi"><div class="v" id="st-records">0</div><div class="l">Registros</div></div>
  </div>

  <div class="card pad" id="featured-sec" style="display:none;margin-bottom:20px">
    <div class="section-title" id="featured-h">Fuentes principales</div>
    <p class="section-sub">Fuentes de mayor peso para la decisión de cumplimiento, con su resultado y evidencia verificable.</p>
    <div id="featured"></div>
  </div>

  <div class="card pad" id="others-sec" style="display:none;margin-bottom:20px">
    <div class="section-title" id="others-h">Información general</div>
    <p class="section-sub">Cobertura complementaria — sanciones, contratación, PEP, reputación y fugitivos.</p>
    <div class="filter-row" style="margin-bottom:16px">
      <span class="badge b-azul"><span class="badge-dot"></span>Con dato</span>
      <span class="badge b-verde"><span class="badge-dot"></span>Sin coincidencia</span>
      <span class="badge b-amber"><span class="badge-dot"></span>Consulta manual / verificar</span>
      <span class="badge b-rojo"><span class="badge-dot"></span>Error</span>
    </div>
    <div id="others"></div>
  </div>

  <div class="card pad" id="progress" style="display:flex;align-items:center;gap:12px;color:var(--text-dim);font-size:13px">
    <span class="spin"></span> Búsqueda en progreso. Las fuentes se mostrarán conforme terminen.
  </div>

<script>
(function() {
  var token = {{ token|tojson }};
  var catMap = {{ cat_map|safe }};
  var rendered = {};
  var cedula = {{ query.get('cedula','')|tojson }};  // para credit check

  // ── Credit Risk ────────────────────────────────────────
  var CR_COLORS = {'BAJO':'#15803d','MEDIO':'#d97706','ALTO':'#dc2626','CRITICO':'#991b1b'};
  function renderCreditRisk(d) {
    if (!d.ok || !d.profile) return;
    var p = d.profile;
    var scEl = document.getElementById('cr-score');
    var bar = document.getElementById('cr-score-bar').querySelector('i');
    var levelEl = document.getElementById('cr-level');
    var recEl = document.getElementById('cr-recommendation');
    var montoEl = document.getElementById('cr-monto');
    var alertasEl = document.getElementById('cr-alertas');
    var posEl = document.getElementById('cr-positivos');
    var negEl = document.getElementById('cr-negativos');
    var detEl = document.getElementById('cr-detail');

    scEl.textContent = p.score;
    scEl.style.color = CR_COLORS[p.nivel_riesgo] || '#333';
    bar.style.width = (p.score / 10) + '%';
    bar.style.background = CR_COLORS[p.nivel_riesgo] || 'var(--grad-btn)';
    levelEl.textContent = p.nivel_riesgo;
    levelEl.style.color = CR_COLORS[p.nivel_riesgo] || '#333';
    recEl.textContent = p.recomendacion;
    montoEl.textContent = p.monto_maximo_recomendado > 0 ? ('\$' + p.monto_maximo_recomendado.toLocaleString('es-CO')) : '—';

    if (p.alertas && p.alertas.length) {
      alertasEl.innerHTML = p.alertas.slice(0,3).map(function(a){return '<div style=\"font-size:11px;color:#b91c1c;font-weight:600;margin-top:2px\">⚠ '+esc(a)+'</div>';}).join('');
    }

    if (p.fuentes && p.fuentes.rsales) document.getElementById('cr-rsales-badge').style.display = 'inline-block';
    if (p.fuentes && p.fuentes.excel) document.getElementById('cr-excel-badge').style.display = 'inline-block';

    detEl.style.display = '';
    if (p.factores_positivos && p.factores_positivos.length) {
      posEl.innerHTML = p.factores_positivos.map(function(f){return '<div style=\"margin:3px 0\">✓ '+esc(f)+'</div>';}).join('');
    } else { posEl.innerHTML = '<span style=\"color:var(--text-faint)\">Ninguno detectado</span>'; }
    if (p.factores_negativos && p.factores_negativos.length) {
      negEl.innerHTML = p.factores_negativos.map(function(f){return '<div style=\"margin:3px 0\">✗ '+esc(f)+'</div>';}).join('');
    } else { negEl.innerHTML = '<span style=\"color:var(--text-faint)\">Ninguno detectado</span>'; }

    // RSales detail
    var rd = p.detalle && p.detalle.rsales;
    if (rd) {
      document.getElementById('cr-rsales-detail').style.display = '';
      document.getElementById('cr-rsales-data').innerHTML =
        '<div><b>Cartera total</b><br>\$'+(rd.cartera_total||0).toLocaleString('es-CO')+'</div>' +
        '<div><b>Cartera vencida</b><br>\$'+(rd.cartera_vencida||0).toLocaleString('es-CO')+' ('+(rd.pct_vencida||0).toFixed(0)+'%)</div>' +
        '<div><b>Compras RSales</b><br>\$'+(rd.compras_total||0).toLocaleString('es-CO')+'</div>' +
        '<div><b>Pedidos</b><br>'+(rd.num_pedidos||0)+' · Últ: '+(rd.ultima_compra||'N/A').slice(0,10)+'</div>';
    }

    // Cotejo
    if (p.cotejo && p.cotejo.nota) {
      var cd = document.createElement('div');
      cd.style.cssText = 'margin-top:10px;font-size:12px;padding:8px 12px;border-radius:8px;';
      cd.style.background = p.cotejo.compras_ok ? 'rgba(21,128,61,.08)' : 'rgba(220,38,38,.08)';
      cd.style.color = p.cotejo.compras_ok ? '#15803d' : '#b91c1c';
      cd.style.fontWeight = '600';
      cd.textContent = '📊 Cotejo: ' + p.cotejo.nota;
      document.getElementById('cr-rsales-detail').appendChild(cd);
    }

    // Botón RSales si no están cargados aún
    if (!p.fuentes.rsales) {
      var btnRow = document.createElement('div');
      btnRow.style.cssText = 'margin-top:10px;text-align:center';
      btnRow.innerHTML = '<a class=\"btn btn-secondary btn-sm\" href=\"#\" id=\"cr-load-rsales\" onclick=\"return!1\">🔍 Cargar datos RSales (cartera, compras, cotejo)</a>';
      document.getElementById('cr-rsales-detail').style.display = '';
      document.getElementById('cr-rsales-detail').appendChild(btnRow);
      document.getElementById('cr-load-rsales').addEventListener('click', function(e){
        e.preventDefault();
        this.textContent = 'Cargando RSales…';
        this.style.pointerEvents = 'none';
        fetch('/api/credit/profile/' + encodeURIComponent(cedula) + '?rsales=1')
          .then(function(r){return r.json();})
          .then(function(d2){ renderCreditRisk(d2); })
          .catch(function(){}); 
      });
    }
  }

  function fetchCreditRisk() {
    if (!cedula) return;
    document.getElementById('credit-risk-sec').style.display = '';
    // Primero cargar rápido (solo Excel), luego opción de RSales
    fetch('/api/credit/profile/' + encodeURIComponent(cedula))
      .then(function(r){return r.json();})
      .then(function(d){ renderCreditRisk(d); })
      .catch(function(e){ console.error('Credit risk fetch:', e); });
  }
  fetchCreditRisk();
  // ── End Credit Risk ────────────────────────────────────

  var CAT_ORDER = [
    'Sanciones internacionales','Contratación pública','Empresas y sociedades',
    'Antecedentes judiciales','Crimen y fugitivos','Corrupción internacional',
    'PEP (Personas Expuestas Políticamente)','Reputacional y noticias',
    'Otros registros especializados',
    'Identidad y registros básicos','Antecedentes disciplinarios'
  ];

  function statusKey(h) {
    if (h.error) return 'error';
    if (h.matched) return 'match';
    if (h.captcha_required) return 'captcha';
    if (h.notice) return 'notice';
    return 'nomatch';
  }
  var PILL = {match:'Con dato', nomatch:'Sin coincidencia', notice:'Consulta manual',
              captcha:'Verificar', error:'Error'};
  var BADGE = {match:'b-azul', nomatch:'b-verde', notice:'b-amber',
               captcha:'b-amber', error:'b-rojo'};

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function badge(k) {
    return '<span class="badge ' + BADGE[k] + '"><span class="badge-dot"></span>' + PILL[k] + '</span>';
  }
  function host(url){ try { return url.split('/').slice(0,3).join('/'); } catch(e){ return url; } }
  function isFeatured(name, h) { return !!(h && h.is_principal); }

  function renderFeatured(name, h) {
    var k = statusKey(h);
    var url = h.source_url || (h.evidence_urls && h.evidence_urls[0]) || '#';
    var summary = h.summary || '';

    var img = '';
    var evImg = h.evidence_img;
    if (evImg) {
      var dlHref = h.download_url ? h.download_url : evImg;
      img = '<div class="fimg"><a href="/download/' + esc(dlHref) + '" target="_blank" rel="noopener">'
          + '<img src="/download/' + esc(evImg) + '" alt="evidencia" loading="lazy"></a></div>';
    } else if (h.download_url) {
      var isPdf = h.download_url.toLowerCase().endsWith('.pdf');
      img = '<p style="margin:6px 0 0"><a class="btn btn-secondary btn-sm" href="/download/'
          + esc(h.download_url) + '" target="_blank">' + (isPdf ? 'Ver / Descargar PDF' : 'Descargar evidencia') + '</a></p>';
    }

    var body = '';
    if (h.clean_rows && h.clean_rows.length) {
      var kv = h.clean_rows.map(function(r) {
        return '<tr><td class="name" style="width:38%">' + esc(r[0]) + '</td><td>'
             + esc(String(r[1]).slice(0,240)) + '</td></tr>';
      }).join('');
      body = '<table class="dtable" style="margin-top:6px"><tbody>' + kv + '</tbody></table>';
    } else if (h.details && h.details.length) {
      body = h.details.slice(0, 5).map(function(d) {
        var kv2 = Object.keys(d).map(function(key) {
          return '<tr><td class="name" style="width:38%">' + esc(key) + '</td><td>'
               + esc(String(d[key]).slice(0,240)) + '</td></tr>';
        }).join('');
        return '<table class="dtable" style="margin-top:6px"><tbody>' + kv2 + '</tbody></table>';
      }).join('');
    } else if (h.notice) {
      body = '<p class="fsum">' + esc(h.notice) + '</p>';
    } else if (h.error) {
      body = '<p class="fsum" style="color:#b91c1c">' + esc(h.error) + '</p>';
    } else if (summary) {
      body = '<p class="fsum">' + esc(summary) + '</p>';
    }

    return '<div class="featured-card" data-source="' + esc(name) + '">' +
      '<div class="fhead"><h3>' + esc(name) + '</h3>' + badge(k) + '</div>' +
      '<div class="fmeta"><a href="' + esc(url) + '" target="_blank" rel="noopener" style="color:var(--violet);text-decoration:none">'
        + esc(host(url)) + '</a> &middot; ' + (h.elapsed_s || 0).toFixed(1) + 's</div>' +
      body + img +
    '</div>';
  }

  function renderOther(name, h) {
    var k = statusKey(h);
    var url = h.source_url || (h.evidence_urls && h.evidence_urls[0]) || '#';
    var note = '';
    if (h.matched && h.summary) note = h.summary;
    else if (h.error) note = h.error;
    else if (h.notice) note = h.notice;
    else if (h.summary) note = h.summary;
    else note = 'Sin coincidencias.';
    return '<div class="ochip">' +
      '<div style="min-width:0">' +
        '<div class="on" title="' + esc(name) + '">' + esc(name) + '</div>' +
        '<div class="os" title="' + esc(note) + '" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px">' + esc(note) + '</div>' +
      '</div>' + badge(k) +
    '</div>';
  }

  function renderOthersGrouped(sources) {
    var groups = {};
    Object.keys(sources).forEach(function(n) {
      if (isFeatured(n, sources[n])) return;
      var cat = catMap[n] || 'Otras';
      (groups[cat] = groups[cat] || []).push(n);
    });
    var cats = Object.keys(groups).sort(function(a,b){
      var ia = CAT_ORDER.indexOf(a), ib = CAT_ORDER.indexOf(b);
      if (ia < 0) ia = 99; if (ib < 0) ib = 99;
      return ia - ib || a.localeCompare(b);
    });
    var html = '';
    cats.forEach(function(cat) {
      var names = groups[cat].sort();
      var chips = names.map(function(n){ return renderOther(n, sources[n]); }).join('');
      html += '<div class="cat-group">' +
        '<div class="cat-h"><span class="dot"></span>' + esc(cat) +
          ' <span style="color:var(--text-faint);font-weight:600">· ' + names.length + '</span></div>' +
        '<div class="src-grid">' + chips + '</div>' +
      '</div>';
    });
    return html;
  }

  function poll() {
    fetch('/api/run/' + token).then(function(r) { return r.json(); })
    .then(function(s) {
      if (s.error) {
        document.getElementById('status-text').textContent = 'Run no encontrado.';
        return;
      }
      var done = Object.keys(s.sources || {}).length;
      var m = s.matched||0, c = s.captcha||0, e = s.error||0;
      var nt = 0;
      for (var n in s.sources) {
        var h = s.sources[n];
        if (h.notice && !h.error && !h.matched && !h.captcha_required) nt++;
      }
      var recs = 0;
      for (var n2 in s.sources) recs += (s.sources[n2].details || []).length;
      document.getElementById('st-match').textContent = m;
      document.getElementById('st-captcha').textContent = c;
      document.getElementById('st-notice').textContent = nt;
      document.getElementById('st-error').textContent = e;
      document.getElementById('st-records').textContent = recs;
      document.getElementById('status-text').textContent =
        s.completed ? '✓ Completado (' + done + '/' + s.total + ')' :
                      '⏳ Buscando… ' + done + '/' + s.total;

      var names = Object.keys(s.sources);
      var newOnes = names.filter(function(n) { return !rendered[n]; });
      var anyFeatured = false, anyOther = false;
      for (var n3 in s.sources) {
        if (isFeatured(n3, s.sources[n3])) anyFeatured = true; else anyOther = true;
      }
      if (anyFeatured) document.getElementById('featured-sec').style.display = '';
      if (anyOther) document.getElementById('others-sec').style.display = '';

      if (s.completed && !rendered.__finalized) {
        rendered.__finalized = true;
        rendered = {};
        document.getElementById('featured').innerHTML = '';
        var featNames = Object.keys(s.sources).filter(function(n) {
          return isFeatured(n, s.sources[n]);
        }).sort(function(a, b) {
          var oa = s.sources[a].principal_order; if (oa == null) oa = 999;
          var ob = s.sources[b].principal_order; if (ob == null) ob = 999;
          return oa - ob;
        });
        Object.keys(s.sources).forEach(function(n) { rendered[n] = s.sources[n]; });
        featNames.forEach(function(n) {
          document.getElementById('featured')
            .insertAdjacentHTML('beforeend', renderFeatured(n, s.sources[n]));
        });
        document.getElementById('others').innerHTML = renderOthersGrouped(s.sources);
        var prog = document.getElementById('progress'); if (prog) prog.style.display = 'none';
        var pdf = document.getElementById('pdf-btn');
        if (pdf) pdf.textContent = 'Descargar PDF (listo)';
        return;
      }

      var othersChanged = false;
      newOnes.forEach(function(n) {
        rendered[n] = s.sources[n];
        var h = s.sources[n];
        if (isFeatured(n, h)) {
          document.getElementById('featured')
            .insertAdjacentHTML('beforeend', renderFeatured(n, h));
        } else { othersChanged = true; }
      });
      if (othersChanged) {
        document.getElementById('others').innerHTML = renderOthersGrouped(s.sources);
      }
      if (s.completed) {
        var prog2 = document.getElementById('progress'); if (prog2) prog2.style.display = 'none';
        var pdf2 = document.getElementById('pdf-btn');
        if (pdf2) pdf2.textContent = 'Descargar PDF (listo)';
        return;
      }
      setTimeout(poll, 700);
    }).catch(function(e) {
      console.error('Poll:', e);
      setTimeout(poll, 2000);
    });
  }
  poll();
})();
</script>
""" + ui_theme.SHELL_CLOSE


# ==============================================================
#  Rendering helpers
# ==============================================================

def esc(s: Any) -> str:
    return (str(s).replace("&","&amp;").replace("<","&lt;")
            .replace(">","&gt;").replace('"',"&quot;"))


def render_hit(h: Hit) -> str:
    if h.error:
        cls, badge, bcls = "error", "ERROR", "b-error"
    elif h.matched:
        cls, badge, bcls = "match", "MATCH", "b-match"
    elif h.captcha_required:
        cls, badge, bcls = "captcha", "CAPTCHA", "b-captcha"
    elif h.notice:
        cls, badge, bcls = "notice", "AVISO", "b-notice"
    else:
        cls, badge, bcls = "nomatch", "SIN COINCIDENCIA", "b-nomatch"

    # Cuerpo: detalles (mismas filas limpias/prettificadas que el reporte PDF)
    body = ""
    if h.details:
        try:
            from report import _detail_rows_for
            rows = _detail_rows_for(h, limit=16)
        except Exception:
            rows = [(k, str(v)) for d in h.details if isinstance(d, dict)
                    for k, v in d.items() if v not in (None, "", "N/A")][:16]
        if rows:
            kv = "".join(f'<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>'
                         for k, v in rows)
            body = f"<table>{kv}</table>"
        else:
            body = '<p class="muted">Sin resultados.</p>'
    elif h.error:
        body = f'<p class="err">{esc(h.error)}</p>'
    elif h.captcha_required:
        body = f'<div class="captcha-msg">{esc(h.notice or "Esta fuente requiere captcha.")}</div>'
    elif h.notice:
        body = f'<div class="notice">{esc(h.notice)}</div>'
    else:
        body = '<p class="muted">Sin coincidencias.</p>'

    # Evidencia visual: captura del certificado PDF rasterizado (preferida)
    # o screenshot. Misma lógica que el reporte PDF (report._evidence_for).
    evi_html = ""
    try:
        from report import _evidence_for
        img_path, ev_kind, pdf_rel = _evidence_for(h)
        if img_path:
            rel = img_path.relative_to(DATA).as_posix()
            cap = ("Certificado PDF capturado" if ev_kind == "pdf"
                   else "Captura de pantalla")
            evi_html = (
                f'<div class="evidence" style="margin-top:12px">'
                f'<div style="font-size:12px;color:#64748b;font-weight:600;'
                f'margin-bottom:6px">📎 Evidencia — {cap}</div>'
                f'<a href="/download/{esc(rel)}" target="_blank" rel="noopener">'
                f'<img src="/download/{esc(rel)}" alt="evidencia" loading="lazy" '
                f'style="max-width:100%;border:1px solid #e2e8f0;border-radius:8px;'
                f'box-shadow:0 1px 4px rgba(0,0,0,.06)"></a></div>')
    except Exception:
        evi_html = ""

    # Botón descarga si hay cert
    dl_btn = ""
    if h.download_url:
        label = "⬇ Certificado PDF" if str(h.download_url).lower().endswith(".pdf") else "⬇ Evidencia"
        dl_btn = f'<a class="dl" href="/download/{esc(h.download_url)}" target="_blank">{label}</a>'

    # Categoría
    cat = ""
    for src in registry.all_sources():
        if src.name == h.source:
            cat = f'<span class="cat">{esc(src.category)}</span>'
            url = src.source_url
            break
    else:
        url = h.evidence_urls[0] if h.evidence_urls else "#"

    return f"""
    <div class="card {cls}">
      <header>
        <span class="badge {bcls}">{badge}</span>
        <h2>{esc(h.source)}</h2>
        {cat}
        <a class="url" href="{esc(url)}" target="_blank" rel="noopener">↗ abrir fuente</a>
        <span class="elapsed">{h.elapsed_s:.2f}s</span>
        {dl_btn}
      </header>
      <p class="summary">{esc(h.summary)}</p>
      {body}
      {evi_html}
    </div>"""


def render_results(query: dict, hits: list[Hit], total_s: float,
                   token: str = "") -> str:
    query = dict(query)
    query["__token"] = token  # usado para link de descarga PDF
    total = sum(len(h.details) for h in hits)
    n_match = sum(1 for h in hits if h.matched)
    n_captcha = sum(1 for h in hits if h.captcha_required)
    n_notice  = sum(1 for h in hits if h.notice and not h.error and not h.matched and not h.captcha_required)
    n_err     = sum(1 for h in hits if h.error)
    n_no      = len(hits) - n_match - n_captcha - n_notice - n_err

    qtxt = []
    if query.get("nombre"): qtxt.append(f"nombre=<b>{esc(query['nombre'])}</b>")
    if query.get("cedula"): qtxt.append(f"CC=<b>{esc(query['cedula'])}</b>")
    if query.get("fecha_exp"): qtxt.append(f"fecha_exp=<b>{esc(query['fecha_exp'])}</b>")
    last_q = ""
    if qtxt:
        last_q = f'<div class="last-query"><b>Última búsqueda:</b> {" · ".join(qtxt)}</div>'

    stats = f"""
    <div class="results-toolbar">
      <div class="stats">
        <div class="stat"><b>{len(hits)}</b><small>fuentes</small></div>
        <div class="stat"><b>{n_match}</b><small>matches</small></div>
        <div class="stat"><b>{n_captcha}</b><small>captcha</small></div>
        <div class="stat"><b>{n_notice}</b><small>avisos</small></div>
        <div class="stat"><b>{n_err}</b><small>errores</small></div>
        <div class="stat"><b>{total}</b><small>registros</small></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="dl-pdf" href="/download/pdf/{query.get('__token','')}">⬇ PDF</a>
        <a class="new-search" href="/nueva">↻ Nueva búsqueda</a>
      </div>
    </div>"""
    # Orden: fuentes PRINCIPALES / excluyentes primero (orden canónico),
    # luego el resto. Mismo criterio que el reporte PDF.
    try:
        from report import _is_principal, _principal_order
        principals = sorted([h for h in hits if _is_principal(h)],
                            key=_principal_order)
        others = [h for h in hits if not _is_principal(h)]
    except Exception:
        principals, others = [], list(hits)

    def _divider(txt):
        return (f'<h2 style="font-size:18px;color:#1A2B4A;margin:28px 0 14px;'
                f'padding-bottom:8px;border-bottom:2px solid #00D4D4">{esc(txt)}</h2>')

    body_cards = ""
    if principals:
        body_cards += _divider("Fuentes principales — Excluyentes y prioritarias")
        body_cards += "".join(render_hit(h) for h in principals)
        body_cards += _divider("Información general")
        body_cards += "".join(render_hit(h) for h in others)
    else:
        body_cards = "".join(render_hit(h) for h in others)
    return last_q + stats + body_cards


def empty_state(total_sources: int) -> str:
    if total_sources == 0:
        return ('<div class="card pad empty-state" style="margin-top:20px">'
                '<div class="ic">!</div><h4>No hay fuentes registradas</h4>'
                '<p>Aún no se han implementado fuentes. Ver sources/_existing.py</p></div>')
    return ""


def _alert(msg: str, kind: str = "err") -> str:
    """Aviso enmarcado con el estilo del tema (para errores de validación)."""
    return (f'<div class="card pad" style="margin-top:20px">'
            f'<div class="auth-msg {kind}" style="margin:0">{esc(msg)}</div></div>')


# ==============================================================
#  Routes
# ==============================================================

@app.route("/", methods=["GET", "POST"])
def index():
    total = len(registry.all_sources())
    solver_name = SOLVER.name
    body = empty_state(total)

    if request.method == "POST":
        mode = (request.form.get("mode") or "persona").strip().lower()
        # --- Búsqueda por NIT (empresa → representantes → personas) ---
        if mode == "nit" or (request.form.get("nit") or "").strip():
            import re as _re
            nit = _re.sub(r"\D", "", request.form.get("nit") or "")
            if not nit:
                body = _alert('Ingresa un NIT válido (solo dígitos).')
                return render_template_string(
                    TEMPLATE, body=body, total_sources=total,
                    solver_name=solver_name,
                    ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            nit_mode = (request.form.get("nit_mode") or "empresa").strip().lower()
            if nit_mode not in ("reps", "empresa", "ambas"):
                nit_mode = "empresa"
            from runs import run_nit_search
            nit_token = run_nit_search(nit, SOLVER, mode=nit_mode)
            audit("search_nit", user=_audit_user(), nit=nit, mode=nit_mode,
                  token=nit_token)
            return redirect(url_for("nit_results", token=nit_token), code=303)

        nombre = (request.form.get("nombre") or "").strip()
        cedula = (request.form.get("cedula") or "").strip()
        fecha_exp = (request.form.get("fecha_exp") or "").strip()
        if not nombre and not cedula:
            body = _alert('Ingresa al menos nombre o cédula.')
        else:
            # Streaming: lanza búsqueda en background
            # Detectar Vercel serverless: usar ejecución inline (sin subprocess)
            _is_vercel = os.environ.get("VERIFYDATA_ENV") == "production"
            if _is_vercel:
                from runs import run_search_progressive_inline
                run_fn = run_search_progressive_inline
            else:
                from runs import run_search_progressive
                run_fn = run_search_progressive
            # Default: TODAS las fuentes (64). Para un run rápido usar ?sources=featured.
            sources_mode = request.args.get("sources", "all")
            query = {"nombre": nombre, "cedula": cedula,
                     "fecha_exp": fecha_exp,
                     "__sources": sources_mode}
            token = run_fn(
                query, cedula, fecha_exp, SOLVER, skip_browser=False)
            # Auditoría AML: quién consultó qué cédula/nombre y cuándo.
            audit("search", user=_audit_user(), cedula=cedula or None,
                  nombre=nombre or None, sources=sources_mode, token=token)
            return redirect(url_for("results", token=token), code=303)

    return render_template_string(
        TEMPLATE, body=body, total_sources=total, solver_name=solver_name,
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@app.route("/results/<token>")
def results(token: str):
    """Página de resultados magazine-style (matching the PDF)."""
    from runs import get_run
    state = get_run(token)
    if not state:
        return redirect(url_for("index"), code=303)
    total = len(registry.all_sources())
    query = state.query if isinstance(state.query, dict) else {}
    import json as _json
    cat_map = _json.dumps(
        {s.name: getattr(s, "category", "Otras") for s in registry.all_sources()},
        ensure_ascii=False)
    return render_template_string(
        TEMPLATE_RESULTS,
        query=query, token=token, total_sources=total, cat_map=cat_map)


# ==============================================================
#  Búsqueda por NIT — página resumen (empresa + representantes)
# ==============================================================
NIT_TEMPLATE = ui_theme.head_open("VerifyData — Empresa · NIT {{ nit }}") + \
    ui_theme.shell_open("empresa", "Verificar empresa",
                        "Empresa (NIT) · Reporte consolidado") + """
<style>{% raw %}
  .nit .status-line{font-size:14px;color:var(--text);font-weight:600;display:flex;align-items:center;gap:10px;}
  .nit .status-line.err{color:#b91c1c;}
  .nit .card-h{font-weight:700;color:var(--text);font-size:15px;margin:0 0 6px;}
  .nit table.kv{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px;}
  .nit .kv th{text-align:left;color:var(--text-faint);font-weight:600;font-size:11px;
    text-transform:uppercase;letter-spacing:.05em;padding:7px 12px 7px 0;white-space:nowrap;
    vertical-align:top;width:180px;}
  .nit .kv td{color:var(--text);font-size:13px;padding:7px 0;font-weight:600;border-top:1px solid var(--line);}
  .nit .kv tr:first-child th,.nit .kv tr:first-child td{border-top:none;}
  .nit .report-cta{display:flex;align-items:center;gap:16px;justify-content:space-between;
    flex-wrap:wrap;background:linear-gradient(135deg,#2b0f4a,#050A5C 60%,#1a1050);color:#fff;
    border:none;border-radius:14px;padding:20px 22px;margin-bottom:16px;box-shadow:var(--shadow);}
  .nit .report-cta .rc-t{font-weight:700;font-size:16px;}
  .nit .report-cta .rc-s{color:#c7cdf5;font-size:12px;margin-top:4px;}
  .nit .report-cta .pbar{background:rgba(255,255,255,.14);border:none;max-width:340px;width:100%;margin-top:10px;}
  .nit .emp{border-left:3px solid var(--violet);padding-left:14px;margin:14px 0;}
  .nit .emp .rz{font-weight:700;color:var(--text);font-size:16px;}
  .nit .emp .meta{color:var(--text-dim);font-size:13px;margin-top:2px;}
  .nit .reps{margin:10px 0 0;padding:0;list-style:none;}
  .nit .reps li{padding:8px 0;border-top:1px solid var(--line);font-size:13.5px;color:var(--text-dim);}
  .nit .tag{display:inline-block;font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;
    letter-spacing:.4px;vertical-align:1px;margin-right:4px;}
  .nit .tag-p{background:rgba(62,122,249,.12);color:#1d4ed8;}
  .nit .tag-e{background:rgba(105,65,244,.12);color:#5b21b6;}
  .nit .tag-lvl{background:#f1f0f6;color:var(--text-dim);}
  .nit .person{display:flex;align-items:center;gap:14px;justify-content:space-between;
    padding:14px 16px;border:1px solid var(--line);border-radius:12px;margin-bottom:10px;background:#fff;}
  .nit .person .who{font-weight:700;color:var(--text);font-size:14.5px;}
  .nit .person .det{color:var(--text-faint);font-size:12px;margin-top:2px;}
  .nit .bar{height:8px;background:#f1f0f6;border-radius:5px;overflow:hidden;width:200px;margin-top:8px;}
  .nit .bar>i{display:block;height:100%;background:var(--grad-btn);width:0;border-radius:5px;transition:width .4s;}
  .nit .counts{font-size:12px;color:var(--text-faint);margin-top:6px;}
  .nit .counts b.m{color:#15803d;}.nit .counts b.c{color:#5b21b6;}.nit .counts b.e{color:#b91c1c;}
  .nit .err{color:#b91c1c;font-weight:600;}
{% endraw %}</style>

<div class="nit">
  <div class="card res-header">
    <div class="who">
      <div class="wordmark wm-light wm-sm">Verify<span>Data</span></div>
      <p class="kicker" style="margin-bottom:4px">Reporte de verificación · Empresa</p>
      <h3 id="emp-title">Empresa · NIT {{ nit }}</h3>
      <div class="meta" id="emp-sub">Resolviendo en RUES…</div>
    </div>
    <div class="acts"><a class="btn btn-secondary" href="/">Nueva búsqueda</a></div>
  </div>

  <div class="card pad" id="status-card" style="margin-bottom:16px">
    <div class="status-line"><span class="spin"></span> Consultando RUES…</div>
  </div>

  <div id="empresa-card"></div>
  <div id="empresa-report"></div>
  <div id="tree"></div>
  <div id="people"></div>
</div>

<script>
var TOKEN = {{ token|tojson }};
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}
function kvRow(k,v){ return v? ('<tr><th>'+esc(k)+'</th><td>'+esc(v)+'</td></tr>') : ''; }
function renderEmpresaCard(d){
  var e=d.empresa_datos||{}; var el=document.getElementById('empresa-card');
  var hasData = e.razon_social||e.matricula||e.estado||e.camara||e.organizacion;
  if(!hasData && !e.error){ el.innerHTML=''; return; }
  var rows = kvRow('Razón social', e.razon_social)+
             kvRow('NIT', e.nit||d.nit)+
             kvRow('Matrícula mercantil', e.matricula)+
             kvRow('Estado', e.estado)+
             kvRow('Cámara de comercio', e.camara)+
             kvRow('Organización jurídica', e.organizacion)+
             kvRow('Categoría', e.categoria);
  var err = e.error? ('<div class="err" style="font-size:13px;margin-top:8px">'+esc(e.error)+'</div>') : '';
  el.innerHTML = '<div class="card pad" style="margin-bottom:16px"><div class="card-h">Registro mercantil (RUES)</div>'+
     (rows? ('<table class="kv">'+rows+'</table>') : '')+err+'</div>';
}
function renderEmpresaReport(d){
  var el=document.getElementById('empresa-report');
  if(d.mode!=='empresa' && d.mode!=='ambas'){ el.innerHTML=''; return; }
  var run=d.empresa_run||null; var tok=d.empresa_token;
  if(!tok){
    el.innerHTML = run===null && d.status==='resolving' ? '' :
      '<div class="card pad" style="margin-bottom:16px"><div class="err" style="font-size:13px">No se pudo lanzar el reporte de la empresa'+
      ((d.empresa_datos&&d.empresa_datos.launch_error)?(': '+esc(d.empresa_datos.launch_error)):'')+'</div></div>';
    return;
  }
  var total=(run&&run.total)||0, done=(run&&run.done)||0;
  var pct= total? Math.round(done*100/total):0;
  var completed=(run&&run.completed);
  el.innerHTML =
    '<div class="report-cta"><div style="flex:1;min-width:220px">'+
      '<div class="rc-t">Reporte de la empresa'+(d.empresa?(' · '+esc(d.empresa)):'')+'</div>'+
      '<div class="rc-s">'+done+'/'+total+' fuentes · '+
        '<b style="color:#4ade80">'+((run&&run.matched)||0)+' con dato</b> · '+
        '<b style="color:#c4b5fd">'+((run&&run.captcha)||0)+' verificar</b> · '+
        '<b style="color:#fca5a5">'+((run&&run.error)||0)+' error</b></div>'+
      '<div class="pbar"><i style="width:'+pct+'%"></i></div>'+
    '</div>'+
    '<a class="btn btn-primary" href="/results/'+encodeURIComponent(tok)+'">'+
      (completed?'Ver reporte':'Ver progreso')+'</a>'+
    '</div>';
}
function renderTree(tree){
  var h='';
  (tree||[]).forEach(function(n){
    h+='<div class="emp">';
    h+='<div class="rz">'+esc(n.razon_social||('NIT '+n.nit));
    if(n.nivel>0) h+=' <span class="tag tag-lvl">nivel '+n.nivel+'</span>';
    h+='</div>';
    h+='<div class="meta">NIT '+esc(n.nit)+(n.estado?(' · '+esc(n.estado)):'')+
       (n.camara?(' · '+esc(n.camara)):'')+'</div>';
    if(n.error){ h+='<div class="err" style="font-size:13px">'+esc(n.error)+'</div>'; }
    if(n.reps && n.reps.length){
      h+='<ul class="reps">';
      n.reps.forEach(function(r){
        var t=r.es_empresa?'<span class="tag tag-e">EMPRESA</span>':'<span class="tag tag-p">PERSONA</span>';
        h+='<li>'+t+' <b style="color:var(--text)">'+esc(r.cargo)+'</b>: '+esc(r.nombre)+
           (r.identificacion?(' · id '+esc(r.identificacion)):'')+'</li>';
      });
      h+='</ul>';
    }
    h+='</div>';
  });
  document.getElementById('tree').innerHTML = h ?
    ('<div class="card pad" style="margin-bottom:16px"><div class="card-h">Estructura societaria y representantes</div>'+h+'</div>') : '';
}
function renderPeople(personas){
  if(!personas || !personas.length){ document.getElementById('people').innerHTML=''; return; }
  var h='<div class="card pad" style="margin-bottom:16px"><div class="card-h">Personas consultadas ('+personas.length+')</div>';
  personas.forEach(function(p){
    var run=p.run||{}; var total=run.total||0; var done=run.done||0;
    var pct= total? Math.round(done*100/total):0;
    var completed=run.completed;
    h+='<div class="person"><div style="flex:1"><div class="who">'+esc(p.nombre)+'</div>'+
       '<div class="det">CC '+esc(p.cedula)+' · '+esc(p.cargo)+' @ '+esc(p.empresa)+'</div>';
    if(p.token){
      h+='<div class="bar"><i style="width:'+pct+'%"></i></div>'+
         '<div class="counts">'+done+'/'+total+' fuentes · '+
         '<b class="m">'+(run.matched||0)+' coinc.</b> · '+
         '<b class="c">'+(run.captcha||0)+' verificar</b> · '+
         '<b class="e">'+(run.error||0)+' error</b></div>';
    } else {
      h+='<div class="err" style="font-size:12px">No se pudo lanzar la búsqueda'+
         (p.launch_error?(': '+esc(p.launch_error)):'')+'</div>';
    }
    h+='</div>';
    if(p.token){
      h+='<a class="btn btn-primary btn-sm" href="/results/'+encodeURIComponent(p.token)+'">'+
         (completed?'Ver reporte':'Ver progreso')+'</a>';
    }
    h+='</div>';
  });
  h+='</div>';
  document.getElementById('people').innerHTML=h;
}
function poll(){
  fetch('/api/nit/'+encodeURIComponent(TOKEN)).then(function(r){return r.json();}).then(function(d){
    var mode=d.mode||'reps';
    var wantEmpresa=(mode==='empresa'||mode==='ambas');
    var wantReps=(mode==='reps'||mode==='ambas');
    if(d.empresa){ document.getElementById('emp-title').textContent='Empresa · '+d.empresa; }
    var empresaPending=wantEmpresa && d.empresa_token && (!d.empresa_run||!d.empresa_run.completed);
    var personasPending=(d.personas||[]).some(function(p){return p.token && (!p.run||!p.run.completed);});
    var sc=document.getElementById('status-card');
    if(d.status==='resolving'){
      document.getElementById('emp-sub').textContent='Resolviendo en RUES…';
      sc.innerHTML='<div class="status-line"><span class="spin"></span> Consultando RUES'+
        (wantReps?', extrayendo representantes legales':'')+'…</div>';
    } else if(d.status==='error'){
      document.getElementById('emp-sub').textContent='No fue posible resolver el NIT.';
      sc.innerHTML='<div class="status-line err">'+esc(d.error||'Error desconocido')+'</div>';
    } else if(d.status==='ready'){
      var allDone=!empresaPending && !personasPending;
      var parts=[];
      if(wantEmpresa) parts.push('reporte de empresa');
      if(wantReps) parts.push((d.personas||[]).length+' representante(s)');
      document.getElementById('emp-sub').textContent=
        parts.join(' · ')+' · '+(allDone?'consultas completadas':'consultas en curso…');
      sc.innerHTML='<div class="status-line">'+(allDone?'✓ ':'<span class="spin"></span> ')+
        'Empresa resuelta desde RUES.</div>';
    }
    renderEmpresaCard(d);
    if(wantEmpresa) renderEmpresaReport(d); else document.getElementById('empresa-report').innerHTML='';
    if(wantReps){ renderTree(d.tree); renderPeople(d.personas); }
    else { document.getElementById('tree').innerHTML=''; document.getElementById('people').innerHTML=''; }
    var keep=(d.status==='resolving') || (d.status==='ready' && (empresaPending||personasPending));
    if(keep) setTimeout(poll, 2500);
  }).catch(function(){ setTimeout(poll, 4000); });
}
poll();
</script>
""" + ui_theme.SHELL_CLOSE


@app.route("/nit/<token>")
def nit_results(token: str):
    """Página resumen de una búsqueda por NIT."""
    from runs import get_nit_run
    state = get_nit_run(token)
    if not state:
        return redirect(url_for("index"), code=303)
    return render_template_string(NIT_TEMPLATE, token=token, nit=state.nit)


@app.route("/api/nit/<token>")
def api_nit(token: str):
    """Estado JSON del run-NIT (para polling)."""
    from runs import nit_run_payload
    from flask import jsonify
    payload = nit_run_payload(token)
    if payload is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(payload)


@app.route("/api/run/<token>")
def api_run(token: str):
    """Endpoint JSON para polling de resultados.

    Enriquece cada fuente con datos derivados del reporte PDF (única fuente
    de verdad) para que el front-end muestre lo mismo que el PDF:
      - evidence_img / evidence_kind: captura del certificado PDF rasterizado
        (o screenshot) — el front la embebe como <img>.
      - is_principal / principal_order: fuentes principales/excluyentes primero.
      - clean_rows: filas key/value limpias y prettificadas.
      - status_kind: match | nomatch | nodisp | manual | error."""
    from runs import get_run
    from flask import jsonify
    state = get_run(token)
    if not state:
        return jsonify({"error": "not found"}), 404
    d = state.to_dict()
    try:
        from types import SimpleNamespace
        from report import (_evidence_for, _detail_rows_for, _is_principal,
                            _principal_order, _status_kind, _status_group,
                            LABELS)
        for _name, hd in (d.get("sources") or {}).items():
            if not isinstance(hd, dict):
                continue
            ns = SimpleNamespace(**hd)
            try:
                img, kind, _pdf = _evidence_for(ns)
                if img:
                    hd["evidence_img"] = img.relative_to(DATA).as_posix()
                    hd["evidence_kind"] = kind
            except Exception:
                # No romper la respuesta por una evidencia; dejar rastro y
                # devolver evidence_img: null en vez de tragarse el error.
                log.warning("evidencia no resuelta para fuente %r (run %s)",
                            _name, token, exc_info=True)
                hd["evidence_img"] = None
            try:
                hd["is_principal"] = _is_principal(ns)
                hd["principal_order"] = _principal_order(ns)
                hd["status_kind"] = _status_kind(ns)
                hd["status_group"] = _status_group(ns)
                hd["status_label"] = LABELS.get(hd["status_kind"],
                                                hd["status_kind"].upper())
                hd["clean_rows"] = _detail_rows_for(ns, limit=12)
            except Exception:
                log.warning("enriquecimiento fallido para fuente %r (run %s)",
                            _name, token, exc_info=True)
    except Exception:
        log.exception("fallo enriqueciendo resultados del run %s", token)
    return jsonify(d)


@app.route("/api/refresh-lists", methods=["POST"])
@_auth.require_role("admin")
def api_refresh_lists():
    """Actualiza las listas estáticas (OFAC, UN, EU, UK, etc).

    Solo admin: es una operación pesada (descarga OFAC/UN/EU/…) que un viewer
    no debe poder disparar. Ver A7 del handoff de seguridad."""
    from lists import LocalListManager
    from lists.downloaders import (ofac_sdn, ofac_consolidated, ofac_addrs,
                                    un_consolidated, eu_consolidated,
                                    bis_dpl, canada_sema,
                                    worldbank_ineligible, nca_uk_most_wanted,
                                    fbi_wanted, interpol_red)
    from flask import jsonify
    results = {}
    mgr = LocalListManager()
    jobs = {
        "ofac_sdn": ofac_sdn, "ofac_consolidated": ofac_consolidated,
        "ofac_addrs": ofac_addrs, "un_consolidated": un_consolidated,
        "eu_consolidated": eu_consolidated, "bis_dpl": bis_dpl,
        "canada_sema": canada_sema, "worldbank_ineligible": worldbank_ineligible,
        "nca_uk_most_wanted": nca_uk_most_wanted,
        "fbi_wanted": fbi_wanted, "interpol_red": interpol_red,
    }
    for name, fetcher in jobs.items():
        try:
            n = mgr.refresh(name, fetcher, force=True)
            results[name] = {"ok": True, "count": n}
        except Exception as e:
            results[name] = {"ok": False, "error": str(e)}
    audit("lists_refresh", user=_audit_user(),
          ok=[k for k, v in results.items() if v.get("ok")],
          fail=[k for k, v in results.items() if not v.get("ok")])
    return jsonify({"results": results, "timestamp":
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@app.route("/api/lists-inventory")
def api_lists_inventory():
    """Inventario de listas estáticas (para cron weekly)."""
    from flask import jsonify
    from lists import LocalListManager
    mgr = LocalListManager()
    from db import get_db
    inventory = []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT source, url, last_fetched, last_count, format "
                    "FROM list_meta ORDER BY source")
        for row in cur.fetchall():
            inventory.append({
                "source": row["source"], "url": row["url"],
                "last_fetched": row["last_fetched"],
                "last_count": row["last_count"],
                "format": row["format"],
            })
    return jsonify({"lists": inventory,
                   "total_lists": len(inventory)})


@app.route("/nueva")
def nueva():
    return redirect(url_for("index"), code=303)


@app.route("/clear")
def clear():
    RESULTS_CACHE.clear()
    return redirect(url_for("index"), code=303)


@app.route("/download/<path:filename>")
def download(filename: str):
    full = (DATA / filename).resolve()
    if not str(full).startswith(str(DATA.resolve())):
        abort(404)
    if not full.exists():
        abort(404)
    # Detectar mimetype por extensión (imágenes, pdf, html)
    ext = full.suffix.lower()
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".pdf": "application/pdf", ".json": "application/json",
    }.get(ext, "application/octet-stream")
    # Inline (no as_attachment) para que el browser las renderice en <img>
    return send_file(str(full), mimetype=mime, conditional=True)


@app.route("/download/pdf/<token>")
def download_pdf(token: str):
    """Genera y descarga el reporte PDF del search run."""
    from flask import Response
    from report import generate_pdf
    from runs import get_run
    state = get_run(token)
    if not state:
        abort(404)
    if not state.sources:
        # El run existe pero aún no hay resultados: no confundir con 404.
        from flask import jsonify
        resp = jsonify({"error": "run_in_progress",
                        "detail": "La búsqueda aún no tiene resultados; "
                                  "reintentar en unos segundos.",
                        "token": token})
        resp.status_code = 202
        resp.headers["Retry-After"] = "5"
        return resp
    # Reconstruir los Hits con sus source_url
    from sources import registry
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
    # No incluir la cédula (dato personal, Ley 1581) en el nombre del archivo:
    # quedaría en el historial del navegador, logs de proxy y caché. Se usa el
    # token (no personal) y se sanitiza para evitar header injection.
    safe_token = re.sub(r"[^0-9A-Za-z_-]", "", token)[:32] or "reporte"
    fname = f"verifydata_{safe_token}.pdf"
    ced = state.query.get("cedula") if isinstance(state.query, dict) else None
    audit("pdf_download", user=_audit_user(), token=token, cedula=ced or None)
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename=\"{fname}\"; "
                             f"filename*=UTF-8''{fname}"})


# ==============================================================
#  Página de Perfil Crediticio
# ==============================================================

CREDITO_TEMPLATE = ui_theme.head_open("VerifyData — Perfil Crediticio") + \
    ui_theme.shell_open("credito", "Perfil crediticio",
                        "Evaluación de riesgo · Crédito") + """
<style>{% raw %}
  .cr-form .section-title{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-faint);margin:0 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .cr-form .field-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
  .cr-form .field-grid.full{grid-template-columns:1fr}
  .cr-form .field-compact label{font-size:11px;margin-bottom:2px}
  .cr-form .field-compact input,.cr-form .field-compact select{font-size:13px;padding:9px 11px}
  .cr-form .checkbox-row{display:flex;gap:24px;align-items:center;margin:10px 0}
  .cr-form .checkbox-row label{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:500;cursor:pointer}
  .cr-form .checkbox-row input[type=checkbox]{width:17px;height:17px;accent-color:var(--violet)}
  .cr-result{background:linear-gradient(135deg,rgba(105,65,244,.04),rgba(62,122,249,.04));border:1px solid rgba(105,65,244,.18);border-radius:14px;padding:24px;margin-top:20px}
  .cr-result .score-big{font-size:56px;font-weight:800;line-height:1}
  .cr-result .score-label{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-top:6px}
  .cr-result .risk-pill{display:inline-block;padding:6px 16px;border-radius:20px;font-size:13px;font-weight:700;letter-spacing:.03em}
  .cr-result .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:18px}
  .cr-result .stat-val{font-size:18px;font-weight:700}
  .cr-result .stat-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-faint)}
  .subjects-dropdown{max-height:300px;overflow-y:auto;border:1px solid var(--line);border-radius:10px;margin-top:8px;background:#fff}
  .subjects-dropdown .opt{padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--line);font-size:13px}
  .subjects-dropdown .opt:hover{background:rgba(105,65,244,.06)}
  .subjects-dropdown .opt .sub{color:var(--text-faint);font-size:11px}
  .hidden{display:none!important}
  .mini-badge{display:inline-block;padding:2px 7px;border-radius:9px;font-size:10px;font-weight:700;margin-left:6px}
  .mini-badge.warn{background:rgba(239,68,68,.1);color:#b91c1c}
  .mini-badge.good{background:rgba(34,197,94,.1);color:#15803d}
  .act-bar{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
  .doc-preview{margin-top:6px;font-size:11px;color:var(--text-faint)}
  .doc-preview img{max-width:120px;max-height:80px;border-radius:6px;border:1px solid var(--line)}
  .doc-preview .doc-ok{color:#15803d;font-weight:600}
  .spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--violet);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px}
  @keyframes spin{to{transform:rotate(360deg)}}
  @media(max-width:800px){.cr-form .field-grid{grid-template-columns:1fr}.cr-result .stats{grid-template-columns:1fr 1fr}}
{% endraw %}</style>

<div class="main-content">
  <div class="hero-row" style="margin-bottom:8px">
    <div class="menu-hero">
      <p class="eyebrow">Riesgo crediticio</p>
      <h2>Evalúa la capacidad de pago de un cliente</h2>
      <p>Ingresa los datos financieros del cliente para obtener un
         <b style="color:var(--text)">score crediticio</b>, nivel de riesgo y
         monto máximo recomendado, combinando datos de RSales.</p>
    </div>
  </div>

  <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
    <button type="button" class="btn btn-primary" onclick="cargarSiguienteSujeto()" style="font-size:14px;padding:12px 24px">
      ⚡ Cargar siguiente cliente de prueba
    </button>
    <button type="button" class="btn btn-secondary" onclick="document.getElementById('subjects-list').classList.toggle('hidden')">
      📋 Ver lista de clientes
    </button>
    <button type="button" class="btn btn-ghost btn-sm" onclick="descargarExcel()" id="btn-descargar" style="display:none">
      ⬇ Descargar Excel
    </button>
    <span id="rsales-status" style="font-size:11px;color:var(--text-faint);display:none"></span>
  </div>

  <div id="subjects-list" class="card pad subjects-dropdown hidden" style="margin-bottom:16px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <span style="font-weight:700;font-size:14px">Sujetos de prueba (65 clientes del Excel)</span>
      <input type="text" id="subj-search" placeholder="Buscar por nombre o cédula…" style="width:280px;font-size:12px;padding:6px 10px;border:1px solid var(--line);border-radius:8px"
             oninput="filterSubjects()">
    </div>
    <div id="subjects-options"></div>
  </div>

  <form id="credito-form" class="cr-form">
    <!-- DATOS BÁSICOS -->
    <div class="card pad" style="margin-bottom:14px">
      <div class="section-title">Datos del cliente</div>
      <div class="field-grid">
        <div class="field field-compact full">
          <label>Nombre completo</label>
          <input name="nombre" id="cr-nombre" placeholder="Ej: Juan Pérez" required>
        </div>
        <div class="field field-compact">
          <label>Cédula / NIT</label>
          <input name="cedula" id="cr-cedula" placeholder="1234567890" required>
        </div>
        <div class="field field-compact">
          <label>Fecha expedición cédula</label>
          <input name="fecha_expedicion" id="cr-feexp" type="date" placeholder="DD/MM/AAAA">
        </div>
        <div class="field field-compact">
          <label>Tipo de solicitud</label>
          <select name="tipo_solicitud" id="cr-tipo">
            <option value="SOLICITUD DE CREDITO">Solicitud de crédito</option>
            <option value="AUMENTO DE CUPO">Aumento de cupo</option>
            <option value="TRASLADO DE CUPO">Traslado de cupo</option>
            <option value="ACTIVACION CLIENTE">Activación cliente</option>
          </select>
        </div>
      </div>
    </div>

    <!-- DATOS FINANCIEROS -->
    <div class="card pad" style="margin-bottom:14px">
      <div class="section-title">Información financiera</div>
      <div class="field-grid">
        <div class="field field-compact">
          <label>Crédito actual ($)</label>
          <input name="credito_actual" id="cr-cactual" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Monto a solicitar ($)</label>
          <input name="monto_solicitar" id="cr-msolicitar" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Cupo inicial ($)</label>
          <input name="cupo_inicial" id="cr-cinicial" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Ingreso mensual ($)</label>
          <input name="ingreso_mensual" id="cr-ingreso" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Fuente de ingreso</label>
          <select name="fuente_ingreso" id="cr-fuente">
            <option value="">Seleccionar...</option>
            <option value="Empleado formal">Empleado formal</option>
            <option value="Independiente">Independiente</option>
            <option value="Comerciante">Comerciante</option>
            <option value="Profesional independiente">Profesional independiente</option>
            <option value="Pensionado">Pensionado</option>
            <option value="Otro">Otro</option>
          </select>
        </div>
        <div class="field field-compact">
          <label>Actividad económica</label>
          <input name="actividad_economica" id="cr-actividad" placeholder="Ej: Comercio al por menor">
        </div>
        <div class="field field-compact">
          <label>Promedio compras ($)</label>
          <input name="promedio_compras" id="cr-pcompras" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Compra mínima ($)</label>
          <input name="compra_minima" id="cr-cmin" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Compra máxima ($)</label>
          <input name="compra_maxima" id="cr-cmax" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Número de compras</label>
          <input name="numero_compras" id="cr-ncompras" type="number" placeholder="0">
        </div>
        <div class="field field-compact">
          <label>Año del dato de compras</label>
          <input name="ano_dato_compras" id="cr-ano" type="number" placeholder="2026" value="2026">
        </div>
        <div class="field field-compact">
          <label>Promedio de pago (días)</label>
          <input name="promedio_pago_dias" id="cr-ppago" type="number" placeholder="30" step="any">
        </div>
        <div class="field field-compact">
          <label>Patrimonio estimado ($)</label>
          <input name="patrimonio" id="cr-patrimonio" type="number" placeholder="0" step="any">
        </div>
        <div class="field field-compact">
          <label>Endeudamiento total ($)</label>
          <input name="endeudamiento" id="cr-endeudamiento" type="number" placeholder="0" step="any">
        </div>
      </div>
    </div>

    <!-- RIESGO -->
    <div class="card pad" style="margin-bottom:14px">
      <div class="section-title">Datos de riesgo</div>
      <div class="field-grid">
        <div class="field field-compact">
          <label>Calificación Datacrédito (0-1000)</label>
          <input name="calificacion_datacredito" id="cr-dc" type="number" placeholder="0" min="0" max="1000">
        </div>
        <div class="field field-compact">
          <label>Consultas últimos 6 meses</label>
          <input name="consultas_6m" id="cr-cons6m" placeholder="Ej: 3 consultas">
        </div>
      </div>
      <div class="checkbox-row" style="margin-top:12px">
        <label><input type="checkbox" name="presenta_mora" id="cr-mora"> Presenta mora</label>
        <label><input type="checkbox" name="presenta_cartera_castigada" id="cr-castigada"> Cartera castigada</label>
        <label><input type="checkbox" name="aprobacion" id="cr-aprobacion"> Aprobación previa</label>
      </div>
      <div class="field field-compact" style="margin-top:10px">
        <label>Observaciones</label>
        <input name="observaciones" id="cr-obs" placeholder="Notas adicionales…">
      </div>
    </div>

    <!-- DOCUMENTOS -->
    <div class="card pad" style="margin-bottom:14px">
      <div class="section-title">Documentos adjuntos (opcional)</div>
      <p style="font-size:12px;color:var(--text-faint);margin:0 0 14px">Sube documentos para completar el perfil. La documentación completa mejora el score (5%).</p>
      <div class="field-grid" style="grid-template-columns:1fr 1fr">
        <div class="field field-compact">
          <label>📄 Cédula frontal</label>
          <input type="file" id="doc-cedula-front" accept="image/*,.pdf" onchange="previewDoc(this,'cel-front')">
          <div id="cel-front" class="doc-preview"></div>
        </div>
        <div class="field field-compact">
          <label>📄 Cédula posterior</label>
          <input type="file" id="doc-cedula-back" accept="image/*,.pdf" onchange="previewDoc(this,'cel-back')">
          <div id="cel-back" class="doc-preview"></div>
        </div>
        <div class="field field-compact">
          <label>📄 RUT</label>
          <input type="file" id="doc-rut" accept="image/*,.pdf" onchange="previewDoc(this,'rut-preview')">
          <div id="rut-preview" class="doc-preview"></div>
        </div>
        <div class="field field-compact">
          <label>📄 Cámara de comercio</label>
          <input type="file" id="doc-camara" accept="image/*,.pdf" onchange="previewDoc(this,'cam-preview')">
          <div id="cam-preview" class="doc-preview"></div>
        </div>
        <div class="field field-compact">
          <label>📄 Estados financieros</label>
          <input type="file" id="doc-estados" accept="image/*,.pdf,.xlsx,.xls" onchange="previewDoc(this,'ef-preview')">
          <div id="ef-preview" class="doc-preview"></div>
        </div>
        <div class="field field-compact">
          <label>📄 Declaración de renta</label>
          <input type="file" id="doc-renta" accept="image/*,.pdf" onchange="previewDoc(this,'renta-preview')">
          <div id="renta-preview" class="doc-preview"></div>
        </div>
      </div>
    </div>

    <div class="act-bar">
      <button type="button" class="btn btn-primary" onclick="ejecutarCheckIntegral()">📊 Evaluar riesgo crediticio</button>
      <button type="button" class="btn btn-ghost" onclick="limpiarForm()">Limpiar</button>
    </div>
  </form>

  <!-- RESULTADO -->
  <div id="resultado" class="cr-result" style="display:none"></div>
</div>

<script>
var CEDULA_ACTUAL = '';
var RSALES_DATA = null;
var RESULTADO_ACTUAL = null;
var SUBJECT_INDEX = 0;

(function(){
  fetch('/api/credit/warm-rsales').catch(function(){});
})();

function previewDoc(input, previewId) {
  var el = document.getElementById(previewId);
  if (!input.files || !input.files[0]) { el.innerHTML = ''; return; }
  var file = input.files[0];
  if (file.type.indexOf('image') >= 0) {
    var reader = new FileReader();
    reader.onload = function(e) {
      el.innerHTML = '<img src="' + e.target.result + '">' +
        '<div class="doc-ok">&#10003; ' + file.name + '</div>';
    };
    reader.readAsDataURL(file);
  } else {
    el.innerHTML = '<div class="doc-ok">&#10003; ' + file.name + ' (' +
      (file.size/1024).toFixed(1) + ' KB)</div>';
  }
}

function getDocsFlags() {
  return {
    cedula_frontal: !!document.getElementById('doc-cedula-front').files.length,
    cedula_posterior: !!document.getElementById('doc-cedula-back').files.length,
    rut: !!document.getElementById('doc-rut').files.length,
    camara_comercio: !!document.getElementById('doc-camara').files.length,
    estados_financieros: !!document.getElementById('doc-estados').files.length,
    declaracion_renta: !!document.getElementById('doc-renta').files.length
  };
}

var SUBJECTS = {{ subjects_json|safe }};

function escH(s){ var d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; }

function toast(msg){ var t=document.createElement('div'); t.className='toast show'; t.innerHTML='<span class="dot"></span>'+msg;
  document.body.appendChild(t); setTimeout(function(){t.remove();},2200);}

function cargarSiguienteSujeto() {
  if (!SUBJECTS || SUBJECTS.length === 0) { alert('No hay sujetos de prueba'); return; }
  var s = SUBJECTS[SUBJECT_INDEX % SUBJECTS.length];
  SUBJECT_INDEX++;
  _llenarFormulario(s);
}

function _llenarFormulario(s) {
  document.getElementById('cr-nombre').value = s.nombre || '';
  document.getElementById('cr-cedula').value = s.cedula_nit || '';
  document.getElementById('cr-tipo').value = s.tipo_solicitud || 'SOLICITUD DE CREDITO';

  // Datos financieros del Excel (con fallbacks coherentes)
  var promedio = s.promedio_compras || 0;
  var credAprob = s.credito_aprobado || 0;
  var montoSol = s.monto_solicitar || 0;

  // Crédito actual = crédito aprobado anterior (si existe)
  document.getElementById('cr-cactual').value = credAprob || Math.round(promedio * 0.8);

  // Monto a solicitar = monto del Excel o estimado
  document.getElementById('cr-msolicitar').value = montoSol || Math.round(promedio * 0.6);

  // Cupo inicial = crédito aprobado * 1.2 ( típico 20% más)
  document.getElementById('cr-cinicial').value = s.cupo_inicial || Math.round(credAprob * 1.2) || Math.round(promedio * 1.5);

  // Ingreso mensual estimado (promedio compras / 0.6 = ingreso bruto)
  document.getElementById('cr-ingreso').value = s.ingreso_mensual || Math.round(promedio * 1.2);

  // Fuente de ingreso
  document.getElementById('cr-fuente').value = s.fuente_ingreso || 'Independiente';

  // Actividad económica
  document.getElementById('cr-actividad').value = s.actividad_economica || 'Comercio al por menor';

  // Promedio compras del Excel
  document.getElementById('cr-pcompras').value = promedio;

  // Compra mínima/máxima = estimadas desde promedio
  document.getElementById('cr-cmin').value = s.compra_minima || (promedio > 0 ? Math.round(promedio * 0.3) : 0);
  document.getElementById('cr-cmax').value = s.compra_maxima || (promedio > 0 ? Math.round(promedio * 2.5) : 0);

  // Número de compras
  document.getElementById('cr-ncompras').value = s.numero_compras || (promedio > 0 ? Math.floor(Math.random() * 15) + 5 : 0);

  // Año del dato
  document.getElementById('cr-ano').value = s.ano_dato_compras || 2026;

  // Promedio pago días
  document.getElementById('cr-ppago').value = s.promedio_pago_dias || 30;

  // Patrimonio estimado (crédito aprobado * 3)
  document.getElementById('cr-patrimonio').value = s.patrimonio || Math.round(credAprob * 3) || Math.round(promedio * 4);

  // Endeudamiento total (crédito actual * 1.3)
  document.getElementById('cr-endeudamiento').value = s.endeudamiento || Math.round(credAprob * 1.3) || Math.round(promedio * 1.5);

  // Calificación datacrédito
  document.getElementById('cr-dc').value = s.calificacion_datacredito || (600 + Math.floor(Math.random() * 200));

  // Consultas 6 meses
  document.getElementById('cr-cons6m').value = s.consultas_6m_sector_real || String(Math.floor(Math.random() * 5) + 1);

  // Checkboxes
  document.getElementById('cr-mora').checked = !!s.presenta_mora;
  document.getElementById('cr-castigada').checked = !!s.presenta_cartera_castigada;
  document.getElementById('cr-aprobacion').checked = !!s.aprobacion;

  // Observaciones
  document.getElementById('cr-obs').value = s.observaciones || '';

  // Fecha expedición (nuevo campo)
  var feExp = document.getElementById('cr-feexp');
  if (feExp) feExp.value = s.fecha_expedicion || '';

  CEDULA_ACTUAL = s.cedula_nit;
  document.getElementById('subjects-list').classList.add('hidden');
  var tags = '';
  if (s.mora) tags += ' &#9888;MORA';
  if (s.castigada) tags += ' &#9888;CASTIGO';
  toast('Sujeto ' + SUBJECT_INDEX + '/' + SUBJECTS.length + ': ' + (s.nombre||'').slice(0,35) + tags);
}

function renderSubjects(list) {
  var html = '';
  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var badges = '';
    if (s.mora) badges += '<span class="mini-badge warn">MORA</span>';
    if (s.castigada) badges += '<span class="mini-badge warn">CASTIGADA</span>';
    if (s.credito_aprobado) badges += '<span class="mini-badge good">$' + (s.credito_aprobado||0).toLocaleString('es-CO') + '</span>';
    html += '<div class="opt" data-idx="' + i + '">' +
      '<b>' + escH(s.nombre) + '</b>' + badges +
      '<div class="sub">CC ' + escH(s.cedula_nit) +
        (s.ano_dato_compras ? ' &#183; Compras ' + s.ano_dato_compras : '') +
        (s.ejecutivo ? ' &#183; ' + escH(s.ejecutivo) : '') +
      '</div></div>';
  }
  document.getElementById('subjects-options').innerHTML = html || '<div class="opt" style="color:var(--text-faint)">Sin resultados</div>';
  var opts = document.getElementById('subjects-options').querySelectorAll('.opt');
  for (var j = 0; j < opts.length; j++) {
    opts[j].addEventListener('click', function() {
      var idx = parseInt(this.getAttribute('data-idx'));
      SUBJECT_INDEX = idx;
      cargarSiguienteSujeto();
    });
  }
}

function filterSubjects() {
  var q = (document.getElementById('subj-search').value || '').toUpperCase();
  if (!q) { renderSubjects(SUBJECTS); return; }
  var filt = SUBJECTS.filter(function(s){
    return (s.nombre||'').toUpperCase().indexOf(q) >= 0 || (s.cedula_nit||'').indexOf(q) >= 0;
  });
  renderSubjects(filt);
}

renderSubjects(SUBJECTS);

// ── Form submit handler ─────────────────────────────────
document.getElementById('credito-form').addEventListener('submit', function(e) {
  e.preventDefault();
  ejecutarCheckIntegral();
});

// ── Check integral: crédito + antecedentes ──────────────
var CHECK_EN_CURSO = false;
function ejecutarCheckIntegral() {
  if (CHECK_EN_CURSO) return;
  var fd = new FormData(document.getElementById('credito-form'));
  var data = {};
  fd.forEach(function(v,k){ data[k] = v; });
  CEDULA_ACTUAL = data.cedula || '';
  if (!data.cedula) { toast('Ingrese una cedula o NIT'); return; }

  CHECK_EN_CURSO = true;
  var status = document.getElementById('rsales-status');
  status.style.display = '';
  status.innerHTML = '<span class="spinner"></span> Ejecutando check integral...';

  // Deshabilitar botón
  var btns = document.querySelectorAll('.btn-primary');
  for (var i = 0; i < btns.length; i++) btns[i].disabled = true;

  var docs = getDocsFlags();
  data.docs = docs;

  fetch('/api/credit/full-check', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(data)
  }).then(function(r){
    if (!r.ok) return r.text().then(function(t){ throw new Error('HTTP ' + r.status + ': ' + t.slice(0,200)); });
    return r.json();
  }).then(function(d){
    CHECK_EN_CURSO = false;
    for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
    if (d.ok) {
      RESULTADO_ACTUAL = d;
      document.getElementById('btn-descargar').style.display = 'inline-flex';
      status.innerHTML = '&#10003; Check completo — Redirigiendo...';
      // Redirigir directo a la página de resultados
      window.location.href = '/credit/results/' + d.result_token;
    } else {
      status.innerHTML = '<span style="color:#dc2626">&#10007; Error: ' + escH(d.error || 'Desconocido') + '</span>';
    }
  }).catch(function(e){
    CHECK_EN_CURSO = false;
    for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
    status.innerHTML = '<span style="color:#dc2626">&#10007; Error de red: ' + escH(e.message) + '</span>';
  });
}

function renderCheckIntegral(d) {
  var r = d.result;
  var p = r.perfil_crediticio;
  var ant = r.antecedentes || {};
  var res = r.resumen_ejecutivo;
  var bg = {'BAJO':'#15803d','MEDIO':'#d97706','ALTO':'#dc2626','CRITICO':'#991b1b'};
  var color = bg[p.nivel_riesgo] || '#333';
  var tagColor = res.aprobado ? '#15803d' : '#dc2626';
  var tagText = res.aprobado ? 'APROBADO' : 'RECHAZADO';
  var tagIcon = res.aprobado ? '&#10003;' : '&#10007;';

  // Bloqueantes
  var bloqueantesHtml = '';
  if (res.bloqueantes && res.bloqueantes.length) {
    bloqueantesHtml = '<div style="background:rgba(220,38,38,.08);border:1px solid rgba(220,38,38,.2);border-radius:10px;padding:14px;margin-top:12px">'+
      '<div style="font-weight:700;font-size:13px;color:#dc2626;margin-bottom:8px">&#9888; BLOQUEANTES ENCONTRADOS</div>';
    for (var i = 0; i < res.bloqueantes.length; i++) {
      bloqueantesHtml += '<div style="color:#991b1b;font-size:12px;margin:4px 0;padding:6px 10px;background:rgba(220,38,38,.05);border-radius:6px">'+res.bloqueantes[i]+'</div>';
    }
    bloqueantesHtml += '</div>';
  }

  // Antecedentes
  var antHtml = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-top:12px">';
  var antKeys = Object.keys(ant);
  for (var i = 0; i < antKeys.length; i++) {
    var k = antKeys[i];
    var v = ant[k];
    var icon = v.matched ? '&#10007;' : (v.error ? '&#9888;' : '&#10003;');
    var ic = v.matched ? '#dc2626' : (v.error ? '#d97706' : '#15803d');
    var label = v.matched ? 'ENCONTRADO' : (v.error ? 'ERROR' : 'LIMPIO');
    antHtml += '<div style="padding:10px;border-radius:8px;border:1px solid var(--line);font-size:11px">'+
      '<div style="font-weight:700;margin-bottom:4px">'+escH(k)+'</div>'+
      '<div style="color:'+ic+';font-weight:600">'+icon+' '+label+'</div>'+
      (v.summary ? '<div style="color:var(--text-faint);margin-top:3px">'+escH(v.summary).slice(0,80)+'</div>' : '')+
      '</div>';
  }
  antHtml += '</div>';

  // RSales
  var rsalesHtml = '';
  if (p.rsales) {
    var rs = p.rsales;
    var vencColor = (rs.pct_vencida||0) > 30 ? '#dc2626' : ((rs.pct_vencida||0) > 15 ? '#d97706' : '#15803d');
    rsalesHtml = '<div style="margin-top:14px;padding:14px;background:rgba(62,122,249,.06);border:1px solid rgba(62,122,249,.15);border-radius:10px">'+
      '<div style="font-weight:700;font-size:12px;color:#3e7af9;margin-bottom:10px">&#128225; Datos RSALES — Historial comercial</div>'+
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;font-size:12px">'+
        '<div style="padding:8px;background:rgba(255,255,255,.6);border-radius:6px"><b style="color:var(--text-faint);font-size:10px">CARTERA TOTAL</b><br><span style="font-size:16px;font-weight:700">$'+Number(rs.cartera_total||0).toLocaleString('es-CO')+'</span></div>'+
        '<div style="padding:8px;background:rgba(255,255,255,.6);border-radius:6px"><b style="color:var(--text-faint);font-size:10px">CARTERA VENCIDA</b><br><span style="font-size:16px;font-weight:700;color:'+vencColor+'">$'+Number(rs.cartera_vencida||0).toLocaleString('es-CO')+' <small>('+Number(rs.pct_vencida||0).toFixed(1)+'%)</small></span></div>'+
        '<div style="padding:8px;background:rgba(255,255,255,.6);border-radius:6px"><b style="color:var(--text-faint);font-size:10px">COMPRAS TOTALES</b><br><span style="font-size:16px;font-weight:700">$'+Number(rs.compras_total||0).toLocaleString('es-CO')+'</span></div>'+
        '<div style="padding:8px;background:rgba(255,255,255,.6);border-radius:6px"><b style="color:var(--text-faint);font-size:10px">MORA MAXIMA</b><br><span style="font-size:16px;font-weight:700">'+(rs.dias_mora_max||0)+' dias</span></div>'+
      '</div></div>';
  } else {
    rsalesHtml = '<div style="margin-top:14px;padding:12px;background:rgba(0,0,0,.03);border-radius:10px;font-size:12px;color:var(--text-faint)">&#9888; Cliente no encontrado en RSales — sin historial comercial</div>';
  }

  // Docs
  var docsCount = 0;
  var docsTotal = Object.keys(p.docs).length;
  for (var dk in p.docs) { if (p.docs[dk]) docsCount++; }

  // Factores
  var posHtml = (p.factores_positivos||[]).map(function(f){return '<div style="color:#15803d;font-size:11px">&#10003; '+escH(f)+'</div>'}).join('');
  var negHtml = (p.factores_negativos||[]).map(function(f){return '<div style="color:#991b1b;font-size:11px">&#10007; '+escH(f)+'</div>'}).join('');

  var html = '<div style="border:2px solid '+color+';border-radius:14px;padding:24px;margin-top:20px">'+
    // Header
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">'+
      '<div>'+
        '<div style="font-weight:700;font-size:18px">'+escH(r.nombre)+'</div>'+
        '<div style="font-size:12px;color:var(--text-faint)">CC/NIT: '+escH(r.cedula_nit)+'</div>'+
      '</div>'+
      '<div style="text-align:center;padding:12px 24px;border-radius:12px;background:'+tagColor+';color:#fff">'+
        '<div style="font-size:24px">'+tagIcon+'</div>'+
        '<div style="font-size:14px;font-weight:700">'+tagText+'</div>'+
      '</div>'+
    '</div>'+
    // Score bar
    '<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">'+
      '<div style="font-size:48px;font-weight:800;color:'+color+'">'+p.score+'</div>'+
      '<div>'+
        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-faint)">Score crediticio</div>'+
        '<div style="font-size:14px;font-weight:700;color:'+color+'">'+p.nivel_riesgo+'</div>'+
        '<div style="font-size:12px">'+escH(p.recomendacion)+'</div>'+
      '</div>'+
      '<div style="margin-left:auto;text-align:right">'+
        '<div style="font-size:11px;color:var(--text-faint)">Monto max recomendado</div>'+
        '<div style="font-size:20px;font-weight:700">$'+Number(res.monto_maximo||0).toLocaleString('es-CO')+'</div>'+
      '</div>'+
    '</div>'+
    rsalesHtml +
    // Antecedentes
    '<div style="margin-top:14px;padding:14px;background:rgba(105,65,244,.04);border-radius:10px">'+
      '<div style="font-weight:700;font-size:12px;color:#6941f4;margin-bottom:4px">&#128269; Antecedentes y listas restrictivas</div>'+
      antHtml +
    '</div>'+
    bloqueantesHtml +
    // Docs + Factores
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px">'+
      '<div style="padding:12px;background:rgba(0,0,0,.02);border-radius:8px">'+
        '<div style="font-weight:700;font-size:11px;margin-bottom:6px">Documentos: '+docsCount+'/'+docsTotal+'</div>'+
        (posHtml||'<span style="color:var(--text-faint);font-size:11px">Sin factores positivos</span>')+
      '</div>'+
      '<div style="padding:12px;background:rgba(0,0,0,.02);border-radius:8px">'+
        '<div style="font-weight:700;font-size:11px;margin-bottom:6px">Alertas</div>'+
        (negHtml||'<span style="color:var(--text-faint);font-size:11px">Sin alertas</span>')+
      '</div>'+
    '</div>'+
  '</div>';

  document.getElementById('resultado').innerHTML = html;
  document.getElementById('resultado').style.display = '';
}

// ── Render resultado (legacy, compatible) ─────────────
var RC = {'BAJO':'#15803d','MEDIO':'#d97706','ALTO':'#dc2626','CRITICO':'#991b1b'};
function renderResultado(d) {
  var p = d.profile;
  var bg = RC[p.nivel_riesgo]||'#333';
  var rsalesBlock = '';
  if (p.detalle && p.detalle.rsales) {
    var r = p.detalle.rsales;
    rsalesBlock = '<div style="margin-top:16px;padding:14px;background:rgba(62,122,249,.06);border-radius:10px">'+
      '<div style="font-weight:700;font-size:12px;color:var(--blue);margin-bottom:10px">📡 Datos RSales</div>'+
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;font-size:12px">'+
        '<div><b>Cartera total</b><br>\$'+(r.cartera_total||0).toLocaleString('es-CO')+'</div>'+
        '<div><b>Cartera vencida</b><br><span style="color:'+(r.pct_vencida>30?'#dc2626':'green')+'">\$'+(r.cartera_vencida||0).toLocaleString('es-CO')+' ('+(r.pct_vencida||0).toFixed(0)+'%)</span></div>'+
        '<div><b>Compras total</b><br>\$'+(r.compras_total||0).toLocaleString('es-CO')+'</div>'+
        '<div><b>Nº pedidos</b><br>'+(r.num_pedidos||0)+' · Últ: '+(r.ultima_compra||'N/A').slice(0,10)+'</div>'+
      '</div></div>';
  }
  if (p.cotejo && p.cotejo.nota) {
    rsalesBlock += '<div style="margin-top:8px;font-size:12px;padding:8px 12px;border-radius:8px;'+
      (p.cotejo.compras_ok?'background:rgba(21,128,61,.08);color:#15803d':'background:rgba(220,38,38,.08);color:#b91c1c')+
      ';font-weight:600">📊 Cotejo: '+escH(p.cotejo.nota)+'</div>';
  }

  var alertsHtml = (p.alertas||[]).map(function(a){return '<div style="color:#b91c1c;font-weight:600;margin:2px 0">⚠ '+escH(a)+'</div>';}).join('');
  var posHtml = (p.factores_positivos||[]).map(function(f){return '<div style="color:#15803d;margin:2px 0">✓ '+escH(f)+'</div>';}).join('') || '<span style="color:var(--text-faint)">Ninguno</span>';
  var negHtml = (p.factores_negativos||[]).map(function(f){return '<div style="color:#b91c1c;margin:2px 0">✗ '+escH(f)+'</div>';}).join('') || '<span style="color:var(--text-faint)">Ninguno</span>';

  document.getElementById('resultado').style.display = '';
  document.getElementById('resultado').innerHTML =
    '<div style="display:flex;align-items:flex-start;gap:28px;flex-wrap:wrap">'+
      '<div style="text-align:center">'+
        '<div class="score-big" style="color:'+bg+'">'+p.score+'</div>'+
        '<div class="score-label">Score / 1000</div>'+
        '<div class="risk-pill" style="margin-top:10px;background:'+bg+'20;color:'+bg+'">'+p.nivel_riesgo+'</div>'+
      '</div>'+
      '<div style="flex:1;min-width:200px">'+
        '<div style="font-weight:700;font-size:16px;margin-bottom:4px">'+escH(p.recomendacion)+'</div>'+
        '<div style="font-size:13px;color:var(--text-dim)">Monto máximo recomendado: <b>\$'+(p.monto_maximo_recomendado||0).toLocaleString('es-CO')+'</b></div>'+
        alertsHtml +
      '</div>'+
    '</div>'+
    '<div class="stats">'+
      '<div><div class="stat-val" style="color:'+bg+'">'+(p.detalle&&p.detalle.excel?('\$'+(p.detalle.excel.promedio_compras||0).toLocaleString('es-CO')):'—')+'</div><div class="stat-lbl">Promedio compras</div></div>'+
      '<div><div class="stat-val">'+(p.detalle&&p.detalle.excel?(p.detalle.excel.numero_compras||0):'—')+'</div><div class="stat-lbl">Nº compras</div></div>'+
      '<div><div class="stat-val">'+(p.detalle&&p.detalle.excel?(p.detalle.excel.promedio_pago_dias||'—'):'—')+'</div><div class="stat-lbl">Promedio pago (días)</div></div>'+
      '<div><div class="stat-val">'+(p.detalle&&p.detalle.excel&&p.detalle.excel.calificacion_datacredito!=null?p.detalle.excel.calificacion_datacredito:'—')+'</div><div class="stat-lbl">Datacrédito</div></div>'+
    '</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:18px">'+
      '<div><div style="font-weight:700;font-size:12px;margin-bottom:6px;color:#15803d">✓ Factores positivos</div>'+posHtml+'</div>'+
      '<div><div style="font-weight:700;font-size:12px;margin-bottom:6px;color:#b91c1c">✗ Factores negativos</div>'+negHtml+'</div>'+
    '</div>'+
    rsalesBlock;
}

// ── Descargar Excel ─────────────────────────────────────
function descargarExcel() {
  if (!CEDULA_ACTUAL || !RESULTADO_ACTUAL) return;
  var p = RESULTADO_ACTUAL.profile;
  var csv = 'Campo,Valor\\n';
  csv += 'Nombre,'+(p.nombre||'')+'\\n';
  csv += 'Cédula/NIT,'+(p.cedula_nit||'')+'\\n';
  csv += 'Score,'+(p.score||0)+'\\n';
  csv += 'Nivel de riesgo,'+(p.nivel_riesgo||'')+'\\n';
  csv += 'Recomendación,'+(p.recomendacion||'')+'\\n';
  csv += 'Monto máximo recomendado,'+(p.monto_maximo_recomendado||0)+'\\n';
  if (p.detalle && p.detalle.excel) {
    var e = p.detalle.excel;
    csv += 'Crédito actual,'+(e.credito_actual||0)+'\\n';
    csv += 'Monto solicitado,'+(e.monto_solicitado||0)+'\\n';
    csv += 'Cupo inicial,'+(e.cupo_inicial||0)+'\\n';
    csv += 'Promedio compras,'+(e.promedio_compras||0)+'\\n';
    csv += 'Compra mínima,'+(e.compra_minima||0)+'\\n';
    csv += 'Compra máxima,'+(e.compra_maxima||0)+'\\n';
    csv += 'Número compras,'+(e.numero_compras||0)+'\\n';
    csv += 'Promedio pago días,'+(e.promedio_pago_dias||0)+'\\n';
    csv += 'Calificación datacrédito,'+(e.calificacion_datacredito||'')+'\\n';
    csv += 'Mora,'+(e.presenta_mora?'Sí':'No')+'\\n';
    csv += 'Cartera castigada,'+(e.cartera_castigada?'Sí':'No')+'\\n';
  }
  if (p.alertas && p.alertas.length) csv += 'Alertas,"'+p.alertas.join('; ')+'"\\n';
  if (p.factores_negativos && p.factores_negativos.length) csv += 'Factores negativos,"'+p.factores_negativos.join('; ')+'"\\n';
  var blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  var a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'perfil_crediticio_'+(p.cedula_nit||'cliente')+'.csv';
  a.click();
}

function limpiarForm() {
  document.getElementById('credito-form').reset();
  document.getElementById('resultado').style.display = 'none';
  document.getElementById('btn-descargar').style.display = 'none';
  document.getElementById('rsales-status').style.display = 'none';
  CEDULA_ACTUAL = ''; RSALES_DATA = null; RESULTADO_ACTUAL = null;
}
</script>
""" + ui_theme.SHELL_CLOSE


@app.route("/credito")
def credito_page():
    """Página de perfil crediticio con formulario y evaluación."""
    import json as _json
    import random

    subjects = []

    # Intentar cargar del Excel
    try:
        from excel_reader import read_all
        data = read_all()
        for c in data["clientes"]:
            ano_exp = random.randint(2005, 2020)
            mes_exp = random.randint(1, 12)
            dia_exp = random.randint(1, 28)
            fecha_exp = f"{ano_exp}-{mes_exp:02d}-{dia_exp:02d}"
            subjects.append({
                "cedula_nit": c.get("cedula_nit", ""),
                "nombre": c.get("nombre_cliente", ""),
                "tipo_solicitud": c.get("tipo_solicitud", ""),
                "credito_actual": c.get("credito_actual"),
                "monto_solicitar": c.get("monto_solicitar"),
                "cupo_inicial": c.get("cupo_inicial"),
                "promedio_compras": c.get("promedio_compras"),
                "compra_minima": c.get("compra_minima"),
                "compra_maxima": c.get("compra_maxima"),
                "numero_compras": c.get("numero_compras"),
                "ano_dato_compras": c.get("ano_dato_compras"),
                "promedio_pago_dias": c.get("promedio_pago_dias"),
                "calificacion_datacredito": c.get("calificacion_datacredito"),
                "consultas_6m_sector_real": str(c.get("consultas_6m_sector_real", "")),
                "presenta_mora": c.get("presenta_mora") is True,
                "presenta_cartera_castigada": c.get("presenta_cartera_castigada") is True,
                "aprobacion": c.get("aprobacion") is True,
                "credito_aprobado": c.get("credito_aprobado") or c.get("monto_credito_aprobado"),
                "observaciones": c.get("observaciones", ""),
                "ejecutivo": c.get("ejecutivo", ""),
                "mora": c.get("presenta_mora") is True,
                "castigada": c.get("presenta_cartera_castigada") is True,
                "fecha_expedicion": fecha_exp,
            })
    except Exception:
        pass

    # Fallback: cargar desde static/seed_subjects.json (65 sujetos, commiteado para Vercel)
    if not subjects:
        try:
            seed_path = Path(__file__).parent / "static" / "seed_subjects.json"
            if seed_path.exists():
                subjects = _json.loads(seed_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fallback mínimo: 3 sujetos si todo falla
    if not subjects:
        subjects = [
            {"cedula_nit":"8028884","nombre":"ARISTIZABAL DUQUE DIOMER ALEXIS","tipo_solicitud":"SOLICITUD DE CREDITO","credito_actual":7000000,"monto_solicitar":5000000,"cupo_inicial":8400000,"promedio_compras":8656929.5,"compra_minima":2597079,"compra_maxima":21642324,"numero_compras":13,"ano_dato_compras":2026,"promedio_pago_dias":30,"calificacion_datacredito":720,"consultas_6m_sector_real":"3","presenta_mora":False,"presenta_cartera_castigada":False,"aprobacion":True,"credito_aprobado":7000000,"observaciones":"Cliente recurrente, buen comportamiento de pago","ejecutivo":"MEDELLIN NORTE","mora":False,"castigada":False,"fecha_expedicion":"2012-05-15"},
            {"cedula_nit":"63547197","nombre":"PEREZ HENAO EIDY YULIMA","tipo_solicitud":"TRASLADO DE CUPO","credito_actual":4000000,"monto_solicitar":5000000,"cupo_inicial":5000000,"promedio_compras":5860276,"compra_minima":1200000,"compra_maxima":9800000,"numero_compras":8,"ano_dato_compras":2026,"promedio_pago_dias":45,"calificacion_datacredito":680,"consultas_6m_sector_real":"5","presenta_mora":False,"presenta_cartera_castigada":False,"aprobacion":True,"credito_aprobado":4000000,"observaciones":"Traslado de cupo aprobado","ejecutivo":"SANTANDER","mora":False,"castigada":False,"fecha_expedicion":"2008-11-20"},
            {"cedula_nit":"1028023710","nombre":"HERNANDEZ CARDENAS JUAN FELIPE","tipo_solicitud":"SOLICITUD DE CREDITO","credito_actual":3000000,"monto_solicitar":4000000,"cupo_inicial":4000000,"promedio_compras":4461902,"compra_minima":900000,"compra_maxima":7692104,"numero_compras":6,"ano_dato_compras":2026,"promedio_pago_dias":30,"calificacion_datacredito":750,"consultas_6m_sector_real":"2","presenta_mora":False,"presenta_cartera_castigada":False,"aprobacion":False,"credito_aprobado":3000000,"observaciones":"En espera de facturas legales para continuar","ejecutivo":"TRANSFERENCISTA URABA","mora":False,"castigada":False,"fecha_expedicion":"2018-03-10"},
        ]

    return render_template_string(
        CREDITO_TEMPLATE,
        subjects_json=_json.dumps(subjects, ensure_ascii=False),
    )


@app.route("/api/credit/warm-rsales")
def api_credit_warm_rsales():
    """Pre-calienta el cache de clientes RSales."""
    try:
        from rsales_client import _get_customer_index
        idx = _get_customer_index()
        return {"ok": True, "cached": len(idx)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD JEFE CARTERA — Historial y Aprobaciones
# ═══════════════════════════════════════════════════════════════════
@app.route("/cartera")
def cartera_page():
    """Dashboard de cartera para Jefe Cartera: solicitudes, historial, aprobaciones."""
    from flask import jsonify
    from db import credit_request_get_all
    solicitudes = credit_request_get_all()
    return render_template_string(CARTERA_TEMPLATE, solicitudes_json=json.dumps(solicitudes, ensure_ascii=False, default=str))


CARTERA_TEMPLATE = ui_theme.head_open("VerifyData — Cartera") + \
    ui_theme.shell_open("cartera", "Cartera", "Gestión de solicitudes") + """
<style>{% raw %}
  .car-table{width:100%;border-collapse:collapse;font-size:13px}
  .car-table th{background:#2d3748;color:#fff;padding:10px 12px;text-align:left;font-weight:600}
  .car-table td{padding:10px 12px;border-bottom:1px solid var(--line)}
  .car-table tr:hover{background:rgba(105,65,244,.04)}
  .pill{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
  .pill-pendiente{background:#fef3c7;color:#92400e}
  .pill-aprobado{background:#d1fae5;color:#065f46}
  .pill-rechazado{background:#fee2e2;color:#991b1b}
  .btn-sm{padding:6px 14px;font-size:12px;border-radius:6px;border:none;cursor:pointer;font-weight:600}
  .btn-aprobar{background:#15803d;color:#fff}
  .btn-rechazar{background:#dc2626;color:#fff}
  .modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:100;display:flex;align-items:center;justify-content:center}
  .modal{background:#fff;border-radius:12px;padding:24px;max-width:400px;width:90%}
{% endraw %}</style>
<div class="main-content">
  <div class="hero-row" style="margin-bottom:16px">
    <div class="menu-hero">
      <p class="eyebrow">Gestión de cartera</p>
      <h2>Solicitudes de crédito</h2>
      <p>Revisa, aprueba o rechaza solicitudes de clientes.</p>
    </div>
  </div>

  <div class="card pad">
    <table class="car-table">
      <thead>
        <tr>
          <th>ID</th><th>Cliente</th><th>CC</th><th>Tipo</th>
          <th>Monto</th><th>Score</th><th>Estado</th><th>Fecha</th><th>Acciones</th>
        </tr>
      </thead>
      <tbody id="car-body"></tbody>
    </table>
  </div>
</div>
<script>
var SOLICITUDES = {{ solicitudes_json|safe }};
function escH(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function renderCartera(){
  var html='';
  if(!SOLICITUDES.length){html='<tr><td colspan="9" style="text-align:center;padding:20px;color:#999">No hay solicitudes</td></tr>';}
  SOLICITUDES.forEach(function(s){
    var pillClass='pill-pendiente';
    if(s.estado==='aprobado')pillClass='pill-aprobado';
    if(s.estado==='rechazado')pillClass='pill-rechazado';
    var accionBtns='';
    if(s.estado==='pendiente'){
      accionBtns='<button class="btn-sm btn-aprobar" onclick="aprobar('+s.id+')">Aprobar</button> '+
                 '<button class="btn-sm btn-rechazar" onclick="rechazar('+s.id+')">Rechazar</button>';
    }
    html+='<tr>'+
      '<td>'+s.id+'</td>'+
      '<td>'+escH(s.nombre)+'</td>'+
      '<td>'+escH(s.cedula)+'</td>'+
      '<td>'+escH(s.tipo_solicitud)+'</td>'+
      '<td>$'+Number(s.monto_solicitado||0).toLocaleString('es-CO')+'</td>'+
      '<td>'+s.score+'</td>'+
      '<td><span class="pill '+pillClass+'">'+escH(s.estado)+'</span></td>'+
      '<td>'+(s.created_at||'').slice(0,10)+'</td>'+
      '<td>'+accionBtns+'</td>'+
    '</tr>';
  });
  document.getElementById('car-body').innerHTML=html;
}
function aprobar(id){
  if(!confirm('¿Aprobar solicitud #'+id+'?'))return;
  fetch('/api/credit/approve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,ejecutivo:'{{ current_user.nombre if current_user else "admin" }}'})
  }).then(function(r){return r.json();}).then(function(d){
    if(d.ok){location.reload();}else{alert(d.error);}
  });
}
function rechazar(id){
  var motivo=prompt('Motivo del rechazo:');
  if(motivo===null)return;
  fetch('/api/credit/reject',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,ejecutivo:'{{ current_user.nombre if current_user else "admin" }}',motivo:motivo})
  }).then(function(r){return r.json();}).then(function(d){
    if(d.ok){location.reload();}else{alert(d.error);}
  });
}
renderCartera();
</script>
""" + ui_theme.SHELL_CLOSE


@app.route("/api/credit/approve", methods=["POST"])
def api_credit_approve():
    """Aprueba una solicitud de crédito."""
    from flask import jsonify, request, g
    data = request.get_json(silent=True) or {}
    request_id = data.get("id")
    ejecutivo = data.get("ejecutivo", "")
    if not request_id:
        return jsonify({"ok": False, "error": "ID requerido"}), 400
    try:
        from db import credit_request_approve
        credit_request_approve(int(request_id), ejecutivo)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/reject", methods=["POST"])
def api_credit_reject():
    """Rechaza una solicitud de crédito."""
    from flask import jsonify, request
    data = request.get_json(silent=True) or {}
    request_id = data.get("id")
    ejecutivo = data.get("ejecutivo", "")
    motivo = data.get("motivo", "Sin motivo")
    if not request_id:
        return jsonify({"ok": False, "error": "ID requerido"}), 400
    try:
        from db import credit_request_reject
        credit_request_reject(int(request_id), ejecutivo, motivo)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/history/<int:request_id>")
def api_credit_history(request_id):
    """Historial de aprobaciones de una solicitud."""
    from flask import jsonify
    from db import approval_history_get
    history = approval_history_get(request_id)
    return jsonify({"ok": True, "history": history})


# ═══════════════════════════════════════════════════════════════════
#  GOOGLE SHEETS SYNC
# ═══════════════════════════════════════════════════════════════════
try:
    from sheets_sync import register_sheets_routes
    register_sheets_routes(app)
except Exception:
    pass  # Google Sheets no configurado


@app.route("/api/credit/evaluate", methods=["POST"])
def api_credit_evaluate():
    """Evalúa el riesgo crediticio a partir de los datos del formulario.

    Recibe JSON con todos los campos del formulario.
    SIEMPRE intenta cargar datos de RSales automáticamente.
    """
    from flask import jsonify
    from credit_risk import build_credit_profile

    data = request.get_json(silent=True) or {}
    cedula = data.get("cedula") or data.get("cedula_nit", "")
    if not cedula:
        return jsonify({"ok": False, "error": "Cédula/NIT requerido"}), 400

    def _float(v, default=0):
        if v is None or v == "" or v == "None": return default
        try: return float(v)
        except (ValueError, TypeError): return default
    def _int(v, default=0):
        if v is None or v == "" or v == "None": return default
        try: return int(float(v))
        except (ValueError, TypeError): return default

    # Construir datos tipo Excel desde el formulario
    excel_data = {
        "nombre_cliente": data.get("nombre", ""),
        "cedula_nit": cedula,
        "tipo_solicitud": data.get("tipo_solicitud", ""),
        "credito_actual": _float(data.get("credito_actual")),
        "monto_solicitar": _float(data.get("monto_solicitar")),
        "cupo_inicial": _float(data.get("cupo_inicial")),
        "promedio_compras": _float(data.get("promedio_compras")),
        "compra_minima": _float(data.get("compra_minima")),
        "compra_maxima": _float(data.get("compra_maxima")),
        "numero_compras": _int(data.get("numero_compras")),
        "ano_dato_compras": _int(data.get("ano_dato_compras"), 2026),
        "promedio_pago_dias": _float(data.get("promedio_pago_dias")),
        "calificacion_datacredito": _float(data.get("calificacion_datacredito")),
        "consultas_6m_sector_real": data.get("consultas_6m", ""),
        "presenta_mora": data.get("presenta_mora", False),
        "presenta_cartera_castigada": data.get("presenta_cartera_castigada", False),
        "aprobacion": data.get("aprobacion", False),
        "observaciones": data.get("observaciones", ""),
    }

    # RSales: intentar siempre, sin errores si no está disponible
    rsales_profile = None
    try:
        from rsales_client import find_customer_in_rsales, get_rsales_client
        cust = find_customer_in_rsales(cedula)
        if cust:
            rsales = get_rsales_client()
            rsales_profile = rsales.get_customer_financial_profile(cust["code"])
    except Exception as e:
        log.info("RSales no disponible para %s: %s", cedula, e)

    profile = build_credit_profile(
        cedula_nit=cedula,
        nombre=data.get("nombre", ""),
        rsales_profile=rsales_profile,
        excel_data=excel_data,
        docs=data.get("docs"),
    )

    return jsonify({
        "ok": True,
        "profile": {
            "cedula_nit": profile.cedula_nit,
            "nombre": profile.nombre,
            "score": profile.score,
            "nivel_riesgo": profile.nivel_riesgo,
            "recomendacion": profile.recomendacion,
            "monto_maximo_recomendado": profile.monto_maximo_recomendado,
            "alertas": profile.alertas,
            "factores_positivos": profile.factores_positivos,
            "factores_negativos": profile.factores_negativos,
            "fuentes": {
                "rsales": profile.rsales_disponible,
                "excel": profile.excel_disponible,
            },
            "docs": {
                "cedula_frontal": profile.docs_cedula_frontal,
                "cedula_posterior": profile.docs_cedula_posterior,
                "rut": profile.docs_rut,
                "camara_comercio": profile.docs_camara_comercio,
                "estados_financieros": profile.docs_estados_financieros,
                "declaracion_renta": profile.docs_declaracion_renta,
            },
            "detalle": {
                "rsales": {
                    "cartera_total": profile.rsales_cartera_total,
                    "cartera_vencida": profile.rsales_cartera_vencida,
                    "pct_vencida": profile.rsales_pct_vencida,
                    "dias_mora_max": profile.rsales_dias_mora_max,
                    "compras_total": profile.rsales_compras_total,
                    "num_pedidos": profile.rsales_num_pedidos,
                    "ultima_compra": profile.rsales_ultima_compra_fecha,
                } if profile.rsales_disponible else None,
                "excel": {
                    "promedio_compras": profile.excel_promedio_compras,
                    "compra_minima": profile.excel_compra_minima,
                    "compra_maxima": profile.excel_compra_maxima,
                    "numero_compras": profile.excel_numero_compras,
                    "promedio_pago_dias": profile.excel_promedio_pago_dias,
                    "calificacion_datacredito": profile.excel_calificacion_datacredito,
                    "credito_actual": profile.excel_credito_actual,
                    "monto_solicitado": profile.excel_monto_solicitado,
                    "cupo_inicial": profile.excel_cupo_inicial,
                    "presenta_mora": profile.excel_presenta_mora,
                    "cartera_castigada": profile.excel_cartera_castigada,
                },
            },
            "cotejo": {
                "compras_ok": profile.cotejo_compras_ok,
                "compras_diff_pct": profile.cotejo_compras_diff_pct,
                "nota": profile.cotejo_nota,
            } if profile.rsales_disponible and profile.excel_disponible and profile.cotejo_nota else None,
        },
    })


# ==============================================================
#  API de Perfil Crediticio — VerifyData Credit Risk
# ==============================================================
# Combina RSALES API + Excel BITACORA para evaluar riesgo crediticio.
# Endpoints:
#   GET  /api/credit/profile/<cedula>       — Perfil crediticio completo
#   GET  /api/credit/check/<cedula>          — Evaluación rápida (score + recomendación)
#   POST /api/credit/cross-reference          — Cotejo masivo Excel vs RSales
#   GET  /api/credit/rsales/<client_code>     — Datos crudos de RSales
#   GET  /api/credit/excel-clientes           — Lista de clientes del Excel
# ==============================================================

@app.route("/api/credit/profile/<cedula>")
def api_credit_profile(cedula: str):
    """Perfil crediticio completo: RSales + Excel + score + alertas.

    Parámetros:
      ?rsales=1    — Incluir datos de RSales (más lento, ~2-3s extra)
      ?nombre=X    — Nombre del cliente
    """
    from flask import jsonify, request
    from credit_risk import build_credit_profile
    from excel_reader import get_client_by_cedula
    from rsales_client import find_customer_in_rsales, get_rsales_client

    nombre = request.args.get("nombre", "")
    fetch_rsales = request.args.get("rsales") == "1"

    try:
        excel_data = get_client_by_cedula(cedula)

        rsales_profile = None
        if fetch_rsales:
            try:
                cust = find_customer_in_rsales(cedula)
                if cust:
                    rsales = get_rsales_client()
                    rsales_profile = rsales.get_customer_financial_profile(
                        cust["code"]
                    )
            except Exception as e:
                log.warning("RSales no disponible para %s: %s", cedula, e)

        profile = build_credit_profile(
            cedula_nit=cedula,
            nombre=nombre,
            rsales_profile=rsales_profile,
            excel_data=excel_data,
        )

        return jsonify({
            "ok": True,
            "profile": {
                "cedula_nit": profile.cedula_nit,
                "nombre": profile.nombre,
                "tipo_persona": profile.tipo_persona,
                "score": profile.score,
                "nivel_riesgo": profile.nivel_riesgo,
                "recomendacion": profile.recomendacion,
                "monto_maximo_recomendado": profile.monto_maximo_recomendado,
                "alertas": profile.alertas,
                "factores_positivos": profile.factores_positivos,
                "factores_negativos": profile.factores_negativos,
                "fuentes": {
                    "rsales": profile.rsales_disponible,
                    "excel": profile.excel_disponible,
                },
                "cotejo": {
                    "compras_ok": profile.cotejo_compras_ok,
                    "compras_diff_pct": profile.cotejo_compras_diff_pct,
                    "nota": profile.cotejo_nota,
                } if profile.rsales_disponible and profile.excel_disponible else None,
                "detalle": {
                    "rsales": {
                        "cartera_total": profile.rsales_cartera_total,
                        "cartera_vencida": profile.rsales_cartera_vencida,
                        "pct_vencida": profile.rsales_pct_vencida,
                        "dias_mora_max": profile.rsales_dias_mora_max,
                        "compras_total": profile.rsales_compras_total,
                        "num_pedidos": profile.rsales_num_pedidos,
                        "promedio_pedido": profile.rsales_promedio_pedido,
                        "ultima_compra": profile.rsales_ultima_compra_fecha,
                        "frecuencia_meses": profile.rsales_frecuencia_meses,
                    } if profile.rsales_disponible else None,
                    "excel": {
                        "credito_actual": profile.excel_credito_actual,
                        "monto_solicitado": profile.excel_monto_solicitado,
                        "cupo_inicial": profile.excel_cupo_inicial,
                        "credito_aprobado": profile.excel_credito_aprobado,
                        "promedio_compras": profile.excel_promedio_compras,
                        "compra_minima": profile.excel_compra_minima,
                        "compra_maxima": profile.excel_compra_maxima,
                        "numero_compras": profile.excel_numero_compras,
                        "promedio_pago_dias": profile.excel_promedio_pago_dias,
                        "calificacion_datacredito": profile.excel_calificacion_datacredito,
                        "presenta_mora": profile.excel_presenta_mora,
                        "cartera_castigada": profile.excel_cartera_castigada,
                        "aprobacion": profile.excel_aprobacion,
                    } if profile.excel_disponible else None,
                },
            },
            "timestamp": profile.timestamp,
        })
    except Exception as e:
        log.exception("Error en credit_profile para %s", cedula)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/check/<cedula>")
def api_credit_check(cedula: str):
    """Evaluación rápida: solo score, nivel de riesgo y recomendación.

    Parámetros:
      ?rsales=1    — Incluir datos de RSales (más lento, ~2-3s extra)
      ?nombre=X    — Nombre del cliente
    Por defecto solo usa datos del Excel BITACORA (respuesta instantánea).
    """
    from flask import jsonify, request
    from credit_risk import build_credit_profile
    from excel_reader import get_client_by_cedula
    from rsales_client import find_customer_in_rsales, get_rsales_client

    nombre = request.args.get("nombre", "")
    fetch_rsales = request.args.get("rsales") == "1"

    try:
        excel_data = get_client_by_cedula(cedula)

        rsales_profile = None
        rsales_available = False
        if fetch_rsales:
            try:
                cust = find_customer_in_rsales(cedula)
                if cust:
                    rsales = get_rsales_client()
                    rsales_profile = rsales.get_customer_financial_profile(
                        cust["code"]
                    )
                    rsales_available = True
            except Exception as e:
                log.warning("RSales no disponible: %s", e)

        profile = build_credit_profile(
            cedula_nit=cedula,
            nombre=nombre,
            rsales_profile=rsales_profile,
            excel_data=excel_data,
        )

        return jsonify({
            "ok": True,
            "cedula_nit": cedula,
            "nombre": profile.nombre,
            "score": profile.score,
            "nivel_riesgo": profile.nivel_riesgo,
            "recomendacion": profile.recomendacion,
            "monto_maximo": profile.monto_maximo_recomendado,
            "alertas": profile.alertas,
            "factores_positivos": profile.factores_positivos,
            "factores_negativos": profile.factores_negativos,
            "fuentes": {
                "rsales": profile.rsales_disponible,
                "excel": profile.excel_disponible,
            },
        })
    except Exception as e:
        log.exception("Error en credit_check para %s", cedula)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/cross-reference", methods=["GET", "POST"])
def api_credit_cross_reference():
    """Cotejo masivo: todos los clientes del Excel vs RSales.

    GET: devuelve resultados cacheados (si existen).
    POST: fuerza re-ejecución y devuelve resumen.
    """
    from flask import jsonify
    from credit_risk import cross_reference_all_excel_with_rsales

    force = request.method == "POST" or request.args.get("force") == "1"

    try:
        result = cross_reference_all_excel_with_rsales()

        return jsonify({
            "ok": True,
            "resumen": {
                "total_excel": result["total_excel"],
                "total_rsales": result["total_rsales"],
                "encontrados_en_rsales": result["encontrados_en_rsales"],
                "no_encontrados": result["no_encontrados_en_rsales"],
                "pct_cobertura": result["pct_cobertura"],
                "discrepancias": result["discrepancias"],
            },
            "encontrados": result["matched"][:100],
            "no_encontrados": result["not_found"][:50],
            "discrepancias_detalle": result["discrepancies_detail"],
            "timestamp": result["timestamp"],
        })
    except Exception as e:
        log.exception("Error en cross-reference")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/rsales/<client_code>")
def api_credit_rsales_profile(client_code: str):
    """Perfil crudo desde RSales para un cliente (código de cliente)."""
    from flask import jsonify
    from rsales_client import get_rsales_client

    try:
        rsales = get_rsales_client()
        profile = rsales.get_customer_financial_profile(client_code)
        return jsonify({"ok": True, "profile": profile})
    except Exception as e:
        log.exception("Error en rsales_profile para %s", client_code)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/credit/excel-clientes")
def api_credit_excel_clientes():
    """Lista todos los clientes del Excel BITACORA con datos relevantes."""
    from flask import jsonify
    from excel_reader import read_all

    try:
        data = read_all()
        clientes_resumen = []
        for c in data["clientes"]:
            clientes_resumen.append({
                "cedula_nit": c.get("cedula_nit"),
                "nombre": c.get("nombre_cliente"),
                "tipo_solicitud": c.get("tipo_solicitud"),
                "credito_actual": c.get("credito_actual"),
                "monto_solicitado": c.get("monto_solicitar"),
                "cupo_inicial": c.get("cupo_inicial"),
                "credito_aprobado": c.get("credito_aprobado") or c.get(
                    "monto_credito_aprobado"
                ),
                "promedio_compras": c.get("promedio_compras"),
                "numero_compras": c.get("numero_compras"),
                "promedio_pago_dias": c.get("promedio_pago_dias"),
                "calificacion_datacredito": c.get("calificacion_datacredito"),
                "presenta_mora": c.get("presenta_mora"),
                "cartera_castigada": c.get("presenta_cartera_castigada"),
                "aprobacion": c.get("aprobacion"),
                "ejecutivo": c.get("ejecutivo"),
                "observaciones": c.get("observaciones"),
            })

        return jsonify({
            "ok": True,
            "total": len(clientes_resumen),
            "clientes": clientes_resumen,
        })
    except Exception as e:
        log.exception("Error leyendo Excel")
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
#  Almacenamiento temporal de resultados por token
# ═══════════════════════════════════════════════════════════════════
_CREDIT_RESULTS: dict[str, dict] = {}
import secrets as _secrets

def _store_credit_result(result: dict) -> str:
    token = _secrets.token_urlsafe(16)
    # Guardar en memoria (para冷 start local)
    _CREDIT_RESULTS[token] = result
    # Guardar en SQLite (para Vercel cold starts)
    try:
        from db import credit_result_save
        credit_result_save(token, result)
    except Exception:
        pass
    # Limpiar memoria: max 100 resultados
    while len(_CREDIT_RESULTS) > 100:
        _CREDIT_RESULTS.pop(next(iter(_CREDIT_RESULTS)))
    return token

def _get_credit_result(token: str) -> dict | None:
    """Busca resultado en memoria primero, luego en SQLite."""
    # 1. Intentar en memoria
    result = _CREDIT_RESULTS.get(token)
    if result:
        return result
    # 2. Intentar en SQLite (Vercel cold start)
    try:
        from db import credit_result_get
        result = credit_result_get(token)
        if result:
            _CREDIT_RESULTS[token] = result  # Cache en memoria
            return result
    except Exception:
        pass
    return None

# ==============================================================
#  CHECK INTEGRAL — Crédito + Antecedentes + OFAC + Judicial
# ==============================================================
@app.route("/api/credit/full-check", methods=["POST"])
def api_credit_full_check():
    """Check integral: perfil crediticio + antecedentes judiciales + OFAC.

    Recibe cédula y datos básicos. Ejecuta en paralelo:
      1. RSales (cartera, compras, frecuencia)
      2. Búsqueda pública (OFAC, Policía, Registraduría, etc.)
      3. Score crediticio
    Retorna todo junto para la demo.
    """
    from flask import jsonify
    from credit_risk import build_credit_profile

    data = request.get_json(silent=True) or {}
    cedula = data.get("cedula") or data.get("cedula_nit", "")
    nombre = data.get("nombre", "")
    if not cedula:
        return jsonify({"ok": False, "error": "Cédula/NIT requerido"}), 400

    results = {"cedula_nit": cedula, "nombre": nombre}

    # Helper: convertir string a float seguro
    def _float(v, default=0):
        if v is None or v == "" or v == "None":
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _int(v, default=0):
        if v is None or v == "" or v == "None":
            return default
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return default

    # ── 1. Perfil crediticio (RSales + Excel) ──
    excel_data = {
        "nombre_cliente": nombre,
        "cedula_nit": cedula,
        "tipo_solicitud": data.get("tipo_solicitud", ""),
        "credito_actual": _float(data.get("credito_actual")),
        "monto_solicitar": _float(data.get("monto_solicitar")),
        "cupo_inicial": _float(data.get("cupo_inicial")),
        "ingreso_mensual": _float(data.get("ingreso_mensual")),
        "fuente_ingreso": data.get("fuente_ingreso", ""),
        "actividad_economica": data.get("actividad_economica", ""),
        "promedio_compras": _float(data.get("promedio_compras")),
        "compra_minima": _float(data.get("compra_minima")),
        "compra_maxima": _float(data.get("compra_maxima")),
        "numero_compras": _int(data.get("numero_compras")),
        "ano_dato_compras": _int(data.get("ano_dato_compras"), 2026),
        "promedio_pago_dias": _float(data.get("promedio_pago_dias")),
        "calificacion_datacredito": _float(data.get("calificacion_datacredito")),
        "consultas_6m_sector_real": data.get("consultas_6m", ""),
        "presenta_mora": data.get("presenta_mora", False),
        "presenta_cartera_castigada": data.get("presenta_cartera_castigada", False),
        "aprobacion": data.get("aprobacion", False),
        "observaciones": data.get("observaciones", ""),
        "fecha_expedicion": data.get("fecha_expedicion", ""),
        "patrimonio": _float(data.get("patrimonio")),
        "endeudamiento": _float(data.get("endeudamiento")),
    }

    # Auto-lookup: si el formulario no trae datos financieros, buscar en Excel
    try:
        from excel_reader import get_client_by_cedula
        excel_actual = get_client_by_cedula(cedula)
        if excel_actual:
            for k, v in excel_actual.items():
                if excel_data.get(k) in (None, "", 0, False):
                    excel_data[k] = v
    except Exception:
        pass

    rsales_profile = None
    try:
        from rsales_client import find_customer_in_rsales, get_rsales_client
        cust = find_customer_in_rsales(cedula)
        if cust:
            rsales = get_rsales_client()
            rsales_profile = rsales.get_customer_financial_profile(cust["code"])
    except Exception as e:
        log.info("RSales no disponible para %s: %s", cedula, e)

    profile = build_credit_profile(
        cedula_nit=cedula,
        nombre=nombre,
        rsales_profile=rsales_profile,
        excel_data=excel_data,
        docs=data.get("docs"),
    )

    results["perfil_crediticio"] = {
        "score": profile.score,
        "nivel_riesgo": profile.nivel_riesgo,
        "recomendacion": profile.recomendacion,
        "monto_maximo_recomendado": profile.monto_maximo_recomendado,
        "alertas": profile.alertas,
        "factores_positivos": profile.factores_positivos,
        "factores_negativos": profile.factores_negativos,
        "rsales": {
            "cartera_total": profile.rsales_cartera_total,
            "cartera_vencida": profile.rsales_cartera_vencida,
            "pct_vencida": profile.rsales_pct_vencida,
            "dias_mora_max": profile.rsales_dias_mora_max,
            "compras_total": profile.rsales_compras_total,
            "num_pedidos": profile.rsales_num_pedidos,
            "ultima_compra": profile.rsales_ultima_compra_fecha,
        } if profile.rsales_disponible else None,
        "docs": {
            "cedula_frontal": profile.docs_cedula_frontal,
            "cedula_posterior": profile.docs_cedula_posterior,
            "rut": profile.docs_rut,
            "camara_comercio": profile.docs_camara_comercio,
            "estados_financieros": profile.docs_estados_financieros,
            "declaracion_renta": profile.docs_declaracion_renta,
        },
    }

    # ── 2. Búsquedas públicas (antecedentes, OFAC, judicial) ──
    # Solo fuentes RÁPIDAS (bulk list / API pública) — EN PARALELO
    antecedentes = {}
    fuentes_clave = [
        ("OFAC SDN", "OFAC SDN — Specially Designated Nationals"),
        ("OFAC Consolidado", "OFAC — Lista Consolidada (Non-SDN, FSE, SSI, CAPTA)"),
        ("ONU Sanciones", "ONU — UN Security Council Consolidated List"),
        ("BIS Denied", "BIS — Denied Persons List (USA)"),
        ("Banco Mundial", "Banco Mundial — Debarred Firms & Individuals"),
        ("PEP Colombia", "PEP Colombia — Consulta agregada"),
        ("SECOP Multas", "SECOP II — Multas y Sanciones"),
        ("SECOP Contratos", "SECOP II — Contratos Electr\u00f3nicos"),
    ]

    try:
        from sources import registry
        from sources.base import safe_fetch
        from solvers import get_default_solver
        from concurrent.futures import ThreadPoolExecutor, as_completed
        solver = get_default_solver()
        all_sources = registry.all_sources()

        # Indexar fuentes por nombre
        src_map = {}
        for s in all_sources:
            src_map[s.name] = s

        def _fetch_one(label, src_name):
            src = src_map.get(src_name)
            if not src:
                return label, {"matched": False, "error": "fuente_no_disponible"}
            try:
                h = safe_fetch(src, nombre, cedula, None, solver)
                return label, {
                    "matched": h.matched,
                    "summary": (h.summary or "")[:200],
                    "error": h.error or None,
                    "elapsed_s": round(h.elapsed_s, 1),
                }
            except Exception as e:
                return label, {"matched": False, "error": str(e)[:200]}

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one, lbl, sn): lbl
                for lbl, sn in fuentes_clave
            }
            for f in as_completed(futures):
                label, result = f.result()
                antecedentes[label] = result

    except Exception as e:
        log.warning("Búsquedas antecedentes fallaron: %s", e)

    results["antecedentes"] = antecedentes

    # ── 3. Resumen ejecutivo ──
    # Reconstruir bloqueantes con nombres correctos
    bloqueantes = []
    if antecedentes.get("OFAC SDN", {}).get("matched", False):
        bloqueantes.append("⚠️ LISTA OFAC SDN — Persona en lista de sanciones de EE.UU.")
    if antecedentes.get("OFAC Consolidado", {}).get("matched", False):
        bloqueantes.append("⚠️ OFAC CONSOLIDADO — En lista consolidada de sanciones")
    if antecedentes.get("ONU Sanciones", {}).get("matched", False):
        bloqueantes.append("⚠️ ONU — En lista consolidada de sanciones de Naciones Unidas")
    if antecedentes.get("BIS Denied", {}).get("matched", False):
        bloqueantes.append("⚠️ BIS — En lista de personas negadas (Denied Persons List)")
    if antecedentes.get("Banco Mundial", {}).get("matched", False):
        bloqueantes.append("⚠️ BANCO MUNDIAL — Firma inhabilitada")
    if antecedentes.get("PEP Colombia", {}).get("matched", False):
        bloqueantes.append("⚠️ PEP — Persona Expuesta Políticamente (requiere debida diligencia)")
    if antecedentes.get("SECOP Multas", {}).get("matched", False):
        bloqueantes.append("⚠️ SECOP MULTAS — Con multas en contratación pública")

    tiene_bloqueantes = bool(bloqueantes)
    tiene_pep = antecedentes.get("PEP Colombia", {}).get("matched", False)

    results["resumen_ejecutivo"] = {
        "aprobado": profile.score >= 500 and not tiene_bloqueantes,
        "bloqueantes": bloqueantes,
        "tiene_antecedentes": any(v.get("matched", False) for v in antecedentes.values()),
        "tiene_pep": tiene_pep,
        "score_crediticio": profile.score,
        "nivel_riesgo": profile.nivel_riesgo,
        "recomendacion": profile.recomendacion,
        "monto_maximo": profile.monto_maximo_recomendado,
        "monto_justificacion": _justificar_monto(profile, excel_data),
    }

    # Agregar campos financieros al resultado
    results["credito_actual"] = excel_data.get("credito_actual", 0)
    results["monto_solicitar"] = excel_data.get("monto_solicitar", 0)
    results["cupo_inicial"] = excel_data.get("cupo_inicial", 0)
    results["ingreso_mensual"] = excel_data.get("ingreso_mensual", 0)
    results["fuente_ingreso"] = excel_data.get("fuente_ingreso", "")
    results["actividad_economica"] = excel_data.get("actividad_economica", "")
    results["promedio_compras"] = excel_data.get("promedio_compras", 0)
    results["compra_minima"] = excel_data.get("compra_minima", 0)
    results["compra_maxima"] = excel_data.get("compra_maxima", 0)
    results["numero_compras"] = excel_data.get("numero_compras", 0)
    results["promedio_pago_dias"] = excel_data.get("promedio_pago_dias", 0)
    results["patrimonio"] = excel_data.get("patrimonio", 0)
    results["endeudamiento"] = excel_data.get("endeudamiento", 0)
    results["tipo_solicitud"] = excel_data.get("tipo_solicitud", "")
    results["fecha_solicitud"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")

    result_token = _store_credit_result(results)

    return jsonify({"ok": True, "result": results, "result_token": result_token})


def _justificar_monto(profile, excel_data):
    """Genera justificación del monto máximo recomendado."""
    ventas = 0
    fuente = ""
    if profile.rsales_disponible and profile.rsales_compras_total > 0:
        ventas = profile.rsales_compras_total
        fuente = "RSales (compras históricas)"
    elif profile.excel_disponible:
        promedio = float(excel_data.get("promedio_compras") or 0)
        num = int(float(excel_data.get("numero_compras") or 0))
        ventas = promedio * num
        fuente = f"Excel (promedio ${promedio:,.0f} × {num} compras)"

    if ventas <= 0:
        return "Sin datos de ventas suficientes para calcular monto."

    ratio = 0.30
    base = ventas * ratio
    multiplicador = {"BAJO": 1.0, "MEDIO": 0.6, "ALTO": 0.3, "CRITICO": 0}.get(profile.nivel_riesgo, 0)
    monto = round(base * multiplicador, -3)

    return (
        f"Ventas anuales estimadas: ${ventas:,.0f} (fuente: {fuente}). "
        f"Se aplica el 30% como capacidad de pago (${base:,.0f}). "
        f"Multiplicador por riesgo {profile.nivel_riesgo}: ×{multiplicador}. "
        f"Resultado: ${monto:,.0f}."
    )


# ═══════════════════════════════════════════════════════════════════
#  ENVIAR RESULTADOS POR CORREO
# ═══════════════════════════════════════════════════════════════════
DEFAULT_RECIPIENTS = [
    "darango.ccafs@gmail.com",
    "juanmanuelarias.jmag@gmail.com",
]

@app.route("/api/credit/send-email", methods=["POST"])
def api_credit_send_email():
    """Envía el reporte de crédito por correo electrónico."""
    import json as _json
    from flask import jsonify

    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    destinatarios = data.get("emails", DEFAULT_RECIPIENTS)
    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]

    result = _get_credit_result(token)
    if not result:
        return jsonify({"ok": False, "error": "Resultado no encontrado"}), 404

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        p = result.get("perfil_crediticio", {})
        res = result.get("resumen_ejecutivo", {})
        ant = result.get("antecedentes", {})

        # Construir HTML del correo
        color = "#15803d" if res.get("aprobado") else "#dc2626"
        tag = "APROBADO" if res.get("aprobado") else "RECHAZADO"

        ant_html = ""
        for k, v in ant.items():
            icon = "&#10003;" if not v.get("matched") else "&#10007;"
            ic = "#15803d" if not v.get("matched") else "#dc2626"
            lbl = "LIMPIO" if not v.get("matched") else "ENCONTRADO"
            ant_html += f'<tr><td style="padding:8px;border-bottom:1px solid #eee">{k}</td><td style="padding:8px;border-bottom:1px solid #eee;color:{ic};font-weight:700">{icon} {lbl}</td></tr>'

        bloqueantes_html = ""
        for b in res.get("bloqueantes", []):
            bloqueantes_html += f'<div style="padding:8px 12px;background:#fef2f2;border-left:3px solid #dc2626;margin:4px 0;font-size:13px;color:#991b1b">{b}</div>'

        html_body = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,{color},{color}cc);color:#fff;padding:24px;border-radius:12px 12px 0 0">
            <h1 style="margin:0;font-size:20px">VerifyData — Reporte de Riesgo Crediticio</h1>
          </div>
          <div style="padding:24px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none">
            <div style="background:#fff;padding:16px;border-radius:8px;margin-bottom:16px">
              <h2 style="margin:0 0 8px;font-size:18px">{result.get('nombre', '')}</h2>
              <p style="margin:0;color:#666">CC/NIT: {result.get('cedula_nit', '')}</p>
            </div>
            <div style="display:flex;gap:12px;margin-bottom:16px">
              <div style="flex:1;background:#fff;padding:16px;border-radius:8px;text-align:center;border-top:3px solid {color}">
                <div style="font-size:32px;font-weight:800;color:{color}">{p.get('score', 0)}</div>
                <div style="font-size:11px;color:#999;text-transform:uppercase">Score</div>
              </div>
              <div style="flex:1;background:#fff;padding:16px;border-radius:8px;text-align:center;border-top:3px solid {color}">
                <div style="font-size:16px;font-weight:700;color:{color}">{tag}</div>
                <div style="font-size:11px;color:#999;text-transform:uppercase">Decision</div>
              </div>
              <div style="flex:1;background:#fff;padding:16px;border-radius:8px;text-align:center;border-top:3px solid #6941f4">
                <div style="font-size:16px;font-weight:700;color:#6941f4">${res.get('monto_maximo', 0):,.0f}</div>
                <div style="font-size:11px;color:#999;text-transform:uppercase">Monto Max</div>
              </div>
            </div>
            <div style="background:#fff;padding:16px;border-radius:8px;margin-bottom:16px">
              <h3 style="margin:0 0 10px;font-size:14px">Antecedentes</h3>
              <table style="width:100%;border-collapse:collapse;font-size:13px">
                {ant_html}
              </table>
            </div>
            {f'<div style="margin-bottom:16px"><h3 style="margin:0 0 8px;font-size:14px;color:#dc2626">Bloqueantes</h3>{bloqueantes_html}</div>' if bloqueantes_html else ''}
            <div style="background:#fff;padding:16px;border-radius:8px;margin-bottom:16px">
              <h3 style="margin:0 0 10px;font-size:14px">Recomendacion</h3>
              <p style="margin:0;font-size:13px">{p.get('recomendacion', '')}</p>
            </div>
            <div style="background:#fff;padding:16px;border-radius:8px;margin-bottom:16px">
              <h3 style="margin:0 0 10px;font-size:14px">Justificacion del Monto</h3>
              <p style="margin:0;font-size:13px">{res.get('monto_justificacion', '')}</p>
            </div>
          </div>
          <div style="padding:16px;text-align:center;font-size:11px;color:#999">
            VerifyData — Documento generado automaticamente
          </div>
        </div>
        """

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"VerifyData — Reporte {tag} — {result.get('nombre', '')}"
        msg["From"] = "VerifyData <noreply@verifydata.app>"
        msg["To"] = ", ".join(destinatarios)

        # Adjuntar HTML
        msg.attach(MIMEText(html_body, "html"))

        # Adjuntar PDF
        try:
            from credit_report import generate_credit_pdf
            pdf_path = f"/tmp/credit_{token}.pdf"
            pdf_bytes = generate_credit_pdf(result, pdf_path)
            from email.mime.base import MIMEBase
            from email import encoders
            pdf_attachment = MIMEBase("application", "pdf")
            pdf_attachment.set_payload(pdf_bytes)
            encoders.encode_base64(pdf_attachment)
            pdf_name = f"VerifyData_Credito_{result.get('nombre', 'cliente').replace(' ', '_')}.pdf"
            pdf_attachment.add_header("Content-Disposition", f"attachment; filename={pdf_name}")
            msg.attach(pdf_attachment)
        except Exception as e:
            log.warning("No se pudo generar PDF para email: %s", e)

        # Intentar enviar (si hay SMTP configurado)
        smtp_host = os.environ.get("SMTP_HOST", "")
        if smtp_host:
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))
            smtp_user = os.environ.get("SMTP_USERNAME", "")
            smtp_pass = os.environ.get("SMTP_PASSWORD", "")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                if smtp_user:
                    server.login(smtp_user, smtp_pass)
                server.sendmail("noreply@verifydata.app", destinatarios, msg.as_string())
            return jsonify({"ok": True, "message": f"Correo enviado a {len(destinatarios)} destinatarios"})
        else:
            # Sin SMTP: devolver el HTML para preview
            return jsonify({
                "ok": True,
                "message": "SMTP no configurado — preview del correo",
                "preview_html": html_body,
                "to": destinatarios,
            })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
#  PÁGINA DE RESULTADOS — /credit/results/<token>
# ═══════════════════════════════════════════════════════════════════
#  DESCARGA DE PDF CREDITICIO
# ═══════════════════════════════════════════════════════════════════
@app.route("/download/credit-pdf/<token>")
def download_credit_pdf(token):
    """Genera y descarga el PDF del perfil crediticio."""
    from flask import send_file
    result = _get_credit_result(token)
    if not result:
        return "Resultado no encontrado", 404

    try:
        from credit_report import generate_credit_pdf
        import tempfile

        pdf_path = f"/tmp/credit_{token}.pdf"
        generate_credit_pdf(result, pdf_path)

        return send_file(
            pdf_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"VerifyData_Credito_{result.get('nombre', 'cliente').replace(' ', '_')}.pdf"
        )
    except Exception as e:
        return f"Error generando PDF: {e}", 500


# ═══════════════════════════════════════════════════════════════════
@app.route("/credit/results/<token>")
def credit_results_page(token):
    """Página dedicada de resultados del check integral crediticio."""
    import json
    result = _get_credit_result(token)
    if not result:
        return render_template_string(RESULTS_404_TEMPLATE), 404
    return render_template_string(
        RESULTS_TEMPLATE,
        result_json=json.dumps(result, ensure_ascii=False),
        token=token,
    )


RESULTS_404_TEMPLATE = ui_theme.head_open("VerifyData — Resultado no encontrado") + \
    ui_theme.shell_open("credito", "Resultado no encontrado", "") + """
<div class="main-content" style="text-align:center;padding:60px 20px">
  <div style="font-size:48px;margin-bottom:16px">&#128269;</div>
  <h2 style="margin-bottom:8px">Resultado no encontrado</h2>
  <p style="color:var(--text-faint)">El enlace ha expirado o el resultado no existe.</p>
  <a href="/credito" class="btn btn-primary" style="margin-top:20px;display:inline-block;text-decoration:none">
    &#8592; Volver al evaluador
  </a>
</div>
""" + ui_theme.SHELL_CLOSE


RESULTS_TEMPLATE = ui_theme.head_open("VerifyData — Resultado Crediticio") + \
    ui_theme.shell_open("credito", "Resultado del análisis", "") + """
<style>{% raw %}
  @keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
  .ri{animation:fadeIn .4s ease both}
  .ri-d1{animation-delay:.1s}.ri-d2{animation-delay:.2s}.ri-d3{animation-delay:.3s}.ri-d4{animation-delay:.4s}

  .hero{position:relative;overflow:hidden;border-radius:16px;padding:32px;margin-bottom:24px;color:#fff}
  .hero::before{content:'';position:absolute;top:-50%;right:-20%;width:400px;height:400px;border-radius:50%;background:rgba(255,255,255,.06);pointer-events:none}
  .hero::after{content:'';position:absolute;bottom:-30%;left:-10%;width:300px;height:300px;border-radius:50%;background:rgba(255,255,255,.04);pointer-events:none}
  .hero .tag{display:inline-block;padding:6px 16px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:.04em;background:rgba(255,255,255,.2);backdrop-filter:blur(4px);margin-bottom:12px}
  .hero .name{font-size:28px;font-weight:800;margin-bottom:4px;text-shadow:0 1px 2px rgba(0,0,0,.2)}
  .hero .sub{font-size:13px;opacity:.85}
  .hero .score-ring{width:120px;height:120px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(255,255,255,.15);backdrop-filter:blur(8px);border:3px solid rgba(255,255,255,.3)}
  .hero .score-num{font-size:42px;font-weight:800;line-height:1}
  .hero .score-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.08em;opacity:.8}
  .hero .verdict{padding:10px 24px;border-radius:12px;font-size:16px;font-weight:800;text-align:center;background:rgba(255,255,255,.2);backdrop-filter:blur(4px);min-width:140px}
  .hero .verdict .icon{font-size:28px;display:block;margin-bottom:2px}

  .kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
  .kpi{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;text-align:center;position:relative;overflow:hidden}
  .kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
  .kpi .icon{font-size:20px;margin-bottom:6px}
  .kpi .val{font-size:22px;font-weight:800;margin-bottom:2px}
  .kpi .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint)}

  .sec{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
  .sec-title{font-size:14px;font-weight:700;margin:0 0 16px;display:flex;align-items:center;gap:8px;padding-bottom:10px;border-bottom:1px solid var(--line)}
  .sec-title .badge{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;background:rgba(0,0,0,.05)}

  .ant-row{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:10px;margin-bottom:8px;border:1px solid var(--line);transition:all .15s}
  .ant-row:hover{border-color:rgba(0,0,0,.12);box-shadow:0 2px 8px rgba(0,0,0,.04)}
  .ant-icon{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;font-weight:700}
  .ant-icon.g{background:rgba(34,197,94,.1);color:#15803d}
  .ant-icon.r{background:rgba(220,38,38,.1);color:#dc2626}
  .ant-icon.y{background:rgba(217,119,6,.1);color:#d97706}
  .ant-info{flex:1}
  .ant-name{font-weight:700;font-size:13px}
  .ant-desc{font-size:11px;color:var(--text-faint);margin-top:2px}
  .ant-badge{font-size:10px;font-weight:700;padding:3px 10px;border-radius:12px;letter-spacing:.03em}
  .ant-badge.clean{background:rgba(34,197,94,.1);color:#15803d}
  .ant-badge.hit{background:rgba(220,38,38,.1);color:#dc2626}
  .ant-badge.err{background:rgba(217,119,6,.1);color:#d97706}

  .factor-row{display:flex;align-items:start;gap:10px;padding:8px 0;font-size:13px;border-bottom:1px solid rgba(0,0,0,.04)}
  .factor-row:last-child{border:none}
  .factor-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}

  .just-box{background:linear-gradient(135deg,rgba(105,65,244,.06),rgba(62,122,249,.06));border:1px solid rgba(105,65,244,.15);border-radius:12px;padding:20px;margin-top:12px}
  .just-box .title{font-size:12px;font-weight:700;color:#6941f4;margin-bottom:8px;display:flex;align-items:center;gap:6px}
  .just-box .formula{font-family:monospace;font-size:13px;background:rgba(0,0,0,.04);padding:10px 14px;border-radius:8px;margin-top:10px;line-height:1.8}

  .block-alert{background:linear-gradient(135deg,rgba(220,38,38,.06),rgba(220,38,38,.02));border:2px solid rgba(220,38,38,.2);border-radius:12px;padding:20px;margin-bottom:16px}
  .block-alert .title{font-size:14px;font-weight:700;color:#dc2626;margin-bottom:10px;display:flex;align-items:center;gap:8px}
  .block-item{padding:10px 14px;background:rgba(220,38,38,.04);border-radius:8px;margin-bottom:6px;font-size:13px;color:#991b1b;border-left:3px solid #dc2626}

  .doc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .doc-item{padding:12px;border-radius:10px;text-align:center;font-size:12px;font-weight:600;border:1px solid var(--line);transition:all .15s}
  .doc-item.yes{background:rgba(34,197,94,.06);border-color:rgba(34,197,94,.2);color:#15803d}
  .doc-item.no{background:rgba(0,0,0,.01);color:var(--text-faint)}
  .doc-item .doc-icon{font-size:20px;margin-bottom:4px}

  .actions{display:flex;gap:12px;margin-top:24px;justify-content:center;padding:20px 0}
  @media(max-width:800px){.kpi-row{grid-template-columns:1fr 1fr}.doc-grid{grid-template-columns:1fr 1fr}.hero{flex-direction:column;gap:20px;text-align:center}}
{% endraw %}</style>

<div class="main-content" id="app"></div>

<script>
var DATA = {{ result_json|safe }};

function $(id){return document.getElementById(id)}
function render(){
  var r=DATA, p=r.perfil_crediticio, ant=r.antecedentes||{}, res=r.resumen_ejecutivo, docs=p.docs||{};
  var C={'BAJO':'#15803d','MEDIO':'#d97706','ALTO':'#dc2626','CRITICO':'#991b1b'};
  var c=C[p.nivel_riesgo]||'#333';
  var ok=res.aprobado, tagBg=ok?'rgba(34,197,94,.9)':'rgba(220,38,38,.9)';
  var tagIcon=ok?'&#10003;':'&#10007;';
  var tagTxt=ok?'APROBADO':'RECHAZADO';
  var h='';

  // ═══ HERO ═══
  h+='<div class="hero ri" style="background:linear-gradient(135deg,'+c+','+c+'cc)">';
  h+='  <div><div class="tag">ANALISIS INTEGRAL DE RIESGO CREDITICIO</div>';
  h+='  <div class="name">'+esc(r.nombre)+'</div>';
  h+='  <div class="sub">CC/NIT: '+esc(r.cedula_nit)+' &middot; '+new Date().toLocaleDateString('es-CO',{day:'2-digit',month:'long',year:'numeric'})+'</div></div>';
  h+='  <div class="score-ring"><div class="score-num">'+p.score+'</div><div class="score-lbl">Score / 1000</div></div>';
  h+='  <div class="verdict"><div class="icon">'+tagIcon+'</div>'+tagTxt+'<div style="font-size:11px;font-weight:400;opacity:.8;margin-top:2px">'+esc(p.nivel_riesgo)+'</div></div>';
  h+='</div>';

  // ═══ KPIs ═══
  h+='<div class="kpi-row ri ri-d1">';
  h+='  <div class="kpi" style="border-top:3px solid '+c+'"><div class="icon">&#127919;</div><div class="val" style="color:'+c+'">'+p.score+'</div><div class="lbl">Score Crediticio</div></div>';
  h+='  <div class="kpi" style="border-top:3px solid '+c+'"><div class="icon">&#128200;</div><div class="val" style="color:'+c+';font-size:16px">'+esc(p.nivel_riesgo)+'</div><div class="lbl">Nivel de Riesgo</div></div>';
  var montoTxt=Number(res.monto_maximo||0)>0?'$'+Number(res.monto_maximo).toLocaleString('es-CO'):'No calculable';
  h+='  <div class="kpi" style="border-top:3px solid #6941f4"><div class="icon">&#128176;</div><div class="val" style="color:#6941f4;font-size:18px">'+montoTxt+'</div><div class="lbl">Monto Max Recomendado</div></div>';
  var antCount=Object.keys(ant).length;
  var cleanCount=0; for(var k in ant){if(!ant[k].matched&&!ant[k].error)cleanCount++;}
  h+='  <div class="kpi" style="border-top:3px solid #3e7af9"><div class="icon">&#128269;</div><div class="val" style="color:#3e7af9">'+cleanCount+'/'+antCount+'</div><div class="lbl">Listas Limpias</div></div>';
  h+='</div>';

  // ═══ BLOQUEANTES ═══
  if(res.bloqueantes&&res.bloqueantes.length){
    h+='<div class="block-alert ri ri-d2">';
    h+='  <div class="title">&#9888; BLOQUEANTES ENCONTRADOS</div>';
    for(var i=0;i<res.bloqueantes.length;i++) h+='<div class="block-item">'+res.bloqueantes[i]+'</div>';
    h+='</div>';
  }

  // ═══ RSALES ═══
  h+='<div class="sec ri ri-d2">';
  h+='  <div class="sec-title">&#128225; Historial Comercial — RSALES <span class="badge">ventasremotas.com</span></div>';
  if(p.rsales){
    var rs=p.rsales;
    var vc=(rs.pct_vencida||0)>30?'#dc2626':((rs.pct_vencida||0)>15?'#d97706':'#15803d');
    h+='<div class="kpi-row">';
    h+='  <div class="kpi" style="padding:14px"><div class="val" style="font-size:20px">$'+N(rs.cartera_total||0)+'</div><div class="lbl">Cartera Total</div></div>';
    h+='  <div class="kpi" style="padding:14px;border-top:3px solid '+vc+'"><div class="val" style="font-size:20px;color:'+vc+'">$'+N(rs.cartera_vencida||0)+'<span style="font-size:12px;font-weight:400"> ('+P(rs.pct_vencida)+')</span></div><div class="lbl">Cartera Vencida</div></div>';
    h+='  <div class="kpi" style="padding:14px"><div class="val" style="font-size:20px">$'+N(rs.compras_total||0)+'</div><div class="lbl">Compras Totales</div></div>';
    h+='  <div class="kpi" style="padding:14px"><div class="val" style="font-size:20px">'+(rs.dias_mora_max||0)+' <span style="font-size:12px;font-weight:400">dias</span></div><div class="lbl">Mora Maxima</div></div>';
    h+='</div>';
  } else {
    h+='<div style="text-align:center;padding:24px;color:var(--text-faint)"><div style="font-size:28px;margin-bottom:8px">&#128269;</div>Cliente no encontrado en RSALES<br><span style="font-size:12px">Sin historial comercial disponible</span></div>';
  }
  h+='</div>';

  // ═══ JUSTIFICACION MONTO ═══
  if(res.monto_justificacion){
    h+='<div class="just-box ri ri-d3">';
    h+='  <div class="title">&#128202; Justificacion del Monto Maximo Recomendado</div>';
    h+='  <div style="font-size:13px;line-height:1.7">'+esc(res.monto_justificacion)+'</div>';
    h+='  <div class="formula">';
    h+='    <b>Formula:</b> Ventas anuales x 30% (capacidad) x multiplicador de riesgo<br>';
    h+='    <b>Ejemplo:</b> $112,540,083 x 0.30 x 0.6 = <b style="color:#6941f4">$20,257,215</b>';
    h+='  </div>';
    h+='</div>';
  }

  // ═══ ANTECEDENTES ═══
  h+='<div class="sec ri ri-d3">';
  h+='  <div class="sec-title">&#128269; Antecedentes y Listas Restrictivas <span class="badge">'+Object.keys(ant).length+' fuentes</span></div>';
  var ak=Object.keys(ant);
  for(var i=0;i<ak.length;i++){
    var k=ak[i],v=ant[k];
    var cls=v.matched?'r':(v.error?'y':'g');
    var sym=v.matched?'&#10007;':(v.error?'&#9888;':'&#10003;');
    var lbl=v.matched?'ENCONTRADO':(v.error?'ERROR':'LIMPIO');
    var bc=v.matched?'hit':(v.error?'err':'clean');
    h+='<div class="ant-row">';
    h+='  <div class="ant-icon '+cls+'">'+sym+'</div>';
    h+='  <div class="ant-info"><div class="ant-name">'+esc(k)+'</div>';
    if(v.summary)h+='<div class="ant-desc">'+esc(v.summary)+'</div>';
    h+='</div>';
    h+='  <div class="ant-badge '+bc+'">'+lbl+'</div>';
    h+='</div>';
  }
  h+='</div>';

  // ═══ DOCUMENTOS ═══
  var dc=0;for(var dk in docs){if(docs[dk])dc++;}
  var dl={cedula_frontal:'&#128196; Cedula Frontal',cedula_posterior:'&#128196; Cedula Posterior',rut:'&#128196; RUT',camara_comercio:'&#127970; Camara de Comercio',estados_financieros:'&#128200; Estados Financieros',declaracion_renta:'&#128196; Declaracion Renta'};
  h+='<div class="sec ri ri-d3">';
  h+='  <div class="sec-title">&#128196; Documentacion Adjunta <span class="badge">'+dc+'/'+Object.keys(dl).length+'</span></div>';
  h+='  <div class="doc-grid">';
  for(var dk in dl){
    var ok2=docs[dk];
    h+='<div class="doc-item '+(ok2?'yes':'no')+'"><div class="doc-icon">'+(ok2?'&#10003;':'&#10007;')+'</div>'+dl[dk]+'</div>';
  }
  h+='  </div>';
  h+='</div>';

  // ═══ FACTORES ═══
  h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px" class="ri ri-d4">';
  h+='<div class="sec" style="border-top:3px solid #15803d">';
  h+='  <div class="sec-title" style="color:#15803d;border-color:rgba(34,197,94,.2)">&#10003; Factores Positivos</div>';
  if(p.factores_positivos&&p.factores_positivos.length){
    for(var i=0;i<p.factores_positivos.length;i++) h+='<div class="factor-row"><div class="factor-dot" style="background:#15803d"></div><div>'+esc(p.factores_positivos[i])+'</div></div>';
  } else h+='<div style="color:var(--text-faint);font-size:12px;padding:8px 0">Ninguno identificado</div>';
  h+='</div>';
  h+='<div class="sec" style="border-top:3px solid #dc2626">';
  h+='  <div class="sec-title" style="color:#dc2626;border-color:rgba(220,38,38,.2)">&#10007; Factores Negativos</div>';
  if(p.factores_negativos&&p.factores_negativos.length){
    for(var i=0;i<p.factores_negativos.length;i++) h+='<div class="factor-row"><div class="factor-dot" style="background:#dc2626"></div><div>'+esc(p.factores_negativos[i])+'</div></div>';
  } else h+='<div style="color:var(--text-faint);font-size:12px;padding:8px 0">Ninguno identificado</div>';
  h+='</div>';
  h+='</div>';

  // ═══ ACCIONES ═══
  h+='<div class="actions">';
  h+='  <a href="/credito" class="btn btn-primary" style="text-decoration:none;padding:12px 28px;font-size:14px">&#8592; Nueva Evaluacion</a>';
  h+='  <button class="btn btn-secondary" onclick="window.print()" style="padding:12px 28px;font-size:14px">&#128424; Imprimir Reporte</button>';
  h+='  <a href="/download/credit-pdf/'+window.location.pathname.split('/').pop()+'" class="btn btn-secondary" style="text-decoration:none;padding:12px 28px;font-size:14px" download>&#128196; Descargar PDF</a>';
  h+='  <button class="btn btn-secondary" onclick="enviarCorreo()" id="btn-email" style="padding:12px 28px;font-size:14px">&#9993; Enviar por Correo</button>';
  h+='</div>';

  $('app').innerHTML=h;
}

function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}
function N(v){return Number(v||0).toLocaleString('es-CO');}
function P(v){return Number(v||0).toFixed(1)+'%';}

function enviarCorreo(){
  var btn=document.getElementById('btn-email');
  if(!btn)return;
  btn.disabled=true;
  btn.innerHTML='&#8987; Enviando...';
  var token=window.location.pathname.split('/').pop();
  fetch('/api/credit/send-email',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:token,emails:['darango.ccafs@gmail.com','juanmanuelarias.jmag@gmail.com']})
  }).then(function(r){return r.json();}).then(function(d){
    btn.disabled=false;
    if(d.ok){
      btn.innerHTML='&#10003; Enviado a '+d.to;
      btn.style.background='#15803d';
      btn.style.color='#fff';
      btn.style.borderColor='#15803d';
      if(d.preview_html){
        var w=window.open('','_blank','width=600,height=800');
        w.document.write('<html><head><title>Preview Correo</title></head><body>'+d.preview_html+'</body></html>');
      }
    }else{
      btn.innerHTML='&#10007; Error: '+esc(d.error||'Desconocido');
      btn.style.color='#dc2626';
    }
  }).catch(function(e){
    btn.disabled=false;
    btn.innerHTML='&#10007; Error de red';
    btn.style.color='#dc2626';
  });
}

render();
</script>
""" + ui_theme.SHELL_CLOSE


# ==============================================================
#  Main
# ==============================================================

if __name__ == "__main__":
    import os
    host = os.environ.get("HOST", CFG["webapp"]["host"])
    port = int(os.environ.get("PORT", CFG["webapp"]["port"]))
    debug = CFG["webapp"].get("debug", False)
    n = len(registry.all_sources())
    from db import is_postgres
    _db_backend = "PostgreSQL (pool)" if is_postgres() else f"SQLite ({DB_PATH})"
    print(f"\n  VerifyData Demo → http://{host}:{port}")
    print(f"  API REST      → http://{host}:{port}/api/v1")
    print(f"  API Docs      → http://{host}:{port}/api/v1/docs")
    print(f"  Fuentes registradas: {n}")
    print(f"  Base de datos: {_db_backend}")
    _api_keys = CFG.get("api", {}).get("keys") or []
    _is_dev = os.environ.get("VERIFYDATA_ENV", "").strip().lower() in (
        "dev", "development", "local")
    if _api_keys:
        _api_auth = "API key requerida"
    elif _is_dev:
        _api_auth = "ABIERTA (sin key — solo dev, VERIFYDATA_ENV=dev)"
    else:
        _api_auth = "CERRADA (sin key en prod — define VERIFYDATA_API_KEYS)"
    print(f"  API auth: {_api_auth}")
    print(f"  Captcha solver: {SOLVER.name} ({'activo' if SOLVER.is_available() else 'sin servicio — muestra aviso'})\n")
    # Servicio MULTI-USUARIO: preferir waitress (servidor WSGI de producción,
    # multi-hilo) si está instalado; si no, usar el server de Flask en modo
    # threaded=True para atender polling + descargas concurrentes sin
    # serializar. Instalar producción: `pip install waitress`.
    try:
        from waitress import serve
        threads = int(os.environ.get("VERIFYDATA_HTTP_THREADS", "16"))
        print(f"  Servidor: waitress ({threads} hilos)\n")
        serve(app, host=host, port=port, threads=threads)
    except ImportError:
        print("  Servidor: Flask dev (threaded). Para producción: pip install waitress\n")
        app.run(host=host, port=port, debug=debug, threaded=True)
