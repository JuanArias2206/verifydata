"""
db.py — Capa de base de datos de VerifyData. Soporta DOS backends:

  • SQLite (por defecto)  — dev/tests, cero dependencias externas.
  • PostgreSQL           — producción, vía `DATABASE_URL` y pool de conexiones.

El backend se elige automáticamente: si `DATABASE_URL` empieza por
`postgres://` / `postgresql://` se usa PostgreSQL (psycopg3 + psycopg_pool);
si no, SQLite en la ruta configurada.

Diseño de la migración (Bloque B del handoff de seguridad)
----------------------------------------------------------
  - Los callers (auth.py, seed_admin.py, lists/manager.py, app.py)
    siguen escribiendo SQL con placeholders `?` y leyendo `row["col"]`. Un shim
    ligero traduce `?`→`%s` y expone filas tipo dict, así la migración NO obliga
    a reescribir todas las queries del proyecto (menos superficie de error y
    dev/CI siguen usando SQLite sin instalar psycopg).
  - PostgreSQL usa un POOL de conexiones obligatorio: `runs.py` lanza 16-32
    workers por búsqueda que consultan la BD (search_entries).
    Abrir una conexión nueva por consulta contra Postgres es carísimo y satura
    el server. Dimensionar el pool con VERIFYDATA_PG_POOL_MAX (ver B5: prueba de
    carga antes de prod).
  - PostgreSQL usa TIPOS NATIVOS (tabla B2 del handoff): TIMESTAMPTZ para
    fechas, BOOLEAN para flags activo/used, JSONB para `data`, IDENTITY para los
    ids. SQLite (tipado dinámico) los guarda como TEXT/INTEGER. El código Python
    lee AMBOS backends de forma agnóstica:
      * fechas: as_naive_utc() normaliza str (SQLite) o datetime aware/naive
        (PG TIMESTAMPTZ) a datetime naive UTC antes de comparar.
      * flags: literales SQL TRUE/FALSE (válidos en ambos) y params bool.
      * `data`: se inserta con ::jsonb en PG; al leer, json.loads solo si llega
        como str. El índice pg_trgm para las búsquedas LIKE/ILIKE va sobre
        name_norm.
    `attempts` sigue como INTEGER (es un contador, no aplica BOOLEAN).

Uso:
    from db import get_db, init_db, upsert_entries, search_entries
    init_db()                              # crea el schema si no existe
    with get_db() as conn:
        upsert_entries(conn, "ofac_sdn", rows)
"""
from __future__ import annotations
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DB_PATH = DATA / "verifydata.db"

# Pool de Postgres (lazy) y guardas de inicialización de schema.
# Dos locks distintos (no reentrantes): uno protege la creación del pool y otro
# la del schema. Deben ser independientes porque _init_pg() necesita el pool.
_pg_pool: Any = None
_pool_lock = threading.Lock()
_schema_lock = threading.Lock()
_pg_schema_ready = False


def set_db_path(path: Path | str) -> None:
    """Fija la ruta de la BD SQLite que usarán init_db()/get_db() por defecto.
    La llama app.py con la ruta resuelta desde config (absoluta, relativa al
    proyecto). Irrelevante cuando el backend es PostgreSQL (usa DATABASE_URL)."""
    global DB_PATH
    DB_PATH = Path(path)


# ==========================================================================
#  Selección de backend
# ==========================================================================
def _database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def is_postgres() -> bool:
    return _database_url().startswith(("postgres://", "postgresql://"))


def _now_sql() -> str:
    """Expresión SQL para el instante actual: now() (TIMESTAMPTZ) en Postgres,
    datetime('now') (TEXT UTC) en SQLite."""
    return "now()" if is_postgres() else "datetime('now')"


def as_naive_utc(v: Any):
    """Normaliza un valor de fecha a `datetime` naive en UTC, aceptando tanto
    str ISO (SQLite) como datetime aware/naive (Postgres TIMESTAMPTZ). Permite
    comparar fechas de forma uniforme entre backends. None → None."""
    from datetime import datetime, timezone
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            return v.astimezone(timezone.utc).replace(tzinfo=None)
        return v
    s = str(v).strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ==========================================================================
#  PostgreSQL: pool + shim de conexión/cursor (traduce ? → %s)
# ==========================================================================
def _translate(sql: str) -> str:
    """Traduce placeholders de SQLite ('?') a los de psycopg ('%s').

    Asunción válida en este proyecto: el SQL no contiene '?' dentro de literales
    ni '%' literales (los comodines LIKE viajan como parámetros, no en el SQL)."""
    return sql.replace("?", "%s")


class _PgCursor:
    """Envuelve un cursor psycopg traduciendo placeholders. Expone la misma
    superficie que un cursor sqlite3 usada por los callers."""

    def __init__(self, cur: Any):
        self._c = cur

    def execute(self, sql: str, params: Any = ()) -> "_PgCursor":
        self._c.execute(_translate(sql), params)
        return self

    def executemany(self, sql: str, seq: Any) -> "_PgCursor":
        self._c.executemany(_translate(sql), seq)
        return self

    def fetchone(self) -> Any:
        return self._c.fetchone()

    def fetchall(self) -> Any:
        return self._c.fetchall()

    @property
    def rowcount(self) -> int:
        return self._c.rowcount

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)


class _PgConn:
    """Envuelve una conexión psycopg para que los callers escritos para sqlite3
    (conn.execute(...).fetchone(), conn.cursor(), conn.commit()) funcionen sin
    cambios. Las filas salen como dict (row_factory=dict_row)."""

    def __init__(self, raw: Any):
        self._raw = raw

    def execute(self, sql: str, params: Any = ()) -> _PgCursor:
        cur = self._raw.cursor()
        cur.execute(_translate(sql), params)
        return _PgCursor(cur)

    def cursor(self) -> _PgCursor:
        return _PgCursor(self._raw.cursor())

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def _pool() -> Any:
    """Crea (una vez) y devuelve el pool de conexiones de Postgres."""
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        try:
            from psycopg_pool import ConnectionPool
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "DATABASE_URL apunta a PostgreSQL pero psycopg no está "
                "instalado. Instala: pip install 'psycopg[binary,pool]>=3.1'"
            ) from exc
        min_size = int(os.environ.get("VERIFYDATA_PG_POOL_MIN", "2"))
        max_size = int(os.environ.get("VERIFYDATA_PG_POOL_MAX", "16"))
        timeout = float(os.environ.get("VERIFYDATA_PG_POOL_TIMEOUT", "30"))
        pool = ConnectionPool(
            conninfo=_database_url(),
            min_size=min_size, max_size=max_size,
            timeout=timeout,
            # timezone=UTC: las columnas TIMESTAMPTZ se interpretan/devuelven en
            # UTC sin ambigüedad (as_naive_utc normaliza al leer).
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=False,
        )
        pool.open(wait=True, timeout=timeout)
        _pg_pool = pool
        return _pg_pool


# ==========================================================================
#  Esquema (init_db) — dialectal
# ==========================================================================
def _schema_statements(pg: bool) -> list[str]:
    """Devuelve las sentencias CREATE TABLE/INDEX para el backend indicado.

    Postgres usa tipos NATIVOS (TIMESTAMPTZ, BOOLEAN, JSONB, IDENTITY); SQLite
    usa TEXT/INTEGER (tipado dinámico). El código Python lee ambos de forma
    agnóstica (as_naive_utc para fechas, truthy para flags, json guard para
    JSONB)."""
    now = _now_sql()
    # Tipos que difieren entre backends.
    autoinc = ("BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
               if pg else "INTEGER PRIMARY KEY AUTOINCREMENT")
    ts = "TIMESTAMPTZ" if pg else "TEXT"
    boolt = "BOOLEAN" if pg else "INTEGER"
    jsont = "JSONB" if pg else "TEXT"
    true_, false_ = ("TRUE", "FALSE") if pg else ("1", "0")
    stmts = [
        # list_entries: filas crudas de listas descargadas (OFAC, UN, etc).
        # id_value NOT NULL DEFAULT '' — en Postgres las columnas de PK no
        # admiten NULL (en SQLite sí); se normaliza a '' en upsert_entries.
        f"""CREATE TABLE IF NOT EXISTS list_entries (
            source      TEXT NOT NULL,
            name_norm   TEXT NOT NULL,
            id_value    TEXT NOT NULL DEFAULT '',
            data        {jsont} NOT NULL,
            fetched_at  {ts} NOT NULL DEFAULT ({now}),
            PRIMARY KEY (source, name_norm, id_value, fetched_at)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_list_source_name "
        "ON list_entries(source, name_norm)",
        "CREATE INDEX IF NOT EXISTS idx_list_id "
        "ON list_entries(source, id_value)",
        f"""CREATE TABLE IF NOT EXISTS list_meta (
            source       TEXT PRIMARY KEY,
            url          TEXT,
            last_fetched {ts},
            last_count   INTEGER DEFAULT 0,
            format       TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS search_runs (
            token      TEXT PRIMARY KEY,
            query      TEXT NOT NULL,
            results    TEXT NOT NULL,
            created_at {ts} NOT NULL DEFAULT ({now})
        )""",
        f"""CREATE TABLE IF NOT EXISTS cert_files (
            source      TEXT NOT NULL,
            subject     TEXT,
            filename    TEXT NOT NULL,
            created_at  {ts} NOT NULL DEFAULT ({now}),
            PRIMARY KEY (filename)
        )""",
        # --- Autenticación (RBAC) --- SOLO emails en `users` (activo) entran.
        f"""CREATE TABLE IF NOT EXISTS users (
            id            {autoinc},
            email         TEXT NOT NULL UNIQUE,
            nombre        TEXT NOT NULL DEFAULT '',
            rol           TEXT NOT NULL DEFAULT 'viewer',
            activo        {boolt} NOT NULL DEFAULT {true_},
            auth_provider TEXT NOT NULL DEFAULT 'otp',
            azure_oid     TEXT,
            last_login    {ts},
            created_at    {ts} NOT NULL DEFAULT ({now})
        )""",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        f"""CREATE TABLE IF NOT EXISTS otp_codes (
            id         {autoinc},
            email      TEXT NOT NULL,
            code_hash  TEXT NOT NULL,
            expires_at {ts} NOT NULL,
            attempts   INTEGER NOT NULL DEFAULT 0,
            used       {boolt} NOT NULL DEFAULT {false_},
            created_at {ts} NOT NULL DEFAULT ({now})
        )""",
        "CREATE INDEX IF NOT EXISTS idx_otp_email "
        "ON otp_codes(email, used, expires_at)",
        f"""CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            expires_at {ts} NOT NULL,
            created_at {ts} NOT NULL DEFAULT ({now}),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user "
        "ON auth_sessions(user_id)",
        # --- otp_rate_limit: rate limit de OTP PERSISTENTE (A6) --------------
        # Sobrevive reinicios y se comparte entre workers/procesos. La clave es
        # (scope, key): scope='ip' o 'email'; window_start marca la ventana.
        f"""CREATE TABLE IF NOT EXISTS otp_rate_limit (
            scope        TEXT NOT NULL,
            rkey         TEXT NOT NULL,
            hits         INTEGER NOT NULL DEFAULT 0,
            window_start {ts} NOT NULL DEFAULT ({now}),
            PRIMARY KEY (scope, rkey)
        )""",
        # --- audit_log: auditoría AML PERSISTENTE (A8) ----------------------
        # Registro inmutable de quién consultó qué y cuándo (Superintendencia).
        # `fields` guarda el detalle estructurado del evento (sin secretos).
        f"""CREATE TABLE IF NOT EXISTS audit_log (
            id       {autoinc},
            ts       {ts} NOT NULL DEFAULT ({now}),
            event    TEXT NOT NULL,
            actor    TEXT,
            fields   {jsont}
        )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
        "CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event)",
        # --- search_run_states: estado de runs progresivos (Vercel serverless) -
        # Persiste el estado de cada run en BD para que sobreviva a cold starts.
        f"""CREATE TABLE IF NOT EXISTS search_run_states (
            token       TEXT PRIMARY KEY,
            query       {jsont} NOT NULL,
            started_at  TEXT NOT NULL,
            completed   {boolt} NOT NULL DEFAULT {false_},
            sources     {jsont} NOT NULL DEFAULT '{{}}',
            last_update REAL NOT NULL DEFAULT 0,
            total       INTEGER NOT NULL DEFAULT 0,
            matched     INTEGER NOT NULL DEFAULT 0,
            captcha     INTEGER NOT NULL DEFAULT 0,
            error       INTEGER NOT NULL DEFAULT 0
        )""",
        # --- nit_run_states: estado de runs por NIT ---
        f"""CREATE TABLE IF NOT EXISTS nit_run_states (
            token            TEXT PRIMARY KEY,
            nit              TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            mode             TEXT NOT NULL DEFAULT 'reps',
            status           TEXT NOT NULL DEFAULT 'resolving',
            error            TEXT,
            empresa          TEXT NOT NULL DEFAULT '',
            empresa_datos    {jsont} NOT NULL DEFAULT '{{}}',
            empresa_token    TEXT,
            tree             {jsont} NOT NULL DEFAULT '[]',
            personas         {jsont} NOT NULL DEFAULT '[]',
            last_update      REAL NOT NULL DEFAULT 0
        )""",
        # --- credit_results: resultados del check integral crediticio ---
        f"""CREATE TABLE IF NOT EXISTS credit_results (
            token       TEXT PRIMARY KEY,
            result      {jsont} NOT NULL,
            created_at  {ts} NOT NULL DEFAULT ({now})
        )""",
        # --- credit_requests: solicitudes de crédito (portfolio) ---
        f"""CREATE TABLE IF NOT EXISTS credit_requests (
            id              {autoinc},
            cedula          TEXT NOT NULL,
            nombre          TEXT NOT NULL,
            tipo_solicitud  TEXT NOT NULL DEFAULT 'SOLICITUD DE CREDITO',
            ejecutivo       TEXT NOT NULL DEFAULT '',
            estado          TEXT NOT NULL DEFAULT 'pendiente',
            monto_solicitado REAL NOT NULL DEFAULT 0,
            credito_actual  REAL NOT NULL DEFAULT 0,
            cupo_inicial    REAL NOT NULL DEFAULT 0,
            promedio_compras REAL NOT NULL DEFAULT 0,
            calificacion    REAL NOT NULL DEFAULT 0,
            presenta_mora   {boolt} NOT NULL DEFAULT {false_},
            cartera_castigada {boolt} NOT NULL DEFAULT {false_},
            score           INTEGER NOT NULL DEFAULT 0,
            nivel_riesgo    TEXT NOT NULL DEFAULT 'NO_EVALUADO',
            observaciones   TEXT,
            aprobado_por    TEXT,
            fecha_aprobacion {ts},
            motivo_rechazo  TEXT,
            created_at      {ts} NOT NULL DEFAULT ({now})
        )""",
        # --- approval_history: historial de aprobaciones/rechazos ---
        f"""CREATE TABLE IF NOT EXISTS approval_history (
            id              {autoinc},
            request_id      INTEGER NOT NULL,
            accion          TEXT NOT NULL,
            ejecutivo       TEXT NOT NULL DEFAULT '',
            motivo          TEXT,
            created_at      {ts} NOT NULL DEFAULT ({now})
        )""",
    ]
    return stmts


def _init_pg() -> None:
    """Crea el schema en PostgreSQL (idempotente) + índice pg_trgm para las
    búsquedas LIKE de listas."""
    global _pg_schema_ready
    if _pg_schema_ready:
        return
    pool = _pool()  # fuera del _schema_lock: _pool() usa su propio lock
    with _schema_lock:
        if _pg_schema_ready:
            return
        with pool.connection() as raw:
            cur = raw.cursor()
            for stmt in _schema_statements(pg=True):
                cur.execute(stmt)
            # pg_trgm: acelera `name_norm LIKE '%x%'` con índice GIN. Requiere
            # privilegio para CREATE EXTENSION; si falla, la búsqueda sigue
            # funcionando (seq scan) — se avisa pero no se aborta el arranque.
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_list_name_trgm "
                    "ON list_entries USING gin (name_norm gin_trgm_ops)")
            except Exception:  # noqa: BLE001
                raw.rollback()
                import logging
                logging.getLogger("verifydata.db").warning(
                    "No se pudo crear pg_trgm/índice GIN (¿falta privilegio "
                    "CREATE EXTENSION?). La búsqueda LIKE funcionará sin índice.",
                    exc_info=True)
            raw.commit()
        _pg_schema_ready = True


def init_db(path: Path | None = None) -> sqlite3.Connection | None:
    """Crea las tablas si no existen. Idempotente. Devuelve la conexión SQLite
    (compatibilidad) o None en PostgreSQL."""
    if is_postgres():
        _init_pg()
        return None
    if path is None:
        path = DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()
    for stmt in _schema_statements(pg=False):
        cur.execute(stmt)
    conn.commit()
    return conn


@contextmanager
def get_db(path: Path | None = None) -> Iterator[Any]:
    """Context manager que entrega una conexión lista para usar.

    - PostgreSQL: toma una conexión del pool (la devuelve al salir; commit si no
      hubo excepción). Se expone envuelta para traducir '?'→'%s'.
    - SQLite: abre una conexión nueva a la ruta configurada (como siempre)."""
    if is_postgres():
        _init_pg()
        pool = _pool()
        with pool.connection() as raw:
            yield _PgConn(raw)
        return
    if path is None:
        path = DB_PATH
    init_db(path)  # idempotente, crea schema si no existe
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


# ==========================================================================
#  Operaciones de datos (idénticas para ambos backends salvo dialecto)
# ==========================================================================
def upsert_entries(conn: Any, source: str,
                   entries: Iterable[dict], name_key: str = "name",
                   id_key: str | None = None) -> int:
    """Inserta/actualiza entradas de lista. Devuelve cuántas procesó."""
    from sources.local_lists import normalize
    cur = conn.cursor()
    pg = is_postgres()
    n = 0
    for e in entries:
        name = e.get(name_key) or ""
        if not name:
            continue
        # id_value NOT NULL en el schema (PK): None → '' en ambos backends.
        nid = (e.get(id_key) if id_key else None) or ""
        if pg:
            # INSERT OR REPLACE no existe en Postgres → ON CONFLICT DO UPDATE
            # sobre la PK completa (data es determinista a partir de la entrada).
            # data es JSONB: el string JSON se castea con ::jsonb.
            cur.execute(
                "INSERT INTO list_entries(source,name_norm,id_value,data) "
                "VALUES (?,?,?,?::jsonb) "
                "ON CONFLICT (source,name_norm,id_value,fetched_at) "
                "DO UPDATE SET data=excluded.data",
                (source, normalize(name), nid, _json(e)))
        else:
            cur.execute(
                "INSERT OR REPLACE INTO "
                "list_entries(source,name_norm,id_value,data) VALUES (?,?,?,?)",
                (source, normalize(name), nid, _json(e)))
        n += 1
    conn.commit()
    return n


def search_entries(conn: Any, source: str,
                   name_norm: str, tokens: list[str] | None = None,
                   limit: int = 30) -> list[dict]:
    """Busca entradas. Si se pasan tokens, exige que todos estén en name_norm
    (LIKE por cada token). Si no, LIKE simple por el name_norm completo.

    En Postgres el índice GIN pg_trgm sobre name_norm acelera estos LIKE."""
    import json
    cur = conn.cursor()
    # PostgreSQL LIKE es case-sensitive; SQLite es case-insensitive. Se usa
    # ILIKE en Postgres para preservar exactamente la semántica original (el
    # índice GIN pg_trgm también acelera ILIKE).
    like = "ILIKE" if is_postgres() else "LIKE"
    if tokens:
        where = " AND ".join([f"name_norm {like} ?"] * len(tokens))
        params: list[Any] = [source] + [f"%{t}%" for t in tokens] + [limit]
        cur.execute(f"SELECT data FROM list_entries WHERE source=? AND {where} "
                    "LIMIT ?", params)
    else:
        cur.execute(f"SELECT data FROM list_entries WHERE source=? AND "
                    f"name_norm {like} ? LIMIT ?",
                    [source, f"%{name_norm}%", limit])
    # En Postgres (JSONB) `data` ya llega como dict/list; en SQLite es str.
    out = []
    for r in cur.fetchall():
        d = r["data"]
        out.append(json.loads(d) if isinstance(d, (str, bytes)) else d)
    return out


def list_meta_get(conn: Any, source: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM list_meta WHERE source=?", (source,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_meta_set(conn: Any, source: str, *,
                  url: str | None = None, count: int | None = None,
                  format: str | None = None) -> None:
    now = _now_sql()
    cur = conn.cursor()
    # ON CONFLICT ... DO UPDATE / excluded es compatible con SQLite y Postgres.
    cur.execute(f"""
    INSERT INTO list_meta(source,url,last_fetched,last_count,format)
    VALUES (?,?,{now},?,?)
    ON CONFLICT(source) DO UPDATE SET
        url=COALESCE(excluded.url, list_meta.url),
        last_fetched={now},
        last_count=COALESCE(excluded.last_count, list_meta.last_count),
        format=COALESCE(excluded.format, list_meta.format)
    """, (source, url, count, format))
    conn.commit()


def _json(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, default=str)


# ── A6: rate limit de OTP PERSISTENTE ────────────────────────────────────────
def otp_rate_hit(scope: str, key: str, window_seconds: int) -> int:
    """Registra un intento de OTP para (scope, key) y devuelve cuántos van en la
    ventana actual. Persistente (sobrevive reinicios, se comparte entre workers).
    Ventana deslizante por reinicio: si el registro es más viejo que la ventana,
    el contador se reinicia a 1. Atómico en una sola sentencia (ON CONFLICT)."""
    from datetime import datetime, timedelta, timezone
    now_expr = _now_sql()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    cutoff = cutoff_dt if is_postgres() else cutoff_dt.strftime(
        "%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        row = conn.execute(
            f"INSERT INTO otp_rate_limit (scope, rkey, hits, window_start) "
            f"VALUES (?, ?, 1, {now_expr}) "
            f"ON CONFLICT (scope, rkey) DO UPDATE SET "
            f"  hits = CASE WHEN otp_rate_limit.window_start < ? "
            f"              THEN 1 ELSE otp_rate_limit.hits + 1 END, "
            f"  window_start = CASE WHEN otp_rate_limit.window_start < ? "
            f"      THEN {now_expr} ELSE otp_rate_limit.window_start END "
            f"RETURNING hits",
            (scope, key, cutoff, cutoff)).fetchone()
        conn.commit()
        return int(row["hits"]) if row else 1


# ── A8: auditoría AML PERSISTENTE ────────────────────────────────────────────
def audit_write(event: str, actor: str | None, fields: dict | None) -> None:
    """Persiste un evento de auditoría en audit_log. Best-effort: si la BD
    falla, no debe romper la petición (el llamador captura)."""
    import json as _j
    payload = _j.dumps(fields or {}, ensure_ascii=False, default=str)
    with get_db() as conn:
        if is_postgres():
            conn.execute("INSERT INTO audit_log (event, actor, fields) "
                         "VALUES (?,?,?::jsonb)", (event, actor, payload))
        else:
            conn.execute("INSERT INTO audit_log (event, actor, fields) "
                         "VALUES (?,?,?)", (event, actor, payload))
        conn.commit()


def audit_purge(retention_days: int) -> int:
    """Borra eventos de audit_log más viejos que retention_days. Devuelve
    cuántos borró. retention_days<=0 desactiva la purga."""
    if retention_days <= 0:
        return 0
    from datetime import datetime, timedelta, timezone
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff = cutoff_dt if is_postgres() else cutoff_dt.strftime(
        "%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cur = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        conn.commit()
        return cur.rowcount or 0


# ══════════════════════════════════════════════════════════════════════════════
#  Run state persistence (Vercel serverless — state in DB, not in-memory dict)
# ══════════════════════════════════════════════════════════════════════════════
import json as _json_mod


def run_state_save(token: str, query: dict, started_at: str, completed: bool,
                   sources: dict, last_update: float, total: int, matched: int,
                   captcha: int, error: int) -> None:
    """Persiste o actualiza el estado de un run en search_run_states."""
    src_json = _json_mod.dumps(sources, ensure_ascii=False, default=str)
    q_json = _json_mod.dumps(query, ensure_ascii=False, default=str)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO search_run_states "
            "(token,query,started_at,completed,sources,last_update,total,"
            "matched,captcha,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET "
            "sources=excluded.sources, completed=excluded.completed, "
            "last_update=excluded.last_update, total=excluded.total, "
            "matched=excluded.matched, captcha=excluded.captcha, "
            "error=excluded.error",
            (token, q_json, started_at, 1 if completed else 0,
             src_json, last_update, total, matched, captcha, error))
        conn.commit()


def run_state_get(token: str) -> dict | None:
    """Lee el estado de un run desde search_run_states."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM search_run_states WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["query"] = _json_mod.loads(d["query"]) if isinstance(d["query"], str) else d["query"]
        d["sources"] = _json_mod.loads(d["sources"]) if isinstance(d["sources"], str) else d["sources"]
        d["completed"] = bool(d["completed"])
        return d


def nit_run_state_save(token: str, nit: str, started_at: str, mode: str,
                       status: str, error: str | None, empresa: str,
                       empresa_datos: dict, empresa_token: str | None,
                       tree: list, personas: list, last_update: float) -> None:
    """Persiste o actualiza el estado de un run-NIT en nit_run_states."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO nit_run_states "
            "(token,nit,started_at,mode,status,error,empresa,empresa_datos,"
            "empresa_token,tree,personas,last_update) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET "
            "status=excluded.status, error=excluded.error, "
            "empresa=excluded.empresa, empresa_datos=excluded.empresa_datos, "
            "empresa_token=excluded.empresa_token, tree=excluded.tree, "
            "personas=excluded.personas, last_update=excluded.last_update",
            (token, nit, started_at, mode, status, error, empresa,
             _json_mod.dumps(empresa_datos, ensure_ascii=False, default=str),
             empresa_token,
             _json_mod.dumps(tree, ensure_ascii=False, default=str),
             _json_mod.dumps(personas, ensure_ascii=False, default=str),
             last_update))
        conn.commit()


def nit_run_state_get(token: str) -> dict | None:
    """Lee el estado de un run-NIT desde nit_run_states."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM nit_run_states WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("empresa_datos", "tree", "personas"):
            if isinstance(d.get(key), str):
                d[key] = _json_mod.loads(d[key])
        return d


# ═══════════════════════════════════════════════════════════════════
#  Credit Results persistence
# ═══════════════════════════════════════════════════════════════════
def credit_result_save(token: str, result: dict) -> None:
    """Guarda el resultado del check integral en credit_results."""
    import json
    result_json = json.dumps(result, ensure_ascii=False, default=str)
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO credit_results (token, result) VALUES (?, ?)",
            (token, result_json))
        conn.commit()


def credit_result_get(token: str) -> dict | None:
    """Lee el resultado del check integral desde credit_results."""
    import json
    with get_db() as conn:
        row = conn.execute(
            "SELECT result FROM credit_results WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        return result


# ═══════════════════════════════════════════════════════════════════
#  Credit Requests (Portfolio)
# ═══════════════════════════════════════════════════════════════════
def credit_request_save(cedula: str, nombre: str, tipo_solicitud: str,
                        ejecutivo: str, monto_solicitado: float,
                        credito_actual: float, cupo_inicial: float,
                        promedio_compras: float, calificacion: float,
                        presenta_mora: bool, cartera_castigada: bool,
                        score: int, nivel_riesgo: str,
                        observaciones: str = "") -> int:
    """Guarda una solicitud de crédito. Devuelve el ID."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO credit_requests
               (cedula,nombre,tipo_solicitud,ejecutivo,monto_solicitado,
                credito_actual,cupo_inicial,promedio_compras,calificacion,
                presenta_mora,cartera_castigada,score,nivel_riesgo,observaciones)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cedula, nombre, tipo_solicitud, ejecutivo, monto_solicitado,
             credito_actual, cupo_inicial, promedio_compras, calificacion,
             1 if presenta_mora else 0, 1 if cartera_castigada else 0,
             score, nivel_riesgo, observaciones))
        conn.commit()
        return cur.lastrowid


def credit_request_approve(request_id: int, ejecutivo: str, motivo: str = "") -> None:
    """Aprueba una solicitud de crédito."""
    with get_db() as conn:
        conn.execute(
            """UPDATE credit_requests SET estado='aprobado',
               aprobado_por=?, fecha_aprobacion=datetime('now')
               WHERE id=?""", (ejecutivo, request_id))
        conn.execute(
            """INSERT INTO approval_history (request_id, accion, ejecutivo, motivo)
               VALUES (?,'aprobacion',?,?)""", (request_id, ejecutivo, motivo))
        conn.commit()


def credit_request_reject(request_id: int, ejecutivo: str, motivo: str) -> None:
    """Rechaza una solicitud de crédito."""
    with get_db() as conn:
        conn.execute(
            """UPDATE credit_requests SET estado='rechazado',
               aprobado_por=?, motivo_rechazo=?, fecha_aprobacion=datetime('now')
               WHERE id=?""", (ejecutivo, motivo, request_id))
        conn.execute(
            """INSERT INTO approval_history (request_id, accion, ejecutivo, motivo)
               VALUES (?,'rechazo',?,?)""", (request_id, ejecutivo, motivo))
        conn.commit()


def credit_request_revert(request_id: int, ejecutivo: str, motivo: str = "") -> None:
    """Revierte una solicitud a pendiente (quita aprobación/rechazo). Para Jefe Cartera."""
    with get_db() as conn:
        conn.execute(
            """UPDATE credit_requests SET estado='pendiente',
               aprobado_por=NULL, motivo_rechazo=NULL, fecha_aprobacion=NULL
               WHERE id=?""", (request_id,))
        conn.execute(
            """INSERT INTO approval_history (request_id, accion, ejecutivo, motivo)
               VALUES (?,'reversion',?,?)""", (request_id, ejecutivo, motivo or "Revertido a pendiente"))
        conn.commit()


def credit_request_toggle(request_id: int, ejecutivo: str, nuevo_estado: str, motivo: str = "") -> None:
    """Cambia estado a aprobado/rechazado/pendiente genérico. Jefe Cartera."""
    if nuevo_estado not in ("pendiente", "aprobado", "rechazado"):
        raise ValueError(f"Estado inválido: {nuevo_estado}")
    with get_db() as conn:
        if nuevo_estado == "pendiente":
            conn.execute(
                """UPDATE credit_requests SET estado='pendiente',
                   aprobado_por=NULL, motivo_rechazo=NULL, fecha_aprobacion=NULL WHERE id=?""",
                (request_id,))
        elif nuevo_estado == "aprobado":
            conn.execute(
                """UPDATE credit_requests SET estado='aprobado', aprobado_por=?, fecha_aprobacion=datetime('now'), motivo_rechazo=NULL WHERE id=?""",
                (ejecutivo, request_id))
        else:  # rechazado
            conn.execute(
                """UPDATE credit_requests SET estado='rechazado', aprobado_por=?, motivo_rechazo=?, fecha_aprobacion=datetime('now') WHERE id=?""",
                (ejecutivo, motivo, request_id))
        conn.execute(
            """INSERT INTO approval_history (request_id, accion, ejecutivo, motivo)
               VALUES (?,?,?,?)""", (request_id, f"cambio_a_{nuevo_estado}", ejecutivo, motivo))
        conn.commit()


def credit_request_get_all(estado: str = None) -> list[dict]:
    """Lista todas las solicitudes de crédito."""
    with get_db() as conn:
        if estado:
            rows = conn.execute(
                "SELECT * FROM credit_requests WHERE estado=? ORDER BY created_at DESC",
                (estado,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM credit_requests ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def credit_request_get(request_id: int) -> dict | None:
    """Obtiene una solicitud de crédito por ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM credit_requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None


def approval_history_get(request_id: int) -> list[dict]:
    """Historial de aprobaciones de una solicitud."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM approval_history WHERE request_id=? ORDER BY created_at DESC",
            (request_id,)).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    backend = "PostgreSQL" if is_postgres() else "SQLite"
    init_db()
    if is_postgres():
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT tablename FROM pg_tables "
                        "WHERE schemaname='public' ORDER BY tablename")
            print(f"DB ({backend}) inicializada.")
            print("Tablas:", [r["tablename"] for r in cur.fetchall()])
    else:
        print(f"DB ({backend}) inicializada en {DB_PATH}")
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "ORDER BY name")
            print("Tablas:", [r["name"] for r in cur.fetchall()])
