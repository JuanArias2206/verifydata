"""Prueba UNA fuente por nombre (substring) con el solver de config.
Uso: python3 test_one_source.py "Contralor" 2>&1
Imprime el Hit como JSON legible + timing.
"""
import sys, json, time
sys.path.insert(0, ".")
from sources import registry
from sources.base import safe_fetch
from solvers import get_default_solver

NOMBRE = "Juan Manuel Arias Gallego"
CEDULA = "1192722347"
FECHA = "03/07/2020"

needle = sys.argv[1] if len(sys.argv) > 1 else "Contralor"
solver = get_default_solver()
print(f"[test] solver = {solver.name}", flush=True)
for src in registry.all_sources():
    if needle.lower() in src.name.lower():
        print(f"[test] fuente = {src.name}", flush=True)
        t0 = time.time()
        h = safe_fetch(src, NOMBRE, CEDULA, FECHA, solver)
        dt = time.time() - t0
        out = {
            "source": h.source, "matched": h.matched,
            "summary": (h.summary or "")[:400],
            "details": h.details or [],
            "error": h.error, "notice": h.notice,
            "download_url": h.download_url,
            "captcha_required": h.captcha_required,
            "elapsed_s": round(dt, 1),
        }
        print("[test] RESULT:\n" + json.dumps(out, ensure_ascii=False, indent=2), flush=True)
        break
else:
    print(f"[test] no source matches '{needle}'", flush=True)
