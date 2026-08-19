#!/usr/bin/env python3
"""
migrate_sqlite_to_pg.py — Migra los datos de la BD SQLite a PostgreSQL (Bloque B4).

Copia fila a fila con psycopg PARAMETRIZADO. Esto evita a propósito los gotchas
del enfoque `pg_dump`/`psql` que menciona el handoff:
  - Sin problemas de versiones de pg_dump (no se genera dump).
  - Los hashes bcrypt/pbkdf2 con '$' viajan como parámetros, no como SQL, así
    que NO hay dollar-quoting que interpretar.

Qué migra (por defecto):
  - `users`            — CRÍTICO (no perder cuentas). Preserva los id y ajusta
                          la secuencia IDENTITY para que los próximos INSERT no
                          colisionen.
Con --include-lists añade `list_meta` y `list_entries` (re-descargables, no
críticas). Se OMITEN por diseño: `auth_sessions`, `search_runs`, `cert_files`
(efímeras; los usuarios vuelven a iniciar sesión).

Idempotente: usa INSERT ... ON CONFLICT DO NOTHING, así que re-ejecutar no
duplica.

Uso:
    export DATABASE_URL=postgresql://user:pass@host:5432/verifydata
    python3 migrate_sqlite_to_pg.py [--sqlite RUTA] [--include-lists] [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Tablas a migrar en orden (users antes que nada por si en el futuro hay FKs).
CORE_TABLES = ["users"]
LIST_TABLES = ["list_meta", "list_entries"]

# Columnas que en Postgres son BOOLEAN (SQLite las guarda como 0/1) → convertir.
BOOL_COLUMNS = {"users": {"activo"}, "otp_codes": {"used"}}
# Columnas que en Postgres son JSONB (SQLite las guarda como texto) → castear.
JSON_COLUMNS = {"list_entries": {"data"}}


def _sqlite_rows(scon: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Devuelve (columnas, filas) de una tabla SQLite, o ([],[]) si no existe."""
    cur = scon.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if not cur.fetchone():
        return [], []
    cur = scon.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def _migrate_table(scon, pcon, table: str, dry_run: bool) -> int:
    cols, rows = _sqlite_rows(scon, table)
    if not cols:
        print(f"  · {table}: no existe en SQLite, se omite.")
        return 0
    if not rows:
        print(f"  · {table}: 0 filas.")
        return 0
    collist = ", ".join(cols)
    bool_cols = BOOL_COLUMNS.get(table, set())
    json_cols = JSON_COLUMNS.get(table, set())
    # Placeholders: JSONB requiere cast explícito ::jsonb.
    placeholders = ", ".join(
        "%s::jsonb" if c in json_cols else "%s" for c in cols)
    sql = (f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
           "ON CONFLICT DO NOTHING")
    if dry_run:
        print(f"  · {table}: {len(rows)} filas (dry-run, no se escribe).")
        return 0

    bool_idx = [i for i, c in enumerate(cols) if c in bool_cols]

    def _conv(row):
        vals = list(row)
        for i in bool_idx:  # int 0/1 (SQLite) → bool (Postgres)
            if vals[i] is not None:
                vals[i] = bool(vals[i])
        return tuple(vals)

    with pcon.cursor() as pcur:
        pcur.executemany(sql, [_conv(r) for r in rows])
    pcon.commit()
    print(f"  · {table}: {len(rows)} filas migradas (ON CONFLICT DO NOTHING).")
    return len(rows)


def _fix_identity(pcon, table: str) -> None:
    """Ajusta la secuencia IDENTITY de `table.id` a max(id) tras insertar ids
    explícitos, para que los próximos INSERT automáticos no colisionen."""
    with pcon.cursor() as pcur:
        pcur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))")
    pcon.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", help="Ruta a la BD SQLite origen "
                                     "(default: la de config).")
    ap.add_argument("--include-lists", action="store_true",
                    help="Migrar también list_meta y list_entries.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo contar, no escribir.")
    args = ap.parse_args()

    import os
    if not (os.environ.get("DATABASE_URL") or "").startswith(
            ("postgres://", "postgresql://")):
        sys.exit("ERROR: define DATABASE_URL apuntando a PostgreSQL (destino).")

    # Ruta SQLite: argumento o la de config.
    from config import load_config
    cfg = load_config()
    sqlite_path = Path(args.sqlite) if args.sqlite else Path(cfg["database"]["path"])
    if not sqlite_path.exists():
        sys.exit(f"ERROR: no existe la BD SQLite: {sqlite_path}")

    try:
        import psycopg
    except ImportError:
        sys.exit("ERROR: instala psycopg: pip install 'psycopg[binary,pool]>=3.1'")

    # Crear el schema en Postgres (idempotente) antes de copiar.
    import db
    print(f"Origen SQLite : {sqlite_path}")
    print(f"Destino PG    : {os.environ['DATABASE_URL'].split('@')[-1]}")
    print("Creando/verificando schema en PostgreSQL…")
    db.init_db()

    scon = sqlite3.connect(str(sqlite_path))
    # timezone=UTC: las fechas ISO de SQLite (UTC) se interpretan como UTC al
    # castearse a TIMESTAMPTZ, sin depender del timezone del servidor.
    pcon = psycopg.connect(os.environ["DATABASE_URL"],
                           options="-c timezone=UTC")

    tables = list(CORE_TABLES)
    if args.include_lists:
        tables += LIST_TABLES

    print("Migrando…")
    total = 0
    for t in tables:
        total += _migrate_table(scon, pcon, t, args.dry_run)

    # Ajustar secuencias IDENTITY de las tablas con id autoincremental.
    if not args.dry_run:
        for t in ("users",):
            _fix_identity(pcon, t)

    scon.close()
    pcon.close()
    print(f"\nListo. {total} filas migradas." if not args.dry_run
          else "\nDry-run terminado (no se escribió nada).")
    print("Recuerda verificar (checklist B5): login OK, búsqueda que persiste, "
          "y prueba de carga del pool con búsquedas concurrentes.")


if __name__ == "__main__":
    main()
