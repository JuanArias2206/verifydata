"""
tests/test_phase456_quick.py — Tests rápidos de Fase 4, 5, 6 (sin browser stress).
"""
from __future__ import annotations
import sys
from pathlib import Path
import tempfile

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
    print("\n========= FASE 4 =========")

    from solvers.twocaptcha import TwoCaptchaSolver
    from solvers.anticaptcha import AntiCaptchaSolver
    from solvers.base import CaptchaUnsolved

    def t1():
        s = TwoCaptchaSolver(api_key="")
        assert not s.is_available()
    test("TwoCaptchaSolver sin key", t1)

    def t2():
        s = TwoCaptchaSolver(api_key="fake")
        assert s.is_available()
        assert s.name == "TwoCaptchaSolver"
    test("TwoCaptchaSolver con key", t2)

    def t3():
        s = AntiCaptchaSolver(api_key="")
        assert not s.is_available()
    test("AntiCaptchaSolver sin key", t3)

    def t4():
        s = AntiCaptchaSolver(api_key="fake")
        assert s.is_available()
    test("AntiCaptchaSolver con key", t4)

    print("\n========= FASE 5 =========")

    def t5():
        from playwright.sync_api import sync_playwright
        assert sync_playwright is not None
    test("Playwright instalado", t5)

    def t6():
        from sources import registry
        n = sum(1 for s in registry.all_sources() if "(browser)" in s.name)
        assert n >= 3
    test("3+ fuentes browser registradas", t6)

    def t7():
        from sources import registry, safe_fetch
        sigep = next(s for s in registry.all_sources()
                     if "SIGEP" in s.name and "browser" in s.name)
        # Test que al menos tiene los atributos correctos
        assert sigep.requires_captcha == False
        assert sigep.source_url.startswith("https://")
    test("SIGEP browser source registrado correctamente", t7)

    def t8():
        from sources import registry
        assert len(registry.all_sources()) >= 50
    test("≥50 fuentes", t8)

    print("\n========= FASE 6 =========")

    def t9():
        from report import generate_pdf
        from sources import Hit
        hits = [Hit(source="OFAC", matched=True, summary="x",
                     details=[{"a": 1}])]
        pdf = generate_pdf({"nombre": "T"}, hits)
        assert len(pdf) > 1000
        assert pdf[:4] == b"%PDF"
    test("PDF generado en bytes", t9)

    def t10():
        from report import generate_pdf
        from sources import Hit
        tmp = Path(tempfile.gettempdir()) / "test.pdf"
        if tmp.exists(): tmp.unlink()
        generate_pdf({"nombre": "T"},
                     [Hit(source="OFAC", matched=True, summary="x")],
                     str(tmp))
        assert tmp.exists()
        assert tmp.stat().st_size > 100
        tmp.unlink()
    test("PDF escrito a disco", t10)

    def t11():
        # Run sintético: prueba token→PDF sin lanzar las 59 fuentes en vivo.
        from app import app
        from runs import create_run, update_source, get_run
        c = app.test_client()
        state = create_run({"nombre": "TEST PDF", "cedula": "123"}, 2)
        update_source(state.token, "OFAC SDN — Specially Designated Nationals",
                      {"source": "OFAC SDN — Specially Designated Nationals",
                       "matched": False, "summary": "0 coincidencias",
                       "details": [], "error": None, "notice": None,
                       "download_url": None, "elapsed_s": 0.5,
                       "captcha_required": False, "evidence_urls": [],
                       "source_url": "https://x", "status": "nomatch_verified"})
        r2 = c.get(f"/download/pdf/{state.token}")
        assert r2.status_code == 200, f"status {r2.status_code}"
        assert r2.headers.get("Content-Type") == "application/pdf"
        assert r2.data[:4] == b"%PDF"
        # Run inexistente → 404; run vacío → 202 (no 404 confuso)
        assert c.get("/download/pdf/nope").status_code == 404
        s2 = create_run({"nombre": "VACIO"}, 1)
        assert c.get(f"/download/pdf/{s2.token}").status_code == 202
    test("Flask /download/pdf/<token>", t11)

    print(f"\n=== Resultado: {PASS} ok, {FAIL} fallaron ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
