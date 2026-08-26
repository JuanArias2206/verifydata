#!/usr/bin/env python3
"""
auth.py — Autenticación y control de acceso por roles (RBAC) para VerifyData.

Diseño portado del CRM (verifydata-app) y adaptado a Flask + SQLite:

  • Dos métodos de login, ambos validando contra la tabla `users`:
      1. OTP por email  (código de 6 dígitos vía SMTP MailerSend)
      2. Microsoft SSO  (OAuth2 Authorization Code con MSAL, tenant Entra ID)
  • REGLA DE ORO: solo emails presentes en `users` con activo=1 pueden entrar.
    Nunca se auto-provisiona un usuario (a diferencia del CRM). Si el email no
    está en la BD, el login se rechaza en ambos métodos.
  • Sesión server-side: token opaco en cookie httpOnly; en la BD se guarda solo
    su SHA-256. Revocable (logout borra la fila).
  • RBAC: roles admin | analista | viewer con niveles jerárquicos y decoradores
    require_login / require_role. Panel de administración de usuarios (admin).

Config vía variables de entorno (cargadas de .env por config._load_dotenv):
  SESSION_SECRET                         firma de cookies Flask
  SHAREPOINT_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET   app de Entra (SSO)
  AUTH_REDIRECT_URI                      redirect URI registrado en Entra
  SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / EMAIL_FROM[_NAME]
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from typing import Any, Optional

from flask import (Blueprint, Response, abort, g, jsonify, make_response,
                   redirect, render_template_string, request, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db, as_naive_utc, otp_rate_hit
from logging_config import audit
import ui_theme

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ============================================================================
#  Configuración (leída del entorno)
# ============================================================================
COOKIE_NAME = "mt_session"
SESSION_TTL_HOURS = int(os.environ.get("AUTH_SESSION_TTL_HOURS", "12"))
OTP_TTL_MINUTES = int(os.environ.get("AUTH_OTP_TTL_MINUTES", "10"))
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60  # rate-limit por email

# Azure / Entra ID (reutiliza la app registrada para SharePoint)
AZURE_TENANT_ID = os.environ.get("SHAREPOINT_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("SHAREPOINT_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")
AZURE_AUTHORITY = (f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
                   if AZURE_TENANT_ID else "")
AZURE_SCOPES = ["User.Read"]  # openid/profile/email los agrega MSAL solo
# Redirect URI: DEBE estar registrado en la app de Entra (Authentication →
# Web → Redirect URIs). Configúralo con AUTH_REDIRECT_URI; por defecto local.
_default_port = os.environ.get("PORT", "5070")
AUTH_REDIRECT_URI = os.environ.get(
    "AUTH_REDIRECT_URI",
    f"http://localhost:{_default_port}/auth/microsoft/callback")

# SMTP (MailerSend u otro)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "VerifyData")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Dominios a los que se PERMITE enviar un OTP por email. Solo estos correos
# pueden pedir código; a cualquier otro se le rechaza. Vacío = sin restricción
# (demo). Configurable con OTP_ALLOWED_DOMAINS (lista separada por comas).
OTP_ALLOWED_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get("OTP_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
}

# ============================================================================
#  RBAC — jerarquía de roles
# ============================================================================
#  admin     → acceso total + administración de usuarios
#  analista  → ejecutar búsquedas, ver y exportar resultados
#  viewer    → solo lectura de resultados existentes (no lanza búsquedas)
ROLE_LEVELS = {"admin": 3, "analista": 2, "viewer": 1}
VALID_ROLES = set(ROLE_LEVELS)


def has_level(rol: str, minimum: str) -> bool:
    return ROLE_LEVELS.get(rol or "", 0) >= ROLE_LEVELS.get(minimum, 99)


# ============================================================================
#  Utilidades
# ============================================================================
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def domain_allowed(email: str) -> bool:
    """True si el dominio del email está permitido para recibir OTP.
    Si OTP_ALLOWED_DOMAINS está vacío, no se restringe por dominio."""
    if not OTP_ALLOWED_DOMAINS:
        return True
    domain = normalize_email(email).rsplit("@", 1)[-1]
    return domain in OTP_ALLOWED_DOMAINS


def mask_email(email: str) -> str:
    try:
        local, domain = email.split("@")
    except ValueError:
        return email
    shown = local[0] if local else ""
    return f"{shown}{'*' * max(len(local) - 1, 1)}@{domain}"


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ============================================================================
#  Acceso a usuarios
# ============================================================================
def get_user_by_email(conn, email: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE email=?",
                       (normalize_email(email),)).fetchone()
    return dict(row) if row else None


def get_active_user_by_email(conn, email: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE email=? AND activo=TRUE",
                       (normalize_email(email),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(conn, user_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


# ============================================================================
#  OTP
# ============================================================================
# Rate limit de OTP PERSISTENTE (tabla otp_rate_limit, ver db.otp_rate_hit).
# Sobrevive reinicios y se comparte entre workers/procesos.
OTP_IP_MAX = int(os.environ.get("AUTH_OTP_IP_MAX", "5"))          # peticiones
OTP_IP_WINDOW_SECONDS = int(os.environ.get("AUTH_OTP_IP_WINDOW", "300"))


def _client_ip() -> str:
    """IP del cliente. Detrás de un proxy TLS toma el primer salto de
    X-Forwarded-For; si no, remote_addr."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _ip_rate_limited(ip: str) -> bool:
    """True si `ip` superó OTP_IP_MAX solicitudes en la ventana. Persistente."""
    return otp_rate_hit("ip", ip, OTP_IP_WINDOW_SECONDS) > OTP_IP_MAX


def _email_rate_limited(email: str) -> bool:
    """True si ese email pidió OTP hace menos de OTP_RESEND_SECONDS. Persistente
    (1 código por ventana)."""
    return otp_rate_hit("email", email, OTP_RESEND_SECONDS) > 1


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_otp(conn, email: str) -> str:
    """Genera un OTP, invalida los previos y devuelve el código en claro."""
    email = normalize_email(email)
    code = _generate_code()
    # pbkdf2:sha256 explícito: el default de Werkzeug (scrypt) requiere soporte
    # OpenSSL que no siempre está (p.ej. LibreSSL en macOS).
    code_hash = generate_password_hash(code, method="pbkdf2:sha256")
    expires = _iso(_now() + timedelta(minutes=OTP_TTL_MINUTES))
    conn.execute("UPDATE otp_codes SET used=TRUE WHERE email=? AND used=FALSE",
                 (email,))
    conn.execute(
        "INSERT INTO otp_codes (email, code_hash, expires_at) VALUES (?,?,?)",
        (email, code_hash, expires))
    conn.commit()
    return code


def verify_otp(conn, email: str, code: str) -> tuple[bool, str]:
    """Devuelve (ok, motivo). Consume el OTP si es válido."""
    email = normalize_email(email)
    row = conn.execute(
        "SELECT * FROM otp_codes WHERE email=? AND used=FALSE "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (email,)).fetchone()
    if not row:
        return False, "not_found"
    if as_naive_utc(row["expires_at"]) < _now():
        return False, "expired"
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        return False, "max_attempts"
    if not check_password_hash(row["code_hash"], (code or "").strip()):
        conn.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?",
                     (row["id"],))
        conn.commit()
        return False, "invalid_code"
    conn.execute("UPDATE otp_codes SET used=TRUE WHERE id=?", (row["id"],))
    conn.commit()
    return True, "ok"


def send_otp_email(to_email: str, code: str) -> None:
    """Envía el OTP por SMTP. Lanza excepción si SMTP no está configurado."""
    if not (SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD):
        raise RuntimeError("SMTP no configurado (SMTP_HOST/USERNAME/PASSWORD).")
    html = f"""\
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px 24px">
  <div style="font-weight:900;font-size:22px;margin-bottom:24px;color:#111827">
    Verify<span style="color:#6941F4">Data</span>
  </div>
  <p style="font-size:15px;color:#333;margin-bottom:16px">
    Tu código de acceso a VerifyData es:</p>
  <div style="background:#221f33;color:#1de5e9;font-size:36px;font-weight:900;
              letter-spacing:12px;text-align:center;padding:20px;border-radius:10px;
              margin-bottom:20px;font-family:'Courier New',monospace">{code}</div>
  <p style="font-size:13px;color:#666;line-height:1.6">
    Válido por <strong>{OTP_TTL_MINUTES} minutos</strong>. Si no solicitaste
    este código, ignora este mensaje.<br>No lo compartas con nadie.</p>
</div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Tu código VerifyData: {code}"
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = to_email
    msg.attach(MIMEText(f"Tu código de acceso a VerifyData es: {code}\n"
                        f"Válido por {OTP_TTL_MINUTES} minutos.", "plain"))
    msg.attach(MIMEText(html, "html"))

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20,
                              context=ssl.create_default_context()) as s:
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(SMTP_USERNAME, SMTP_PASSWORD)
            s.send_message(msg)


# ============================================================================
#  Sesiones
# ============================================================================
def create_session(conn, user_id: int) -> str:
    """Crea una sesión y devuelve el token en claro (para la cookie)."""
    token = secrets.token_urlsafe(32)
    expires = _iso(_now() + timedelta(hours=SESSION_TTL_HOURS))
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, expires_at) "
        "VALUES (?,?,?)", (_sha256(token), user_id, expires))
    conn.commit()
    return token


def resolve_session(conn, token: str) -> Optional[dict]:
    """Devuelve el usuario (dict) de una sesión válida, o None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT s.user_id, s.expires_at FROM auth_sessions s "
        "WHERE s.token_hash=?", (_sha256(token),)).fetchone()
    if not row or as_naive_utc(row["expires_at"]) < _now():
        return None
    user = get_user_by_id(conn, row["user_id"])
    if not user or not user["activo"]:
        return None
    return user


def destroy_session(conn, token: str) -> None:
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?",
                     (_sha256(token),))
        conn.commit()


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_TTL_HOURS * 3600,
        httponly=True, samesite="Lax",
        secure=(request.scheme == "https"))


def _clear_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME)


# ============================================================================
#  Carga del usuario actual + decoradores  (usados por app.py)
# ============================================================================
def load_logged_in_user() -> Optional[dict]:
    """Lee la cookie y fija g.user (dict o None). Idempotente por request."""
    if "user" in g:
        return g.user
    token = request.cookies.get(COOKIE_NAME)
    user = None
    if token:
        with get_db() as conn:
            user = resolve_session(conn, token)
    g.user = user
    return user


def is_public_path(path: str) -> bool:
    """Rutas que NO requieren sesión de UI:
      • /login, /logout
      • /auth/*         (login, callback, logout)
      • /api/v1*        (API REST con su propia auth por API-key)
      • /api/credit/warm-rsales (pre-calentamiento)
      • estáticos de Flask
      • /health (si existiera)
    """
    return (path.startswith("/login")
            or path.startswith("/logout")
            or path.startswith("/auth/")
            or path.startswith("/api/v1")
            or path.startswith("/static/")
            or path == "/health"
            or path == "/favicon.ico"
            or path == "/api/credit/warm-rsales")


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not load_logged_in_user():
            return _reject_unauthenticated()
        return view(*args, **kwargs)
    return wrapped


def require_role(*roles: str):
    """Exige que el usuario tenga uno de los roles dados (o nivel superior)."""
    minimum = min(roles, key=lambda r: ROLE_LEVELS.get(r, 99))

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = load_logged_in_user()
            if not user:
                return _reject_unauthenticated()
            if not has_level(user["rol"], minimum):
                if _wants_json():
                    return jsonify(ok=False,
                                   error=f"Requiere rol: {', '.join(roles)}"), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _wants_json() -> bool:
    return (request.path.startswith("/api/")
            or "application/json" in (request.headers.get("Accept") or ""))


def _reject_unauthenticated():
    if _wants_json():
        return jsonify(ok=False, error="No autenticado"), 401
    return redirect(url_for("login_page", next=request.path))


# ============================================================================
#  MSAL (Microsoft SSO) — construcción perezosa
# ============================================================================
def _msal_app():
    import msal
    if not (AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_AUTHORITY):
        raise RuntimeError("Azure/Entra no configurado "
                           "(SHAREPOINT_TENANT_ID/CLIENT_ID/CLIENT_SECRET).")
    return msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID, authority=AZURE_AUTHORITY,
        client_credential=AZURE_CLIENT_SECRET)


def sso_enabled() -> bool:
    return bool(AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_TENANT_ID)


# ============================================================================
#  Rutas — Login (página + OTP + SSO)
# ============================================================================
# NOTA: La ruta /login ahora está en app.py (login simple naprolab/naprolab)
# Esta ruta OTP/SSO queda deshabilitada.
# @auth_bp.route("/login")
# def login():
#     if load_logged_in_user():
#         return redirect("/")
#     return render_template_string(
#         LOGIN_TEMPLATE, sso_enabled=sso_enabled(),
#         next=request.args.get("next", "/"),
#         error=request.args.get("error", ""))


@auth_bp.route("/otp/request", methods=["POST"])
def otp_request():
    data = request.get_json(silent=True) or request.form
    email = normalize_email(data.get("email", ""))
    if not valid_email(email):
        return jsonify(ok=False, error="Correo inválido."), 400

    # Política corporativa: solo se envía OTP a dominios permitidos.
    if not domain_allowed(email):
        allowed = ", ".join(f"@{d}" for d in sorted(OTP_ALLOWED_DOMAINS))
        return jsonify(ok=False,
                       error=f"Solo se permiten correos {allowed}."), 403

    # Rate-limit por IP (frena enumeración de emails y DoS de correo).
    if _ip_rate_limited(_client_ip()):
        return jsonify(ok=False,
                       error="Demasiadas solicitudes desde esta red. "
                             "Intenta de nuevo en unos minutos."), 429

    # Rate-limit por email (1 código por ventana). Persistente.
    if _email_rate_limited(email):
        return jsonify(ok=False,
                       error="Espera un minuto antes de pedir otro código."), 429

    with get_db() as conn:
        user = get_active_user_by_email(conn, email)
        code = create_otp(conn, email) if user else None

    # Respuesta GENÉRICA: no revelar si el email está registrado. Solo se envía
    # el correo cuando la cuenta existe, pero la respuesta es idéntica en ambos
    # casos ("si el email existe, se envió un código").
    if code is not None:
        try:
            send_otp_email(email, code)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False,
                           error=f"No se pudo enviar el código: {exc}"), 500

    # Auditoría (nunca el código): registra la solicitud y si existía cuenta.
    audit("otp_request", email=email, ip=_client_ip(), sent=code is not None)
    return jsonify(ok=True, masked=mask_email(email))


@auth_bp.route("/otp/verify", methods=["POST"])
def otp_verify():
    data = request.get_json(silent=True) or request.form
    email = normalize_email(data.get("email", ""))
    code = str(data.get("code", "")).strip()
    if not email or not code:
        return jsonify(ok=False, error="email y code son requeridos"), 400

    # Se resuelve todo dentro del `with` y se AUDITA fuera (con la conexión ya
    # liberada) para no anidar get_db → audit_write (evita presión del pool).
    fail_reason = None
    user = None
    token = None
    with get_db() as conn:
        ok, reason = verify_otp(conn, email, code)
        if not ok:
            fail_reason = reason
        else:
            user = get_active_user_by_email(conn, email)
            if not user:  # se desactivó entre request y verify
                fail_reason = "inactive"
            else:
                conn.execute("UPDATE users SET last_login=?, auth_provider="
                             "CASE WHEN auth_provider='microsoft' THEN 'both' "
                             "ELSE auth_provider END WHERE id=?",
                             (_iso(_now()), user["id"]))
                conn.commit()
                token = create_session(conn, user["id"])

    if fail_reason == "inactive":
        audit("login_fail", method="otp", email=email,
              ip=_client_ip(), reason="inactive")
        return jsonify(ok=False, error="Cuenta no autorizada."), 403
    if fail_reason is not None:
        msgs = {
            "not_found": "Código no encontrado. Solicita uno nuevo.",
            "expired": "El código venció. Solicita uno nuevo.",
            "max_attempts": "Demasiados intentos. Solicita un código nuevo.",
            "invalid_code": "Código incorrecto. Verifica e intenta de nuevo.",
        }
        audit("login_fail", method="otp", email=email,
              ip=_client_ip(), reason=fail_reason)
        return jsonify(ok=False,
                       error=msgs.get(fail_reason, "Código inválido.")), 400

    audit("login_ok", method="otp", email=email,
          user_id=user["id"], ip=_client_ip())
    resp = make_response(jsonify(ok=True, redirect="/"))
    _set_cookie(resp, token)
    return resp


@auth_bp.route("/microsoft")
def microsoft_login():
    if not sso_enabled():
        return redirect(url_for("login_page", error="SSO no configurado"))
    state = secrets.token_urlsafe(16)
    resp = make_response(redirect(_msal_app().get_authorization_request_url(
        AZURE_SCOPES, state=state, redirect_uri=AUTH_REDIRECT_URI)))
    # state anti-CSRF en cookie corta
    resp.set_cookie("mt_oauth_state", state, max_age=600, httponly=True,
                    samesite="Lax", secure=(request.scheme == "https"))
    return resp


@auth_bp.route("/microsoft/callback")
def microsoft_callback():
    if request.args.get("error"):
        return redirect(url_for(
            "auth.login",
            error=request.args.get("error_description", "Error de Microsoft")))
    # Validar state anti-CSRF
    if request.args.get("state") != request.cookies.get("mt_oauth_state"):
        return redirect(url_for("login_page", error="Estado inválido (CSRF)."))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login_page", error="Sin código de Microsoft."))

    result = _msal_app().acquire_token_by_authorization_code(
        code, scopes=AZURE_SCOPES, redirect_uri=AUTH_REDIRECT_URI)
    if "access_token" not in result:
        return redirect(url_for(
            "auth.login",
            error=result.get("error_description", "Fallo al autenticar.")))

    claims = result.get("id_token_claims", {}) or {}
    email = normalize_email(
        claims.get("preferred_username") or claims.get("email") or "")
    oid = claims.get("oid")
    if not email:
        return redirect(url_for("login_page", error="Microsoft no devolvió email."))

    user = None
    token = None
    with get_db() as conn:
        user = get_active_user_by_email(conn, email)
        if user:
            conn.execute(
                "UPDATE users SET last_login=?, azure_oid=?, auth_provider="
                "CASE WHEN auth_provider='otp' THEN 'both' ELSE 'microsoft' END "
                "WHERE id=?", (_iso(_now()), oid, user["id"]))
            conn.commit()
            token = create_session(conn, user["id"])

    if not user:
        # REGLA DE ORO: no se auto-provisiona. Solo usuarios en la BD.
        audit("login_fail", method="microsoft", email=email,
              ip=_client_ip(), reason="unauthorized")
        return redirect(url_for(
            "auth.login",
            error="Tu cuenta Microsoft no está autorizada en VerifyData."))

    audit("login_ok", method="microsoft", email=email,
          user_id=user["id"], ip=_client_ip())
    resp = make_response(redirect("/"))
    resp.delete_cookie("mt_oauth_state")
    _set_cookie(resp, token)
    return resp


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with get_db() as conn:
            destroy_session(conn, token)
    resp = make_response(redirect(url_for("login_page")))
    _clear_cookie(resp)
    return resp


@auth_bp.route("/me")
def me():
    user = load_logged_in_user()
    if not user:
        return jsonify(ok=False, error="No autenticado"), 401
    return jsonify(ok=True, user={
        "id": user["id"], "email": user["email"], "nombre": user["nombre"],
        "rol": user["rol"]})


# ============================================================================
#  Rutas — Administración de usuarios (solo admin)
# ============================================================================
@auth_bp.route("/admin/users")
@require_role("admin")
def admin_users_page():
    return render_template_string(ADMIN_TEMPLATE, user=g.user, roles=sorted(
        VALID_ROLES, key=lambda r: -ROLE_LEVELS[r]))


@auth_bp.route("/admin/api/users", methods=["GET"])
@require_role("admin")
def admin_users_list():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, nombre, rol, activo, auth_provider, "
            "last_login, created_at FROM users ORDER BY id").fetchall()
    return jsonify(ok=True, users=[dict(r) for r in rows])


@auth_bp.route("/admin/api/users", methods=["POST"])
@require_role("admin")
def admin_users_create():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email", ""))
    nombre = (data.get("nombre") or "").strip()
    rol = (data.get("rol") or "viewer").strip()
    if not valid_email(email):
        return jsonify(ok=False, error="Correo inválido."), 400
    if rol not in VALID_ROLES:
        return jsonify(ok=False, error=f"Rol inválido: {rol}"), 400
    with get_db() as conn:
        if get_user_by_email(conn, email):
            return jsonify(ok=False, error="Ese correo ya existe."), 409
        # RETURNING id: portable a SQLite (>=3.35) y PostgreSQL, y evita
        # depender de cur.lastrowid (que psycopg no expone).
        row = conn.execute(
            "INSERT INTO users (email, nombre, rol) VALUES (?,?,?) "
            "RETURNING id",
            (email, nombre, rol)).fetchone()
        conn.commit()
        new_id = row["id"] if row else None
    # Auditar fuera del `with` (conexión liberada) para no anidar get_db.
    audit("rbac_change", action="create", by=g.user["email"],
          target=email, rol=rol)
    return jsonify(ok=True, id=new_id)


@auth_bp.route("/admin/api/users/<int:user_id>", methods=["PUT"])
@require_role("admin")
def admin_users_update(user_id: int):
    data = request.get_json(silent=True) or {}
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)
        if not user:
            return jsonify(ok=False, error="Usuario no encontrado."), 404
        nombre = (data.get("nombre") if data.get("nombre") is not None
                  else user["nombre"])
        rol = data.get("rol", user["rol"])
        if rol not in VALID_ROLES:
            return jsonify(ok=False, error=f"Rol inválido: {rol}"), 400
        # activo como bool (columna BOOLEAN en PG; INTEGER en SQLite acepta bool).
        activo = (bool(data.get("activo")) if "activo" in data
                  else bool(user["activo"]))
        # Evitar que el admin se desactive/degrade a sí mismo y quede sin admins
        if user["id"] == g.user["id"] and (rol != "admin" or not activo):
            return jsonify(ok=False,
                           error="No puedes quitarte tu propio acceso admin."), 400
        conn.execute("UPDATE users SET nombre=?, rol=?, activo=? WHERE id=?",
                     (nombre, rol, activo, user_id))
        conn.commit()
        target_email = user["email"]
    audit("rbac_change", action="update", by=g.user["email"],
          target=target_email, rol=rol, activo=activo)
    return jsonify(ok=True)


@auth_bp.route("/admin/api/users/<int:user_id>", methods=["DELETE"])
@require_role("admin")
def admin_users_deactivate(user_id: int):
    with get_db() as conn:
        user = get_user_by_id(conn, user_id)
        if not user:
            return jsonify(ok=False, error="Usuario no encontrado."), 404
        if user["id"] == g.user["id"]:
            return jsonify(ok=False, error="No puedes desactivarte a ti mismo."), 400
        conn.execute("UPDATE users SET activo=FALSE WHERE id=?", (user_id,))
        # Revocar todas sus sesiones activas
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
        conn.commit()
        target_email = user["email"]
    audit("rbac_change", action="deactivate", by=g.user["email"],
          target=target_email)
    return jsonify(ok=True)


# ============================================================================
#  Plantillas HTML
# ============================================================================
LOGIN_TEMPLATE = ui_theme.head_open("VerifyData — Acceso") + """<body>
<div class="auth-wrap">
  <div class="auth-card">
    <div class="wordmark wm-light" style="text-align:center;margin-bottom:22px">Verify<span>Data</span></div>
    <h1>Acceso seguro</h1>
    <p class="auth-sub">Ingresa con tu correo corporativo. Te enviaremos un código de un solo uso para verificar tu identidad.</p>
    {% if error %}<div class="auth-msg err">{{ error }}</div>{% endif %}
    <div id="msg" class="auth-msg" style="display:none"></div>

    <div id="step-email">
      <form id="form-email">
        <div class="field">
          <label for="email">Correo corporativo</label>
          <input type="email" id="email" placeholder="tu@empresa.com" required autofocus>
        </div>
        <button class="btn btn-primary" type="submit">Enviar código</button>
      </form>
      {% if sso_enabled %}
      <div class="auth-divider">o</div>
      <a href="{{ url_for('auth.microsoft_login') }}" class="btn btn-secondary">
        <svg width="18" height="18" viewBox="0 0 23 23"><path fill="#f35325" d="M1 1h10v10H1z"/><path fill="#81bc06" d="M12 1h10v10H12z"/><path fill="#05a6f0" d="M1 12h10v10H1z"/><path fill="#ffba08" d="M12 12h10v10H12z"/></svg>
        Continuar con Microsoft
      </a>
      {% endif %}
    </div>

    <div id="step-code" style="display:none">
      <form id="form-code">
        <div class="field">
          <label for="code">Código de 6 dígitos</label>
          <p class="auth-sub" id="sent-to" style="margin-bottom:10px"></p>
          <input type="text" id="code" inputmode="numeric" maxlength="6"
                 pattern="[0-9]{6}" placeholder="000000" required>
        </div>
        <button class="btn btn-primary" type="submit">Verificar y entrar</button>
      </form>
      <div style="margin-top:14px;text-align:center">
        <a class="btn btn-ghost" id="back" style="cursor:pointer">← Usar otro correo</a>
      </div>
    </div>
  </div>
</div>
<script>
const NEXT = {{ next|tojson }};
const msg = document.getElementById('msg');
function show(t, ok){ msg.textContent=t; msg.className='auth-msg '+(ok?'ok':'err'); msg.style.display = t ? 'block' : 'none'; }
function clearMsg(){ msg.textContent=''; msg.className='auth-msg'; msg.style.display='none'; }
let currentEmail='';
document.getElementById('form-email').addEventListener('submit', async e=>{
  e.preventDefault();
  currentEmail=document.getElementById('email').value.trim().toLowerCase();
  const r=await fetch('/auth/otp/request',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({email:currentEmail})});
  const d=await r.json();
  if(!d.ok){ show(d.error||'Error',false); return; }
  document.getElementById('sent-to').textContent='Enviamos un código a '+d.masked;
  document.getElementById('step-email').style.display='none';
  document.getElementById('step-code').style.display='';
  document.getElementById('code').focus();
  clearMsg();
});
document.getElementById('form-code').addEventListener('submit', async e=>{
  e.preventDefault();
  const code=document.getElementById('code').value.trim();
  const r=await fetch('/auth/otp/verify',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:currentEmail,code})});
  const d=await r.json();
  if(!d.ok){ show(d.error||'Error',false); return; }
  window.location.href = NEXT || d.redirect || '/';
});
document.getElementById('back').addEventListener('click',()=>{
  document.getElementById('step-code').style.display='none';
  document.getElementById('step-email').style.display='';
  clearMsg();
});
</script>
</body></html>"""


ADMIN_TEMPLATE = (
    ui_theme.head_open("VerifyData — Usuarios")
    + ui_theme.shell_open("usuarios", "Usuarios y roles",
                          "Sistema &middot; Gestión de acceso")
    + """
  <div class="card pad">
    <p class="kicker">Nuevo usuario</p>
    <p class="section-sub">Solo los usuarios listados aquí (activos) pueden iniciar sesión.</p>
    <div class="field-row">
      <div class="field">
        <label for="n-email">Correo</label>
        <input id="n-email" type="email" placeholder="correo@empresa.com">
      </div>
      <div class="field">
        <label for="n-nombre">Nombre</label>
        <input id="n-nombre" type="text" placeholder="Nombre">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label for="n-rol">Rol</label>
        <select id="n-rol">
          {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
        </select>
      </div>
      <div class="field" style="display:flex;align-items:flex-end">
        <button class="btn btn-primary" onclick="createUser()">Crear usuario</button>
      </div>
    </div>
    <div id="err" style="color:var(--red);font-size:12.5px;margin-top:4px"></div>
  </div>

  <div class="card pad" style="margin-top:20px">
    <table class="dtable">
      <thead><tr>
        <th>Email</th><th>Nombre</th><th>Rol</th><th>Estado</th><th>Método</th>
        <th>Último acceso</th><th></th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
<script>
const ROLES = {{ roles|tojson }};
const ROLE_BADGE = {admin:'b-violeta', analista:'b-azul', viewer:'b-gris'};
async function load(){
  const r=await fetch('/auth/admin/api/users'); const d=await r.json();
  const tb=document.getElementById('rows'); tb.innerHTML='';
  for(const u of d.users){
    const tr=document.createElement('tr'); if(!u.activo) tr.style.opacity='.5';
    const rb=ROLE_BADGE[u.rol]||'b-gris';
    tr.innerHTML=`<td class="name">${u.email}</td><td>${u.nombre||'—'}</td>
      <td><span class="badge ${rb}"><span class="badge-dot"></span>${u.rol}</span></td>
      <td><span class="badge ${u.activo?'b-verde':'b-gris'}"><span class="badge-dot"></span>${u.activo?'Activo':'Inactivo'}</span></td>
      <td>${u.auth_provider}</td>
      <td>${u.last_login||'nunca'}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-sm btn-secondary" onclick='editUser(${JSON.stringify(u)})'>Editar</button>
        ${u.activo?`<button class="btn btn-sm btn-critical" onclick="deactivate(${u.id})">Desactivar</button>`:''}
      </td>`;
    tb.appendChild(tr);
  }
}
async function createUser(){
  const email=document.getElementById('n-email').value.trim();
  const nombre=document.getElementById('n-nombre').value.trim();
  const rol=document.getElementById('n-rol').value;
  const r=await fetch('/auth/admin/api/users',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({email,nombre,rol})});
  const d=await r.json();
  if(!d.ok){ document.getElementById('err').textContent=d.error; return; }
  document.getElementById('n-email').value='';document.getElementById('n-nombre').value='';
  document.getElementById('err').textContent=''; load();
}
async function editUser(u){
  const rol=prompt('Rol ('+ROLES.join(', ')+'):',u.rol); if(!rol) return;
  const nombre=prompt('Nombre:',u.nombre||'');
  const r=await fetch('/auth/admin/api/users/'+u.id,{method:'PUT',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({rol:rol.trim(),nombre,activo:u.activo})});
  const d=await r.json(); if(!d.ok){ alert(d.error); return; } load();
}
async function deactivate(id){
  if(!confirm('¿Desactivar este usuario? Se cerrarán sus sesiones.')) return;
  const r=await fetch('/auth/admin/api/users/'+id,{method:'DELETE'});
  const d=await r.json(); if(!d.ok){ alert(d.error); return; } load();
}
load();
</script>
"""
    + ui_theme.SHELL_CLOSE
)
