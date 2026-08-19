"""
tests/test_phase456.py — Tests para Fases 4, 5 y 6.

Fase 4: captcha solvers (twocaptcha, anticaptcha)
Fase 5: browser-based sources (SIGEP, INTERPOL, BIS, ICIJ)
Fase 6: PDF report generation
"""
from __future__ import annotations
import sys
import re
import time
import tempfile
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
    print("\n========= FASE 4: CAPTCHA SOLVERS =========")

    print("\n=== Test 1: TwoCaptchaSolver sin key no está disponible ===")
    from solvers.twocaptcha import TwoCaptchaSolver
    from solvers.base import CaptchaUnsolved
    def t1():
        s = TwoCaptchaSolver(api_key="")
        assert not s.is_available()
        try:
            s.solve_image(b"fake")
            raise AssertionError("debería lanzar CaptchaUnsolved")
        except CaptchaUnsolved:
            pass
    test("TwoCaptchaSolver sin key", t1)

    print("\n=== Test 2: TwoCaptchaSolver con key pero sin red ===")
    def t2():
        s = TwoCaptchaSolver(api_key="fake-test-key-12345")
        assert s.is_available()
        assert s.name == "TwoCaptchaSolver"
        # No intentamos solve real porque requiere internet pago
    test("TwoCaptchaSolver con key reporta disponible", t2)

    print("\n=== Test 3: AntiCaptchaSolver sin key ===")
    from solvers.anticaptcha import AntiCaptchaSolver
    def t3():
        s = AntiCaptchaSolver(api_key="")
        assert not s.is_available()
        assert s.name == "AntiCaptchaSolver"
    test("AntiCaptchaSolver sin key", t3)

    print("\n=== Test 4: get_default_solver respeta config ===")
    from solvers import get_solver_from_config
    def t4():
        s = get_solver_from_config()
        # Trivia es el default actual
        assert s.name in ("TriviaSolver", "NoOpSolver",
                          "TwoCaptchaSolver", "AntiCaptchaSolver")
    test("get_solver_from_config funciona", t4)

    print("\n========= FASE 5: BROWSER SOURCES =========")

    print("\n=== Test 5: Fuentes browser registradas ===")
    from sources import registry
    def t5():
        names = [s.name for s in registry.all_sources()]
        for keyword in ["SIGEP — Función Pública Colombia (browser)",
                       "INTERPOL — Red Notices (browser)",
                       "BIS — Entity List (browser)",
                       "ICIJ — Offshore Leaks (browser)"]:
            assert any(keyword in n for n in names), f"missing: {keyword}"
    test("4 fuentes browser registradas", t5)

    print("\n=== Test 6: Playwright está instalado ===")
    def t6():
        try:
            from playwright.sync_api import sync_playwright
            assert sync_playwright is not None
        except (ImportError, AssertionError):
            raise AssertionError("Playwright no instalado")
    test("Playwright instalado", t6)

    print("\n=== Test 7: SIGEP sin browser devuelve notice ===")
    from sources import safe_fetch
    def t7():
        sigep = next(s for s in registry.all_sources()
                     if "SIGEP" in s.name and "browser" in s.name)
        hit = safe_fetch(sigep, "DANIEL MEDINA", None)
        # O hace match real, o devuelve notice por browser error
        assert hit.evidence_urls, "debería tener evidence_urls"
    test("SIGEP browser source funciona", t7)

    print("\n========= FASE 6: PDF REPORT =========")

    print("\n=== Test 8: report.py genera PDF ===")
    def t8():
        from report import generate_pdf
        from sources import Hit
        hits = [
            Hit(source="OFAC SDN", matched=True, summary="1 match",
                details=[{"nombre_lista": "PUTIN, Vladimir",
                          "programa": "RUSSIA-EO14024"}], elapsed_s=0.5),
            Hit(source="Registraduría", matched=True, summary="VIGENTE",
                details=[{"estado": "VIGENTE (Válida)"}], elapsed_s=0.3),
            Hit(source="Procuraduría", matched=False, captcha_required=True,
                summary="Requiere captcha", notice="Trivia no resuelta",
                elapsed_s=0.1),
        ]
        pdf_bytes = generate_pdf({"nombre": "DANIEL", "cedula": "12345"},
                                  hits)
        assert len(pdf_bytes) > 1000, f"PDF demasiado pequeño: {len(pdf_bytes)}"
        # Verificar magic bytes del PDF
        assert pdf_bytes[:4] == b"%PDF", "no es un PDF válido"
    test("generate_pdf produce PDF válido", t8)

    print("\n=== Test 9: PDF se puede escribir a disco ===")
    def t9():
        from pathlib import Path
        from report import generate_pdf
        from sources import Hit
        tmp = Path(tempfile.gettempdir()) / "test_pdf.pdf"
        if tmp.exists(): tmp.unlink()
        hits = [Hit(source="OFAC", matched=True, summary="Test", elapsed_s=0.1)]
        generate_pdf({"nombre": "Test"}, hits, str(tmp))
        assert tmp.exists()
        assert tmp.stat().st_size > 100
        # Verificar magic bytes
        with open(tmp, "rb") as f:
            assert f.read(4) == b"%PDF"
        tmp.unlink()
    test("PDF se puede escribir a disco", t9)

    print("\n=== Test 10: Flask endpoint /download/pdf/ ===")
    def t10():
        from app import app
        c = app.test_client()
        # Primero crear un search run
        r = c.post("/", data={"nombre": "BASHAR AL ASSAD",
                              "cedula": "80793180",
                              "fecha_exp": "22/03/2002"})
        assert r.status_code == 303
        token = r.headers.get("Location", "").split("/")[-1]
        # Descargar PDF
        r2 = c.get(f"/download/pdf/{token}")
        assert r2.status_code == 200
        assert r2.headers.get("Content-Type") == "application/pdf"
        assert r2.data[:4] == b"%PDF"
    test("Flask /download/pdf/<token> funciona", t10)

    print("\n=== Test 11: run_all con browser habilitado ===")
    def t11():
        # Verificar que las fuentes browser están y se pueden ejecutar
        for src_name in ("SIGEP — Función Pública Colombia (browser)",
                        "INTERPOL — Red Notices (browser)"):
            src = next(s for s in registry.all_sources() if s.name == src_name)
            hit = safe_fetch(src, "TEST USER", None)
            # El hit puede ser MATCH o captcha_required, no debe crashear
            assert hit is not None
    test("Browser sources ejecutan sin crash", t11)

    print("\n=== Test 12: Total de fuentes ===")
    def t12():
        sources = registry.all_sources()
        assert len(sources) >= 50, f"esperaba ≥50, got {len(sources)}"
    test("≥50 fuentes registradas", t12)

    print(f"\n=== Resultado: {PASS} ok, {FAIL} fallaron ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
