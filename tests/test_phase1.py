"""
tests/test_phase1.py — Smoke tests para la Fase 1.

Verifica:
  1. La DB se inicializa con el schema correcto
  2. El registry funciona (decorador @register)
  3. Las 10 fuentes existentes se registran automáticamente
  4. El solver por defecto es NoOp
  5. El LocalListManager puede descargar y buscar en OFAC
  6. La config se carga de config.yaml
  7. El token matching funciona
  8. La app Flask arranca (sin request, solo el servidor)

Ejecutar:
  cd busqueda_datos
  python3 tests/test_phase1.py
"""
from __future__ import annotations
import os
import sys
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
    print("\n=== Test 1: DB schema ===")
    from db import init_db, get_db
    tmp_db = Path(tempfile.gettempdir()) / "test_verifydata.db"
    if tmp_db.exists(): tmp_db.unlink()
    init_db(tmp_db)
    def t1():
        with get_db(tmp_db) as conn:
            tables = [r[0] for r in conn.cursor().execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            for t in ("list_entries","list_meta","search_runs","cert_files"):
                assert t in tables, f"missing table {t}"
    test("init_db crea las 4 tablas", t1)
    tmp_db.unlink()

    print("\n=== Test 2: config.yaml ===")
    from config import load_config
    def t2():
        cfg = load_config()
        assert "http" in cfg and "database" in cfg and "captcha" in cfg
        assert cfg["captcha"]["solver"] in ("noop","twocaptcha","anticaptcha","trivia","chain","capsolver")
        assert cfg["webapp"]["port"] == 5070
    test("config.yaml se carga con defaults", t2)

    print("\n=== Test 3: Registry y fuentes registradas ===")
    from sources import registry
    def t3():
        # sources/__init__.py importa todos los módulos que auto-registran
        sources = registry.all_sources()
        assert len(sources) >= 10, f"expected ≥10, got {len(sources)}"
        names = [s.name for s in sources]
        # Verificar que las palabras clave de Fase 1 aparecen
        for keyword in ["OFAC", "UN Security Council", "UK HM Treasury",
                        "SECOP II — Multas", "SECOP I — Multas",
                        "Procuraduría", "Registraduría",
                        "SECOP II — Contratos", "Policía Nacional",
                        "Policía — Delitos Sexuales"]:
            assert any(keyword in n for n in names), \
                f"missing source with keyword: {keyword}"
    test(f"Registry tiene ≥10 fuentes (auto-registradas)", t3)

    print("\n=== Test 4: Categorías ===")
    def t4():
        by_cat = registry.sources_by_category()
        assert "Contratación pública" in by_cat
        assert "Sanciones internacionales" in by_cat
        assert "Antecedentes disciplinarios" in by_cat
    test("sources_by_category agrupa correctamente", t4)

    print("\n=== Test 5: Captcha flag ===")
    def t5():
        captcha_sources = [s for s in registry.all_sources() if s.requires_captcha]
        assert len(captcha_sources) >= 3, \
            f"expected ≥3 captcha sources, got {len(captcha_sources)}"
        # Verificar que el solver NoOp no resuelve
        for s in captcha_sources:
            assert s.captcha_type, f"{s.name} no tiene captcha_type"
    test("3+ fuentes con requires_captcha=True y captcha_type", t5)

    print("\n=== Test 6: NoOp solver ===")
    from solvers import NoOpSolver
    from solvers.base import CaptchaUnsolved
    def t6():
        s = NoOpSolver()
        assert not s.is_available()
        try:
            s.solve_image(b"fake_png")
            raise AssertionError("should have raised")
        except CaptchaUnsolved:
            pass
    test("NoOpSolver lanza CaptchaUnsolved", t6)

    print("\n=== Test 7: Token matching ===")
    from sources.local_lists import normalize, tokenize
    def t7():
        # Nombres invertidos: tokens deben matchear ambos lados
        tokens = tokenize("VLADIMIR PUTIN")
        assert "VLADIMIR" in tokens and "PUTIN" in tokens
        # Normalización de acentos
        assert "MEDINA SALCEDO" == normalize("Medina Salcedo")
        assert "MEDINA SALCEDO" == normalize("medina  salcedo ")
        # Tokens cortos (<3 chars) se filtran
        t_short = tokenize("ED A B CD EF")
        # Solo "EF" (2 chars no, 2 no, 2 no, 2 no) - todos <3 → []
        # en realidad "EF" tampoco, son 2
        assert t_short == []
        # Verificar con tokens de 3+
        t3 = tokenize("ABC DEF GHIJ")
        assert "ABC" in t3 and "DEF" in t3 and "GHIJ" in t3
    test("normalize() y tokenize() funcionan", t7)

    print("\n=== Test 8: LocalListManager (OFAC) ===")
    from lists import LocalListManager
    from db import init_db as _init
    def t8():
        test_db = Path(tempfile.gettempdir()) / "test_lists.db"
        if test_db.exists(): test_db.unlink()
        mgr = LocalListManager(db_path=test_db)
        from lists.downloaders import ofac_sdn
        n = mgr.refresh("ofac_sdn_test", ofac_sdn, force=True)
        assert n > 1000, f"OFAC should have >1000 entries, got {n}"
        rows = mgr.search("ofac_sdn_test", "VLADIMIR PUTIN")
        assert any("PUTIN" in r.get("name","").upper() for r in rows), \
            f"PUTIN not found in {len(rows)} rows"
        test_db.unlink()
    test("LocalListManager descarga OFAC y encuentra a PUTIN", t8)

    print("\n=== Test 9: Hits de la red funcionan ===")
    from sources import Hit, safe_fetch
    def t9():
        # Encontrar OfacSdnSource
        ofac = next(s for s in registry.all_sources() if s.name.startswith("OFAC"))
        hit = safe_fetch(ofac, "VLADIMIR PUTIN", None)
        assert hit.source == "OFAC SDN — Specially Designated Nationals"
        assert hit.matched, "debería matchear PUTIN"
        assert hit.elapsed_s >= 0
    test("safe_fetch(OFAC, 'VLADIMIR PUTIN') devuelve MATCH", t9)

    print("\n=== Test 10: Flask app arranca ===")
    def t10():
        from app import app
        client = app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        # el form debe estar en la respuesta
        html = r.get_data(as_text=True)
        assert "search-form" in html
        assert "f-nombre" in html
        assert "f-cedula" in html
        assert "f-fecha" in html
        # el contador de fuentes debe aparecer
        assert "10 fuentes" in html or "fuentes públicas" in html
    test("GET / responde 200 con form completo", t10)

    print("\n=== Test 11: PRG redirect ===")
    def t11():
        from app import app
        client = app.test_client()
        r = client.post("/", data={"nombre": "VLADIMIR PUTIN"})
        assert r.status_code == 303, f"esperado 303, recibí {r.status_code}"
        assert "/results/" in r.headers.get("Location","")
        # Seguir redirect
        r2 = client.get(r.headers["Location"])
        assert r2.status_code == 200
        html = r2.get_data(as_text=True)
        assert "OFAC" in html
        assert "match" in html.lower() or "coinciden" in html or "fuentes" in html
    test("POST /redirect 303 → /results/... con resultados", t11)

    print(f"\n=== Resultado: {PASS} ok, {FAIL} fallaron ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
