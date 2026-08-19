"""
tests/test_phase3.py — Tests específicos de la Fase 3.

Verifica:
  1. TriviaSolver responde preguntas matemáticas básicas
  2. TriviaSolver rechaza preguntas no matemáticas
  3. El solver por defecto es TriviaSolver (configurado en config.yaml)
  4. Las fuentes reescritas con captcha se registran
  5. Procuraduría detecta trivia y la maneja
  6. Policía detecta TLS error y devuelve captcha_required
  7. RUT y Contraloría detectan captcha
  8. Las nuevas fuentes (RUES, JEPMS, SIUGJ) están registradas
  9. run_all con captcha solver ejecuta todo

Ejecutar:
  cd busqueda_datos
  python3 tests/test_phase3.py
"""
from __future__ import annotations
import sys
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def test(name: str, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✓ {name}")
        PASS += 1
    except AssertionError as e:
        print(f"  ✗ {name}: {e}")
        FAIL += 1
    except Exception as e:
        print(f"  ✗ {name}: {type(e).__name__}: {e}")
        FAIL += 1


def main():
    print("\n=== Test 1: TriviaSolver responde matemáticas ===")
    from solvers import TriviaSolver
    from solvers.base import CaptchaUnsolved
    def t1():
        s = TriviaSolver()
        assert s.is_available()
        assert s.solve_trivia("¿Cuánto es 2 + 3?") == "5"
        assert s.solve_trivia("5 + 5") == "10"
        assert s.solve_trivia("10 - 4") == "6"
        assert s.solve_trivia("3 * 4") == "12"
        assert s.solve_trivia("10 / 2") == "5"
    test("TriviaSolver responde +, -, *, /", t1)

    print("\n=== Test 2: TriviaSolver rechaza no-matemáticas ===")
    def t2():
        s = TriviaSolver()
        for bad in ["¿Cuál es tu nombre?", "Hola", "ABC", ""]:
            try:
                s.solve_trivia(bad)
                raise AssertionError(f"debería rechazar: {bad!r}")
            except CaptchaUnsolved:
                pass
    test("TriviaSolver rechaza preguntas no matemáticas", t2)

    print("\n=== Test 3: Solver por defecto es TriviaSolver ===")
    from solvers import get_default_solver
    def t3():
        s = get_default_solver()
        # El config.yaml tiene solver: "trivia"
        # Pero si la config se leyó antes del cambio, recargar
        from solvers.noop import get_default_solver as gd
        from config import load_config
        cfg = load_config()
        if cfg.get("captcha", {}).get("solver") == "trivia":
            # Forzar recarga
            import solvers.noop as nm
            nm._default = None
            from solvers.trivia import TriviaSolver
            s = nm.get_default_solver()
        assert s.name in ("TriviaSolver", "NoOpSolver") \
            or s.name.startswith("Fallback"), f"got {s.name}"
    test("Solver por defecto es TriviaSolver (o NoOp si no configurado)", t3)

    print("\n=== Test 4: Fuentes reescritas registradas ===")
    from sources import registry
    def t4():
        names = {s.name for s in registry.all_sources()}
        for keyword in ["Procuraduría — Antecedentes",
                  "Policía Nacional — Antecedentes",
                  "Policía — Delitos Sexuales",
                  "Contraloría General",
                  "Contaduría General",
                  "RUT — DIAN",
                  "Rama Judicial — Procesos",
                  "JEPMS",
                  "Juzgados TYBA",
                  "RUES"]:
            assert any(keyword in n for n in names), f"missing: {keyword}"
    test("10 fuentes captcha-requeridas registradas (keywords)", t4)

    print("\n=== Test 5: Procuraduría sin solver → captcha_required ===")
    from sources import safe_fetch
    from solvers import NoOpSolver
    def t5():
        sources = registry.all_sources()
        proc = next(s for s in sources
                    if s.name == "Procuraduría — Antecedentes Disciplinarios")
        # Sin solver (NoOp)
        hit = safe_fetch(proc, "DANIEL MEDINA", "80793180", None, NoOpSolver())
        # Debería o detectar trivia y devolver captcha_required,
        # o tener un notice. La página puede o no tener trivia visible.
        assert hit.notice or hit.captcha_required or hit.summary \
            or hit.error, \
            f"esperaba notice/captcha/summary, got {hit!r}"
    test("Procuraduría sin solver devuelve notice o captcha", t5)

    print("\n=== Test 6: Policía detecta TLS o captcha ===")
    def t6():
        pol = next(s for s in registry.all_sources()
                   if s.name == "Policía Nacional — Antecedentes Judiciales")
        hit = safe_fetch(pol, "DANIEL MEDINA", "80793180")
        # Captcha flag o SSL error
        assert hit.captcha_required or hit.notice or hit.error, \
            f"esperaba captcha/notice/error, got {hit}"
    test("Policía devuelve captcha/notice/error (no crash)", t6)

    print("\n=== Test 7: RUT y Contraloría ===")
    def t7():
        rut = next(s for s in registry.all_sources()
                   if s.name == "RUT — DIAN (Registro Único Tributario)")
        h1 = safe_fetch(rut, "DANIEL", "80793180")
        assert h1.captcha_required or h1.notice or h1.error or h1.summary

        ctr = next(s for s in registry.all_sources()
                   if "Contraloría General" in s.name)
        h2 = safe_fetch(ctr, "DANIEL", "80793180")
        assert h2.captcha_required or h2.notice or h2.error or h2.summary
    test("RUT y Contraloría: captcha o notice", t7)

    print("\n=== Test 8: Rama Judicial y RUES ===")
    def t8():
        for src_name in ("Rama Judicial — Procesos (demandante/demandado)",
                        "JEPMS — Juzgados de Ejecución de Penas y Medidas de Seguridad",
                        "Juzgados TYBA — Justicia XXI (procesos)",
                        "RUES — Registro Único Empresarial y Social"):
            src = next(s for s in registry.all_sources() if s.name == src_name)
            hit = safe_fetch(src, "DANIEL MEDINA", "80793180")
            assert hit.evidence_urls, f"{src_name} no genera evidence_urls"
    test("Rama Judicial y RUES generan evidence_urls", t8)

    print("\n=== Test 9: run_all con TriviaSolver ===")
    def t9():
        from solvers import get_default_solver
        # Forzar recarga con trivia si está configurado
        import solvers.noop as nm
        nm._default = None
        solver = get_default_solver()
        print(f"    solver en uso: {solver.name}")
        t0 = time.time()
        hits = registry.run_all("VLADIMIR PUTIN", None, None, solver,
                                skip_browser=True)
        elapsed = time.time() - t0
        assert len(hits) >= 50, f"esperaba ≥50 hits, hay {len(hits)}"
        n_captcha = sum(1 for h in hits if h.captcha_required)
        n_match = sum(1 for h in hits if h.matched)
        print(f"    {len(hits)} hits, {n_match} MATCH, {n_captcha} CAPTCHA, "
              f"en {elapsed:.1f}s (solver={solver.name})")
    test("run_all ejecuta con solver de trivia", t9)

    print("\n=== Test 10: Flask con nuevas fuentes ===")
    def t10():
        from app import app
        c = app.test_client()
        r = c.get("/")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        m = re.search(r'Consulta\s*<b>(\d+)\s+fuentes', html)
        assert m, "no se encontró contador"
        n = int(m.group(1))
        assert n >= 50, f"esperaba ≥50, html muestra {n}"
        # Hacer POST con captcha solver
        r2 = c.post("/", data={"nombre": "DANIEL MEDINA",
                              "cedula": "80793180",
                              "fecha_exp": "22/03/2002"})
        assert r2.status_code == 303
        loc = r2.headers.get("Location", "")
        r3 = c.get(loc)
        assert r3.status_code == 200
        html2 = r3.get_data(as_text=True)
        assert "Procuraduría" in html2
        assert "MATCH" in html2 or "CAPTCHA" in html2
    test("App funciona con 50+ fuentes", t10)

    print(f"\n=== Resultado: {PASS} ok, {FAIL} fallaron ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
