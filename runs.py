"""
runs.py — Sistema de runs progresivos (streaming).

En lugar de esperar a que TODAS las fuentes terminen para mostrar
el HTML, este sistema:
  - POST crea un run con token
  - Cada fuente actualiza su resultado en la DB
  - El frontend hace polling a /api/run/<token>
  - Las fuentes se muestran a medida que van terminando

También incluye:
  - /api/screenshot/<path> - sirve los screenshots de Playwright
  - /api/refresh-lists - actualiza las listas estáticas
"""
from __future__ import annotations
import json
import secrets
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import sys
import os

# Hacer accesibles los módulos
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from concurrent.futures import ThreadPoolExecutor

# ── Persistencia en BD (para Vercel serverless — state must survive cold starts) ─
try:
    from db import (run_state_save, run_state_get,
                    nit_run_state_save, nit_run_state_get)
    _HAS_DB_PERSIST = True
except ImportError:
    _HAS_DB_PERSIST = False

# ── Gobernadores de concurrencia (servicio MULTI-USUARIO) ────────────────
# El servicio puede ser usado por varios usuarios a la vez. Cada fuente corre
# en su PROPIO subproceso; sin límite, N usuarios × 62 fuentes = 62·N procesos
# (muchos lanzando Chromium) → agota CPU/RAM. Estos semáforos GLOBALES viven en
# el proceso Flask único y acotan el total concurrente entre TODOS los runs:
#   - _GLOBAL_SEM   : tope de subprocesos de fuente simultáneos (todos los users)
#   - _BROWSER_SEM  : tope de fuentes PESADAS (Chromium/captcha) simultáneas
#   - _PER_RUN_WORKERS: hilos por run (evita 62 hilos/run; el resto encola)
# Ajustables por variable de entorno para el despliegue.
_CPU = os.cpu_count() or 4
# Tope TOTAL de subprocesos de fuente simultáneos (todos los runs/usuarios).
# Las fuentes livianas son HTTP corto y baratas; el recurso caro (Chromium) lo
# acota aparte _BROWSER_SEM. Este tope se subió (antes cpu+4) para que la
# búsqueda por NIT —3 personas = 3 runs a la vez, cada uno con
# _PER_RUN_WORKERS— pueda despachar en paralelo las fuentes livianas de las 3
# sin que el primer run acapare los slots (era la causa de que la 1ª persona
# avanzara y las otras se estancaran). Override: VERIFYDATA_MAX_CONCURRENCY.
_MAX_TOTAL_SUBPROCS = int(os.environ.get(
    "VERIFYDATA_MAX_CONCURRENCY", str(max(16, _CPU * 2 + 4))))
# Tope de fuentes PESADAS (Chromium/captcha) simultáneas. CLAVE para el tiempo
# de respuesta, sobre todo en la búsqueda por NIT (varias personas a la vez):
# las fuentes con reCAPTCHA (Contraloría ~159s, Policía) pasan la
# MAYOR parte del tiempo ESPERANDO al solver externo (2captcha/CapSolver hacen
# polling de red 60-90s) — el navegador queda casi ocioso. Por eso el cuello de
# botella es la latencia I/O del solver, NO la CPU local: se pueden tener más
# navegadores que núcleos sin thrashing, y así más captchas se resuelven en
# PARALELO. El default histórico (3) serializaba en exceso (p.ej. 3 personas
# competían por 3 slots). Escalamos con los núcleos, acotado, y siempre
# override-able por entorno para ajustarlo a la RAM del servidor (~250MB/Chromium).
_DEFAULT_MAX_BROWSER = min(8, max(3, _CPU // 2 + 1))
_MAX_BROWSER_SUBPROCS = int(os.environ.get("VERIFYDATA_MAX_BROWSER", str(_DEFAULT_MAX_BROWSER)))
_PER_RUN_WORKERS = int(os.environ.get("VERIFYDATA_PER_RUN_WORKERS", "8"))
_GLOBAL_SEM = threading.BoundedSemaphore(_MAX_TOTAL_SUBPROCS)
_BROWSER_SEM = threading.BoundedSemaphore(_MAX_BROWSER_SUBPROCS)


# ── Búsqueda POR EMPRESA — subconjunto curado de fuentes ─────────────────────
# La búsqueda por NIT en modo "empresa" (estilo tusdatos) consulta a la COMPAÑÍA
# directamente (razón social + NIT), no a una persona. Reutilizamos el mismo
# motor `run_search_progressive` pero con un subconjunto: se EXCLUYEN las fuentes
# que solo tienen sentido para una PERSONA NATURAL (identidad/antecedentes por
# cédula) y que, para un NIT, o bien no aplican o bien son navegadores pesados
# que romperían (p.ej. la trivia de Procuraduría se resuelve con el NOMBRE de una
# persona). El resto de fuentes (registro mercantil, Supersociedades, sanciones,
# listas restrictivas, judicial por NIT/razón, contratación, boletines, PEP,
# fugitivos y noticias) SÍ se consultan — igual que el reporte por empresa de
# tusdatos, que las lista todas aunque muchas devuelvan "no encontrado".
# Se modela como lista de EXCLUSIÓN (no allowlist) para que fuentes nuevas que
# apliquen a empresas se incluyan por defecto; revisar al agregar una fuente
# estrictamente de persona natural.
EMPRESA_EXCLUDED_SOURCES = frozenset({
    "Registraduría — Estado de cédula",                 # estado de cédula (NUIP)
    "Registraduría — Defunciones (estado de la cédula)",# defunción de persona
    "Policía Nacional — Antecedentes Judiciales",        # antecedentes por cédula
    "Policía — Delitos Sexuales contra Menores",         # registro persona natural
    "Policia Nacional — Inhabilidades por delitos sexuales (Ley 1918)",  # persona
    "Procuraduría — Antecedentes Disciplinarios",        # trivia por nombre-persona
    "SIRNA — Registro Nacional de Abogados",             # abogados (persona)
    "SIGEP — Función Pública Colombia (browser)",        # funcionarios (persona)
    "JEPMS — Juzgados de Ejecución de Penas y Medidas de Seguridad",     # persona
    "Pérdida de Investidura de Congresistas (Consejo de Estado)",        # persona
})


def empresa_source_names() -> list[str]:
    """Nombres de las fuentes a consultar en la búsqueda POR EMPRESA
    (todas las registradas menos las estrictamente de persona natural)."""
    from sources import registry
    return [s.name for s in registry.all_sources()
            if s.name not in EMPRESA_EXCLUDED_SOURCES]


def _is_heavy_source(src) -> bool:
    """True si la fuente lanza navegador/captcha (recurso pesado que debe
    limitarse aparte para no abrir decenas de Chromium a la vez)."""
    n = (getattr(src, "name", "") or "").lower()
    if getattr(src, "requires_captcha", False):
        return True
    if "(browser)" in n:
        return True
    return any(k in n for k in ("rues", "registrad", "rut —",
                                "supersociedades", "sigep"))


# ── Cache de resultados por consulta (reduce carga con usuarios simultáneos) ─
# Si varios usuarios consultan la MISMA cédula en una ventana corta (o alguien
# recarga/re-envía), reutilizamos el run recién completado en vez de relanzar
# 62 subprocesos. TTL configurable; 0 desactiva el cache.
_RESULT_CACHE: dict = {}          # key -> (token, completed_ts)
_RESULT_CACHE_TTL_S = int(os.environ.get("VERIFYDATA_CACHE_TTL_S", "300"))
_CACHE_LOCK = threading.Lock()


def _cache_key(query: dict, cedula, fecha_exp, sources_mode) -> str:
    nombre = (query.get("nombre", "") if isinstance(query, dict) else "") or ""
    # Normalizar un subconjunto explícito (lista) a una clave estable e
    # independiente del orden, para que dos peticiones con las mismas fuentes
    # en distinto orden reutilicen el mismo run cacheado.
    if isinstance(sources_mode, (list, tuple, set)):
        sm = "custom:" + ",".join(sorted(str(x) for x in sources_mode))
    else:
        sm = str(sources_mode)
    return (f"{(cedula or '').strip()}|{(fecha_exp or '').strip()}|"
            f"{sm}|{nombre.strip().lower()}")


def _cache_lookup(key: str) -> str | None:
    if _RESULT_CACHE_TTL_S <= 0:
        return None
    with _CACHE_LOCK:
        entry = _RESULT_CACHE.get(key)
    if not entry:
        return None
    token, ts = entry
    if (time.time() - ts) > _RESULT_CACHE_TTL_S:
        return None
    st = _RUNS.get(token)
    return token if (st and st.completed) else None


def _cache_store(key: str, token: str) -> None:
    with _CACHE_LOCK:
        _RESULT_CACHE[key] = (token, time.time())
        # Bound: quedarse con las 200 entradas más recientes
        if len(_RESULT_CACHE) > 200:
            for k in sorted(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k][1])[:-200]:
                _RESULT_CACHE.pop(k, None)


@dataclass
class RunState:
    token: str
    query: dict
    started_at: str
    completed: bool = False
    sources: dict = field(default_factory=dict)   # name -> dict
    last_update: float = 0.0
    total: int = 0
    matched: int = 0
    captcha: int = 0
    error: int = 0

    def to_dict(self) -> dict:
        import copy
        return {
            "token": self.token,
            "query": copy.deepcopy(self.query),
            "started_at": self.started_at,
            "completed": self.completed,
            "sources": copy.deepcopy(self.sources),
            "last_update": self.last_update,
            "total": self.total,
            "matched": self.matched,
            "captcha": self.captcha,
            "error": self.error,
        }


# Estado global de runs
_RUNS: dict[str, RunState] = {}


def create_run(query: dict, total_sources: int) -> RunState:
    """Crea un nuevo run y devuelve su estado."""
    token = secrets.token_urlsafe(24)
    state = RunState(
        token=token,
        query=query,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total=total_sources,
    )
    _RUNS[token] = state
    # Persistir en BD si está disponible
    if _HAS_DB_PERSIST:
        try:
            run_state_save(token, query, state.started_at, False,
                          {}, state.last_update, total_sources, 0, 0, 0)
        except Exception:
            pass
    # Limpieza: máximo 200 runs en memoria (evita rotación prematura)
    while len(_RUNS) > 200:
        _RUNS.pop(next(iter(_RUNS)))
    return state


def get_run(token: str) -> RunState | None:
    state = _RUNS.get(token)
    if state:
        return state
    # Si no está en memoria, intentar leer de SQLite (Vercel cold start)
    if _HAS_DB_PERSIST:
        try:
            d = run_state_get(token)
            if d:
                state = RunState(
                    token=d["token"], query=d["query"],
                    started_at=d["started_at"], completed=d["completed"],
                    sources=d["sources"], last_update=d["last_update"],
                    total=d["total"], matched=d["matched"],
                    captcha=d["captcha"], error=d["error"])
                _RUNS[token] = state
                return state
        except Exception:
            pass
    return None


# --- Persistencia del último run (sobrevive reinicios del servidor) -------
_LAST_RUN_FILE = ROOT / "data" / "last_run.json"


def _persist_last_run(state: "RunState") -> None:
    """Guarda el run en data/last_run.json para que sobreviva a reinicios
    del servidor (Flask no recarga estado en memoria al reiniciar)."""
    try:
        _LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_RUN_FILE.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _restore_last_run() -> None:
    """Al iniciar, recarga el último run persistido en la caché en memoria,
    de modo que su token siga siendo consultable en /results/<token> tras un
    reinicio (y el usuario vea de inmediato el nuevo layout)."""
    try:
        if not _LAST_RUN_FILE.exists():
            return
        d = json.loads(_LAST_RUN_FILE.read_text(encoding="utf-8"))
        tok = d.get("token")
        if not tok or tok in _RUNS:
            return
        st = RunState(
            token=tok, query=d.get("query", {}),
            started_at=d.get("started_at", ""),
            completed=d.get("completed", True),
            sources=d.get("sources", {}),
            last_update=d.get("last_update", 0.0),
            total=d.get("total", 0), matched=d.get("matched", 0),
            captcha=d.get("captcha", 0), error=d.get("error", 0))
        _RUNS[tok] = st
    except Exception:
        pass


def update_source(token: str, source_name: str, hit_dict: dict) -> None:
    """Actualiza el resultado de una fuente en un run."""
    state = _RUNS.get(token)
    if not state: return
    state.sources[source_name] = hit_dict
    state.last_update = time.time()
    # Recalcular contadores
    state.matched = sum(1 for h in state.sources.values() if h.get("matched"))
    state.captcha = sum(1 for h in state.sources.values() if h.get("captcha_required"))
    state.error = sum(1 for h in state.sources.values() if h.get("error"))
    # Persistir en BD si está disponible
    if _HAS_DB_PERSIST:
        try:
            run_state_save(token, state.query, state.started_at,
                          state.completed, state.sources, state.last_update,
                          state.total, state.matched, state.captcha, state.error)
        except Exception:
            pass


def mark_completed(token: str) -> None:
    state = _RUNS.get(token)
    if state:
        state.completed = True
        state.last_update = time.time()
        _persist_last_run(state)
        # Persistir en BD si está disponible
        if _HAS_DB_PERSIST:
            try:
                run_state_save(token, state.query, state.started_at, True,
                              state.sources, state.last_update, state.total,
                              state.matched, state.captcha, state.error)
            except Exception:
                pass


def _clean(s):
    """Sanitiza string para JSON: quita control chars problemáticos."""
    if s is None or not isinstance(s, str):
        return s
    # Reemplazar control chars que rompen JSON parsing
    return (s.replace("\x00", "").replace("\x0c", " ")
              .replace("\r", " ").replace("\v", " "))


def hit_to_dict(hit) -> dict:
    """Convierte un Hit a dict serializable."""
    return {
        "source": _clean(hit.source),
        "matched": hit.matched,
        "summary": _clean(hit.summary),
        "details": hit.details,
        "error": _clean(hit.error),
        "notice": _clean(hit.notice),
        "download_url": hit.download_url,
        "elapsed_s": hit.elapsed_s,
        "captcha_required": hit.captcha_required,
        "evidence_urls": hit.evidence_urls,
        "source_url": getattr(hit, "source_url", ""),
        # Evidencia estructurada (v0.7)
        "status": getattr(hit, "status", None),
        "confidence": getattr(hit, "confidence", None),
        "matched_name": _clean(getattr(hit, "matched_name", None)),
        "matched_document": _clean(getattr(hit, "matched_document", None)),
        "role": _clean(getattr(hit, "role", None)),
        "case_number": _clean(getattr(hit, "case_number", None)),
        "dataset_version": getattr(hit, "dataset_version", None),
        "dataset_records": getattr(hit, "dataset_records", 0) or 0,
        "error_type": getattr(hit, "error_type", None),
        "requires_manual_review": getattr(hit, "requires_manual_review", False),
        "notes": _clean(getattr(hit, "notes", None)),
    }


def run_search_progressive(query: dict, cedula: str | None,
                          fecha_exp: str | None, solver,
                          skip_browser: bool = False) -> str:
    """Lanza la búsqueda en background y devuelve el token.
    El caller debe hacer polling a /api/run/<token>."""
    from sources import registry
    from sources.base import safe_fetch
    sources = registry.all_sources()
    # Si skip_browser=True, filtrar (legacy)
    if skip_browser:
        sources = [s for s in sources if "(browser)" not in s.name]
    # Si el query pide mode=featured, FILTRAR a solo las 6 featured.
    # Default = "all" (las 64 fuentes) porque la app está pensada para
    # consultar TODAS las fuentes públicas en un solo run.
    sources_mode = query.get("__sources", "all") if isinstance(query, dict) else "all"
    FEATURED_NAMES = {
        "Registraduría — Estado de cédula",
        "Registraduría — Defunciones (estado de la cédula)",
        "Policía Nacional — Antecedentes Judiciales",
        "Policía Nacional — Inhabilidades por delitos sexuales (Ley 1918)",
        "Contraloría General — Responsabilidad Fiscal",
        "DIAN — Proveedores Ficticios (Boletín)",
        "RUT — DIAN (Registro Único Tributario)",
        "Procuraduría — Antecedentes Disciplinarios",
        "OFAC — Sanctions List Search (form web oficial)",
        "EUROPOL — Most Wanted Fugitives",
    }
    if sources_mode == "featured":
        sources = [s for s in sources if s.name in FEATURED_NAMES]
    elif isinstance(sources_mode, (list, tuple, set)):
        # Subconjunto EXPLÍCITO por nombre (usado por la API pública, que
        # permite al cliente elegir exactamente qué fuentes consultar).
        wanted = {str(x) for x in sources_mode}
        sources = [s for s in sources if s.name in wanted]
    # Cache: si la MISMA consulta se completó hace poco, reutilizar el run
    # (evita relanzar 62 subprocesos si varios usuarios piden lo mismo o
    # alguien reenvía el formulario).
    ckey = _cache_key(query, cedula, fecha_exp, sources_mode)
    cached = _cache_lookup(ckey)
    if cached:
        return cached
    # state.total refleja SOLO las fuentes que se ejecutarán
    state = create_run(query, len(sources))

    def _worker():
        from sources.base import Hit
        nombre = query.get("nombre", "") if isinstance(query, dict) else query
        # Cada fuente se ejecuta en SUBPROCESO en PARALELO con timeout duro.
        # subprocess.run es la forma más robusta en macOS (no usa spawn).
        import subprocess
        import sys
        import threading
        # Timeout por fuente. Las fuentes se ejecutan EN PARALELO, así que
        # el tiempo total del run ≈ la fuente más lenta, no la suma. Por eso
        # damos budgets generosos a las fuentes con captcha/browser: un solo
        # solve de reCAPTCHA (CapSolver/2captcha) puede tardar 60-90s, y si
        # hay respaldo (el otro solver) puede duplicarse.
        DEFAULT_TIMEOUT = 70
        PER_SOURCE_TIMEOUT = {
            "Contraloría General — Responsabilidad Fiscal": 260,
            "Policía Nacional — Antecedentes Judiciales": 170,
            "Policía Nacional — Inhabilidades por delitos sexuales (Ley 1918)": 170,
            "Policia Nacional — Inhabilidades por delitos sexuales (Ley 1918)": 170,
            "Policía — Delitos Sexuales contra Menores": 170,
            "Procuraduría — Antecedentes Disciplinarios": 150,
            "PACO — Portal Anticorrupción de Colombia": 120,
            "RUT — DIAN (Registro Único Tributario)": 160,
            "Registraduría — Estado de cédula": 120,
            "Registraduría — Defunciones (estado de la cédula)": 120,
            "RUES — Registro Único Empresarial y Social": 120,
            "SIGEP — Función Pública Colombia (browser)": 120,
        }
        def _timeout_for(name: str) -> int:
            return PER_SOURCE_TIMEOUT.get(name, DEFAULT_TIMEOUT)
        # Directorio de trabajo del subproceso: el del propio proyecto (ROOT),
        # NO una ruta absoluta hardcodeada. Portable a cualquier servidor.
        CWD = str(ROOT)
        # Script que ejecuta UNA fuente y escribe resultado a stdout.
        # Usa delimitadores únicos __OK_PAYLOAD__/__ERR_PAYLOAD__ para
        # evitar contaminación por prints de debug de las fuentes.
        worker_script = '''
import sys, json
sys.path.insert(0, ".")
from sources import registry
from sources.base import safe_fetch
from solvers import get_default_solver
query_nombre = sys.argv[1]
query_cedula = sys.argv[2] if sys.argv[2] else None
query_fecha = sys.argv[3] if sys.argv[3] else None
source_name = sys.argv[4]
solver = get_default_solver()
for src in registry.all_sources():
    if src.name == source_name:
        try:
            h = safe_fetch(src, query_nombre, query_cedula, query_fecha, solver)
            h.source_url = src.source_url
            payload = {
                "matched": h.matched,
                "summary": h.summary or "",
                "details": h.details or [],
                "error": h.error or "",
                "notice": h.notice or "",
                "download_url": h.download_url or "",
                "elapsed_s": h.elapsed_s,
                "captcha_required": h.captcha_required,
                "evidence_urls": h.evidence_urls or [],
                "source_url": h.source_url,
                "status": getattr(h, "status", None),
                "confidence": getattr(h, "confidence", None),
                "matched_name": getattr(h, "matched_name", None),
                "matched_document": getattr(h, "matched_document", None),
                "role": getattr(h, "role", None),
                "case_number": getattr(h, "case_number", None),
                "dataset_version": getattr(h, "dataset_version", None),
                "dataset_records": getattr(h, "dataset_records", 0) or 0,
                "error_type": getattr(h, "error_type", None),
                "requires_manual_review": getattr(h, "requires_manual_review", False),
                "notes": getattr(h, "notes", None),
            }
            sys.stdout.write("\\n__OK_PAYLOAD__:" + json.dumps(payload, ensure_ascii=False) + "\\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write("\\n__ERR_PAYLOAD__:" + str(e) + "\\n")
            sys.stdout.flush()
        break
else:
    sys.stdout.write("\\n__ERR_PAYLOAD__:source not found: " + source_name + "\\n")
    sys.stdout.flush()
'''
        def _run_one(s):
            per_timeout = _timeout_for(s.name)
            heavy = _is_heavy_source(s)
            # Gobernador de concurrencia GLOBAL (multi-usuario): tomar un slot
            # del pool total y, si la fuente es pesada (Chromium/captcha), un
            # slot adicional del pool de navegadores. Se liberan en finally.
            _GLOBAL_SEM.acquire()
            if heavy:
                _BROWSER_SEM.acquire()
            try:
                # En modo EMPRESA, avisar a las fuentes con selector de tipo de
                # documento que el documento es un NIT, para que elijan la
                # opción correcta en vez de "CEDULA". Se pasa por entorno para
                # no tocar la firma fetch(nombre, cedula, fecha, solver) de
                # todas las fuentes.
                sub_env = dict(os.environ)
                if is_empresa:
                    sub_env["VERIFYDATA_DOC_TYPE"] = "nit"
                result = subprocess.run(
                    [sys.executable, "-c", worker_script,
                     nombre, cedula or "", fecha_exp or "", s.name],
                    capture_output=True, text=True,
                    timeout=per_timeout, cwd=CWD, env=sub_env
                )
                out = result.stdout
                # Buscar delimitadores únicos __OK_PAYLOAD__/__ERR_PAYLOAD__
                # en cualquier parte del stdout (algunos fuentes hacen
                # print() de debug que contamina el prefijo).
                ok_idx = out.rfind("__OK_PAYLOAD__:")
                err_idx = out.rfind("__ERR_PAYLOAD__:")
                if ok_idx >= 0 and (err_idx < 0 or ok_idx > err_idx):
                    payload = json.loads(out[ok_idx+len("__OK_PAYLOAD__:"):].strip())
                    update_source(state.token, s.name, {
                        "source": s.name, "matched": payload["matched"],
                        "summary": payload["summary"],
                        "details": payload["details"],
                        "error": payload["error"] or None,
                        "notice": payload["notice"] or None,
                        "download_url": payload["download_url"] or None,
                        "elapsed_s": payload["elapsed_s"],
                        "captcha_required": payload["captcha_required"],
                        "evidence_urls": payload["evidence_urls"],
                        "source_url": payload["source_url"],
                        "status": payload.get("status"),
                        "confidence": payload.get("confidence"),
                        "matched_name": payload.get("matched_name"),
                        "matched_document": payload.get("matched_document"),
                        "role": payload.get("role"),
                        "case_number": payload.get("case_number"),
                        "dataset_version": payload.get("dataset_version"),
                        "dataset_records": payload.get("dataset_records", 0),
                        "error_type": payload.get("error_type"),
                        "requires_manual_review": payload.get(
                            "requires_manual_review", False),
                        "notes": payload.get("notes"),
                    })
                else:
                    err_msg = (out[err_idx+len("__ERR_PAYLOAD__:"):].strip() if err_idx >= 0
                               else out.strip()[:200])
                    update_source(state.token, s.name, {
                        "source": s.name, "matched": False, "summary": "",
                        "error": err_msg or "Sin output del worker",
                        "details": [], "notice": None, "download_url": None,
                        "elapsed_s": 0, "captcha_required": False,
                        "evidence_urls": [], "source_url": s.source_url,
                    })
            except subprocess.TimeoutExpired:
                update_source(state.token, s.name, {
                    "source": s.name, "matched": False, "summary": "",
                    "notice": f"Timeout duro {per_timeout}s. "
                              f"Proceso terminado forzosamente.",
                    "error": None,
                    "status": "timeout", "error_type": "timeout",
                    "details": [], "download_url": None,
                    "elapsed_s": per_timeout,
                    "captcha_required": False,
                    "evidence_urls": [], "source_url": s.source_url,
                })
            except Exception as e:
                update_source(state.token, s.name, {
                    "source": s.name, "matched": False, "summary": "",
                    "error": f"{type(e).__name__}: {e}",
                    "details": [], "notice": None, "download_url": None,
                    "elapsed_s": 0, "captcha_required": False,
                    "evidence_urls": [], "source_url": s.source_url,
                })
            finally:
                # Liberar SIEMPRE los slots de concurrencia (aunque falle).
                if heavy:
                    _BROWSER_SEM.release()
                _GLOBAL_SEM.release()

        # ORDEN: fuentes LIVIANAS (HTTP rápido) primero, PESADAS
        # (browser/captcha, 90-200s) al final. Sin esto, una fuente pesada
        # temprana ocupa un worker del pool por minutos y las livianas quedan
        # esperando detrás → el "done" se estanca (síntoma: en la búsqueda por
        # NIT la 1ª persona avanza y las otras se quedan a la mitad). Al poner
        # las livianas primero, las ~50 fuentes rápidas de las 3 personas se
        # despachan de inmediato y solo la cola pesada (acotada por
        # _BROWSER_SEM) queda al final, repartida de forma pareja.
        ordered = sorted(sources, key=lambda s: 1 if _is_heavy_source(s) else 0)
        # Ejecutar con un pool ACOTADO por run. La concurrencia real entre
        # TODOS los runs/usuarios la imponen _GLOBAL_SEM / _BROWSER_SEM.
        with ThreadPoolExecutor(max_workers=_PER_RUN_WORKERS,
                                thread_name_prefix=f"run-{state.token[:6]}") as ex:
            list(ex.map(_run_one, ordered))
        mark_completed(state.token)
        # Guardar en cache la consulta completada (para reuso multi-usuario).
        try:
            _cache_store(ckey, state.token)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return state.token


# ═══════════════════════════════════════════════════════════════════════════
#  Búsqueda INLINE para Vercel serverless (sin subprocess, sin threads)
# ═══════════════════════════════════════════════════════════════════════════
# En Vercel, cada invocación es un proceso aislado con timeout de 60s.
# Esta función ejecuta las fuentes DIRECTAMENTE en el handler (sin subprocess)
# y guarda los resultados en SQLite para que el polling los lea.
# Timeout por fuente: 10s (livianas) / 15s (pesadas) para caber en 60s.

VERCEL_SOURCE_TIMEOUT_LIGHT = 10
VERCEL_SOURCE_TIMEOUT_HEAVY = 15


def run_search_progressive_inline(query: dict, cedula: str | None,
                                   fecha_exp: str | None, solver,
                                   skip_browser: bool = False,
                                   max_sources: int = 0) -> str:
    """Versión INLINE de run_search_progressive para Vercel serverless.
    Ejecuta las fuentes directamente (sin subprocess) y guarda en SQLite.
    max_sources: límite de fuentes a ejecutar (0 = todas las que quepan).
    Devuelve el token del run."""
    from sources import registry
    from sources.base import safe_fetch

    sources = registry.all_sources()
    # Si skip_browser=True, filtrar fuentes que requieren navegador
    if skip_browser:
        sources = [s for s in sources if "(browser)" not in s.name]
    sources_mode = query.get("__sources", "all") if isinstance(query, dict) else "all"
    FEATURED_NAMES = {
        "Registraduría — Estado de cédula",
        "Registraduría — Defunciones (estado de la cédula)",
        "Policía Nacional — Antecedentes Judiciales",
        "Policía Nacional — Inhabilidades por delitos sexuales (Ley 1918)",
        "Contraloría General — Responsabilidad Fiscal",
        "DIAN — Proveedores Ficticios (Boletín)",
        "RUT — DIAN (Registro Único Tributario)",
        "Procuraduría — Antecedentes Disciplinarios",
        "OFAC — Sanctions List Search (form web oficial)",
        "EUROPOL — Most Wanted Fugitives",
    }
    if sources_mode == "featured":
        sources = [s for s in sources if s.name in FEATURED_NAMES]
    elif isinstance(sources_mode, (list, tuple, set)):
        wanted = {str(x) for x in sources_mode}
        sources = [s for s in sources if s.name in wanted]

    # Ordenar: livianas primero, pesadas al final
    ordered = sorted(sources, key=lambda s: 1 if _is_heavy_source(s) else 0)

    # Limitar número de fuentes si se especifica
    if max_sources > 0:
        ordered = ordered[:max_sources]

    state = create_run(query, len(ordered))
    nombre = query.get("nombre", "") if isinstance(query, dict) else query

    # Ejecutar cada fuente DIRECTAMENTE (sin subprocess)
    for src in ordered:
        timeout = VERCEL_SOURCE_TIMEOUT_HEAVY if _is_heavy_source(src) else VERCEL_SOURCE_TIMEOUT_LIGHT
        try:
            import signal

            def _timeout_handler(signum, frame):
                raise TimeoutError(f"Timeout {timeout}s")

            # Solo usar signal en Unix
            if hasattr(signal, 'SIGALRM'):
                old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(timeout)
                try:
                    h = safe_fetch(src, nombre, cedula, fecha_exp, solver)
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)
            else:
                # En Windows/no-Unix, ejecutar sin timeout por signal
                h = safe_fetch(src, nombre, cedula, fecha_exp, solver)

            hit_dict = hit_to_dict(h)
            hit_dict["source_url"] = src.source_url
            update_source(state.token, src.name, hit_dict)
        except TimeoutError:
            update_source(state.token, src.name, {
                "source": src.name, "matched": False, "summary": "",
                "notice": f"Timeout {timeout}s en Vercel",
                "error": None, "status": "timeout", "error_type": "timeout",
                "details": [], "download_url": None, "elapsed_s": timeout,
                "captcha_required": False, "evidence_urls": [],
                "source_url": src.source_url,
            })
        except Exception as e:
            update_source(state.token, src.name, {
                "source": src.name, "matched": False, "summary": "",
                "error": f"{type(e).__name__}: {e}",
                "details": [], "notice": None, "download_url": None,
                "elapsed_s": 0, "captcha_required": False,
                "evidence_urls": [], "source_url": src.source_url,
            })

    mark_completed(state.token)
    return state.token


# ═══════════════════════════════════════════════════════════════════════════
#  Búsqueda por NIT (empresa → representantes legales → personas)
# ═══════════════════════════════════════════════════════════════════════════
# Flujo: se resuelve un NIT en RUES a su(s) matrícula(s) mercantil(es), se
# extraen los representantes legales (recursivo si un representante es a su vez
# persona jurídica) y a CADA persona natural encontrada se le lanza la búsqueda
# estándar en todas las fuentes (reusando `run_search_progressive`). El front
# hace polling a /api/nit/<token>. Ver sources/rues_nit.py.

@dataclass
class NitRunState:
    token: str
    nit: str
    started_at: str
    mode: str = "reps"               # reps | empresa | ambas
    status: str = "resolving"        # resolving | ready | error
    error: str | None = None
    empresa: str = ""
    empresa_datos: dict = field(default_factory=dict)  # registro mercantil raíz
    empresa_token: str | None = None  # token del run de la EMPRESA (info compañía)
    tree: list = field(default_factory=list)      # empresas visitadas
    personas: list = field(default_factory=list)  # {nombre,cedula,...,token}
    last_update: float = 0.0

    def to_dict(self) -> dict:
        import copy
        return {
            "token": self.token, "nit": self.nit,
            "started_at": self.started_at, "mode": self.mode,
            "status": self.status,
            "error": self.error, "empresa": self.empresa,
            "empresa_datos": copy.deepcopy(self.empresa_datos),
            "empresa_token": self.empresa_token,
            "tree": copy.deepcopy(self.tree),
            "personas": copy.deepcopy(self.personas),
            "last_update": self.last_update,
        }


_NIT_RUNS: dict[str, NitRunState] = {}


def get_nit_run(token: str) -> NitRunState | None:
    state = _NIT_RUNS.get(token)
    if state:
        return state
    # Si no está en memoria, intentar leer de SQLite (Vercel cold start)
    if _HAS_DB_PERSIST:
        try:
            d = nit_run_state_get(token)
            if d:
                state = NitRunState(
                    token=d["token"], nit=d["nit"],
                    started_at=d["started_at"], mode=d["mode"],
                    status=d["status"], error=d.get("error"),
                    empresa=d.get("empresa", ""),
                    empresa_datos=d.get("empresa_datos", {}),
                    empresa_token=d.get("empresa_token"),
                    tree=d.get("tree", []),
                    personas=d.get("personas", []),
                    last_update=d.get("last_update", 0))
                _NIT_RUNS[token] = state
                return state
        except Exception:
            pass
    return None


def _launch_empresa_run(state: "NitRunState", solver) -> None:
    """Lanza UN run de fuentes sobre la EMPRESA (razón social + NIT), estilo
    tusdatos, y guarda su token en state.empresa_token. Reutiliza
    run_search_progressive con el subconjunto curado de fuentes de empresa."""
    razon = state.empresa or (state.empresa_datos.get("razon_social") or "")
    nit_doc = (state.empresa_datos.get("nit") or state.nit)
    query = {
        "nombre": razon,
        "cedula": nit_doc,
        "fecha_exp": "",
        "__sources": empresa_source_names(),
        "__empresa_mode": True,
        "razon_social": razon,
        "__empresa_datos": state.empresa_datos,
        "__nit_token": state.token,
        "__empresa": razon,
    }
    try:
        import os
        _is_vercel = os.environ.get("VERIFYDATA_ENV") == "production"
        if _is_vercel:
            state.empresa_token = run_search_progressive_inline(
                query, state.nit, "", solver, skip_browser=False)
        else:
            state.empresa_token = run_search_progressive(
                query, state.nit, "", solver, skip_browser=False)
    except Exception as e:  # noqa: BLE001
        state.empresa_token = None
        state.empresa_datos = dict(state.empresa_datos or {})
        state.empresa_datos["launch_error"] = f"{type(e).__name__}: {e}"


def _launch_personas_runs(state: "NitRunState", personas: list, solver) -> list:
    """Lanza la búsqueda estándar por PERSONA para cada representante legal."""
    import os
    _is_vercel = os.environ.get("VERIFYDATA_ENV") == "production"
    launched = []
    for p in personas:
        query = {"nombre": p.get("nombre", ""), "cedula": p.get("cedula", ""),
                 "fecha_exp": "", "__sources": "all",
                 "__nit_token": state.token, "__empresa": state.empresa or ""}
        try:
            if _is_vercel:
                ptok = run_search_progressive_inline(
                    query, p.get("cedula", ""), "", solver, skip_browser=False)
            else:
                ptok = run_search_progressive(
                    query, p.get("cedula", ""), "", solver, skip_browser=False)
        except Exception as e:  # noqa: BLE001
            ptok = None
            p["launch_error"] = f"{type(e).__name__}: {e}"
        p["token"] = ptok
        launched.append(p)
    return launched


def run_nit_search(nit: str, solver, mode: str = "reps") -> str:
    """Lanza en background la búsqueda por NIT. Devuelve el token del run-NIT
    (para polling en /api/nit/<token>).

    mode:
      - "reps"    : resuelve los representantes legales en RUES (recursivo) y
                    corre la búsqueda estándar por CADA persona natural.
      - "empresa" : trae la INFORMACIÓN DE LA EMPRESA (razón social + NIT) contra
                    el subconjunto de fuentes de empresa, estilo tusdatos.
      - "ambas"   : lo anterior + los representantes legales.
    """
    from sources.rues_nit import _digits
    doc = _digits(nit)
    mode = (mode or "reps").strip().lower()
    if mode not in ("reps", "empresa", "ambas"):
        mode = "reps"
    token = secrets.token_urlsafe(24)  # no enumerable (ver A3)
    state = NitRunState(
        token=token, nit=doc, mode=mode,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _NIT_RUNS[token] = state
    # Persistir en BD si está disponible
    if _HAS_DB_PERSIST:
        try:
            nit_run_state_save(token, doc, state.started_at, mode, "resolving",
                              None, "", {}, None, [], [], state.last_update)
        except Exception:
            pass
    while len(_NIT_RUNS) > 100:
        _NIT_RUNS.pop(next(iter(_NIT_RUNS)))

    def _worker():
        want_reps = mode in ("reps", "ambas")
        want_empresa = mode in ("empresa", "ambas")
        # ── Resolver RUES ────────────────────────────────────────────────
        # Modo empresa puro: solo necesitamos los datos de la compañía raíz
        # (ruta rápida, sin recursión). Con reps o ambas: BFS recursivo que
        # también entrega la razón social raíz.
        try:
            if want_reps:
                from sources.rues_nit import resolver_nit
                res = resolver_nit(doc, max_depth=2)
                state.empresa = res.get("empresa", "")
                state.empresa_datos = res.get("empresa_datos", {}) or {}
                state.tree = res.get("tree", [])
                personas = res.get("personas", [])
            else:
                from sources.rues_nit import datos_empresa
                emp = datos_empresa(doc)
                state.empresa = emp.get("razon_social", "")
                emp_nit = emp.get("nit") or doc   # base resuelta (sin DV)
                state.empresa_datos = {
                    "nit": emp_nit, "razon_social": emp.get("razon_social", ""),
                    "estado": emp.get("estado", ""), "camara": emp.get("camara", ""),
                    "matricula": emp.get("matricula", ""),
                    "organizacion": emp.get("organizacion", ""),
                    "categoria": emp.get("categoria", ""),
                    "error": emp.get("error"),
                }
                state.tree = ([{"nivel": 0, "nit": emp_nit,
                                "razon_social": emp.get("razon_social", ""),
                                "estado": emp.get("estado", ""),
                                "camara": emp.get("camara", ""),
                                "matricula": emp.get("matricula", ""),
                                "organizacion": emp.get("organizacion", ""),
                                "categoria": emp.get("categoria", ""),
                                "reps": [], "error": emp.get("error")}]
                              if (emp.get("razon_social") or emp.get("error"))
                              else [])
                personas = []
        except Exception as e:  # noqa: BLE001
            state.status = "error"
            state.error = f"{type(e).__name__}: {e}"
            state.last_update = time.time()
            if _HAS_DB_PERSIST:
                try:
                    nit_run_state_save(token, state.nit, state.started_at,
                                      state.mode, state.status, state.error,
                                      state.empresa, state.empresa_datos,
                                      state.empresa_token, state.tree,
                                      state.personas, state.last_update)
                except Exception:
                    pass
            return

        # ── Lanzar la(s) búsqueda(s) ────────────────────────────────────
        # Empresa: basta con haber resuelto la razón social (o el NIT) para
        # correr las fuentes; muchas admiten búsqueda por documento.
        if want_empresa:
            _launch_empresa_run(state, solver)

        if want_reps:
            if not personas:
                # Sin representantes: en modo "reps" es error; en "ambas" no,
                # porque el reporte de empresa sí se pudo lanzar.
                if not want_empresa:
                    state.status = "error"
                    state.error = res.get("error") or (
                        "No se encontraron representantes legales (personas "
                        "naturales) para este NIT en RUES.")
                    state.last_update = time.time()
                    if _HAS_DB_PERSIST:
                        try:
                            nit_run_state_save(token, state.nit, state.started_at,
                                              state.mode, state.status, state.error,
                                              state.empresa, state.empresa_datos,
                                              state.empresa_token, state.tree,
                                              state.personas, state.last_update)
                        except Exception:
                            pass
                    return
            else:
                state.personas = _launch_personas_runs(state, personas, solver)

        # Modo empresa puro sin razón social ni datos → error.
        if (want_empresa and not want_reps and not state.empresa
                and not state.empresa_token):
            state.status = "error"
            state.error = (state.empresa_datos.get("error")
                           or "No se pudo resolver la empresa en RUES.")
            state.last_update = time.time()
            if _HAS_DB_PERSIST:
                try:
                    nit_run_state_save(token, state.nit, state.started_at,
                                      state.mode, state.status, state.error,
                                      state.empresa, state.empresa_datos,
                                      state.empresa_token, state.tree,
                                      state.personas, state.last_update)
                except Exception:
                    pass
            return

        state.status = "ready"
        state.last_update = time.time()
        if _HAS_DB_PERSIST:
            try:
                nit_run_state_save(token, state.nit, state.started_at,
                                  state.mode, state.status, None,
                                  state.empresa, state.empresa_datos,
                                  state.empresa_token, state.tree,
                                  state.personas, state.last_update)
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return token


def nit_run_payload(token: str) -> dict | None:
    """Estado del run-NIT enriquecido con el progreso en vivo de cada persona
    (matched/total/completed extraídos de su run individual)."""
    state = _NIT_RUNS.get(token)
    if not state:
        # Intentar leer de SQLite (Vercel cold start)
        if _HAS_DB_PERSIST:
            try:
                d = nit_run_state_get(token)
                if d:
                    state = NitRunState(
                        token=d["token"], nit=d["nit"],
                        started_at=d["started_at"], mode=d["mode"],
                        status=d["status"], error=d.get("error"),
                        empresa=d.get("empresa", ""),
                        empresa_datos=d.get("empresa_datos", {}),
                        empresa_token=d.get("empresa_token"),
                        tree=d.get("tree", []),
                        personas=d.get("personas", []),
                        last_update=d.get("last_update", 0))
                    _NIT_RUNS[token] = state
            except Exception:
                pass
        if not state:
            return None
    d = state.to_dict()

    def _progress(tok):
        run = _RUNS.get(tok) if tok else None
        if not run:
            return None
        return {
            "completed": run.completed, "total": run.total,
            "done": len(run.sources), "matched": run.matched,
            "captcha": run.captcha, "error": run.error,
        }

    # Progreso del reporte de EMPRESA (modo empresa/ambas).
    d["empresa_run"] = _progress(d.get("empresa_token"))
    # Progreso de cada PERSONA (modo reps/ambas).
    for p in d.get("personas", []):
        p["run"] = _progress(p.get("token"))
    return d


# Al importar el módulo (arranque del servidor), recargar el último run
# persistido para que su token siga siendo consultable tras un reinicio.
_restore_last_run()
