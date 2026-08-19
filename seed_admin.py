#!/usr/bin/env python3
"""
seed_admin.py — Alta / gestión de usuarios desde la línea de comandos.

Como SOLO los usuarios presentes en la tabla `users` pueden iniciar sesión,
este script sirve para crear el PRIMER admin (que luego administra el resto
desde /auth/admin/users) y para altas rápidas sin UI.

Uso:
    python3 seed_admin.py add  <email> [--nombre "Nombre"] [--rol admin|analista|viewer]
    python3 seed_admin.py list
    python3 seed_admin.py deactivate <email>

Ejemplos:
    python3 seed_admin.py add juan.arias@example.com --nombre "Juan Arias" --rol admin
    python3 seed_admin.py list
"""
from __future__ import annotations

import argparse
import sys

# Carga .env en os.environ (misma mecánica que app.py) y fija la ruta de la BD.
from config import load_config
from db import get_db, set_db_path
from pathlib import Path

CFG = load_config()
set_db_path(Path(CFG["database"]["path"]))

VALID_ROLES = {"admin", "analista", "viewer"}


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def add_user(email: str, nombre: str, rol: str) -> None:
    email = _normalize(email)
    if rol not in VALID_ROLES:
        sys.exit(f"Rol inválido '{rol}'. Usa: {', '.join(sorted(VALID_ROLES))}")
    with get_db() as conn:
        existing = conn.execute("SELECT id, rol, activo FROM users WHERE email=?",
                                (email,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET nombre=?, rol=?, activo=TRUE "
                         "WHERE email=?", (nombre, rol, email))
            conn.commit()
            print(f"↻ Actualizado: {email} (rol={rol}, activo=1)")
        else:
            conn.execute(
                "INSERT INTO users (email, nombre, rol) VALUES (?,?,?)",
                (email, nombre, rol))
            conn.commit()
            print(f"✓ Creado: {email} (rol={rol})")


def list_users() -> None:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, nombre, rol, activo, auth_provider, last_login "
            "FROM users ORDER BY id").fetchall()
    if not rows:
        print("(sin usuarios — crea el primer admin con: add <email> --rol admin)")
        return
    print(f"{'ID':<4}{'EMAIL':<34}{'ROL':<10}{'ACT':<5}{'MÉTODO':<10}ÚLT. ACCESO")
    for r in rows:
        print(f"{r['id']:<4}{r['email']:<34}{r['rol']:<10}"
              f"{'sí' if r['activo'] else 'no':<5}{r['auth_provider']:<10}"
              f"{r['last_login'] or 'nunca'}")


def deactivate_user(email: str) -> None:
    email = _normalize(email)
    with get_db() as conn:
        cur = conn.execute("UPDATE users SET activo=FALSE WHERE email=?", (email,))
        conn.execute("DELETE FROM auth_sessions WHERE user_id="
                     "(SELECT id FROM users WHERE email=?)", (email,))
        conn.commit()
    if cur.rowcount:
        print(f"✓ Desactivado: {email}")
    else:
        print(f"✗ No existe: {email}")


def main() -> None:
    p = argparse.ArgumentParser(description="Gestión de usuarios VerifyData")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="Crear o reactivar un usuario")
    a.add_argument("email")
    a.add_argument("--nombre", default="")
    a.add_argument("--rol", default="admin")

    sub.add_parser("list", help="Listar usuarios")

    d = sub.add_parser("deactivate", help="Desactivar un usuario")
    d.add_argument("email")

    args = p.parse_args()
    if args.cmd == "add":
        add_user(args.email, args.nombre, args.rol)
    elif args.cmd == "list":
        list_users()
    elif args.cmd == "deactivate":
        deactivate_user(args.email)


if __name__ == "__main__":
    main()
