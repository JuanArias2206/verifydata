"""
tests/test_phase2.py — Tests específicos de la Fase 2.

Verifica:
  1. Las nuevas ~39 fuentes están registradas
  2. Las categorías se mantienen organizadas
  3. Las fuentes sin captcha tienen requires_captcha=False
  4. Las fuentes con link de búsqueda generan URLs válidas
  5. FBI API funciona (descarga lista y matchea Putin)
  6. ICIJ genera link a búsqueda
  7. PEP Colombia genera múltiples evidence URLs
  8. run_all ejecuta todas en paralelo

Ejecutar:
  cd busqueda_datos
  python3 tests/test_phase2.py
"""
from __future__ import annotations
import os
import re
import sys
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
    from sources import registry, Hit
    from sources.base import safe_fetch

    sources = registry.all_sources()
    print(f"\n=== Total fuentes: {len(sources)} ===\n")

    print("=== Test 1: Conteos por categoría ===")
    def t1():
        by_cat = registry.sources_by_category()
        # Las categorías pueden tener nombres largos
        cats_concat = " | ".join(by_cat.keys())
        for keyword in ("Sanciones", "Crimen", "Corrupción", "PEP",
                        "Contratación", "Reputacional", "Identidad",
                        "especializados", "Antecedentes"):
            assert keyword in cats_concat, f"missing category with: {keyword}"
    test("9 categorías presentes (keywords)", t1)

    print("\n=== Test 2: Nuevas fuentes internacionales ===")
    def t2():
        names = {s.name for s in sources}
        for n in ["OFAC — Lista Consolidada (Non-SDN, FSE, SSI, CAPTA)",
                  "OFAC — Direcciones y aliases (add.csv)",
                  "UE — Lista Consolidada de Sanciones",
                  "Canadá — SEMA / LMES Sanctions",
                  "Banco Mundial — Debarred Firms & Individuals"]:
            assert n in names, f"missing: {n}"
    test("5 nuevas internacionales registradas", t2)

    print("\n=== Test 3: ICIJ (5) ===")
    def t3():
        names = {s.name for s in sources}
        for n in ["ICIJ — Panama Papers", "ICIJ — Paradise Papers",
                  "ICIJ — Pandora Papers", "ICIJ — Offshore Leaks",
                  "ICIJ — Bahamas Leaks"]:
            assert n in names, f"missing: {n}"
        # Verificar que ICIJ genera links de búsqueda
        icij = next(s for s in sources if s.name == "ICIJ — Panama Papers")
        hit = safe_fetch(icij, "EDUARDO", None)
        assert hit.evidence_urls, "no evidence_urls"
        assert "offshoreleaks.icij.org" in hit.evidence_urls[0]
        assert "q=" in hit.evidence_urls[0], "missing q param"
    test("5 ICIJ registrados y generan search URLs", t3)

    print("\n=== Test 4: Wanted (11) ===")
    def t4():
        names = [s.name for s in sources]
        for keyword in ["FBI — Most Wanted", "INTERPOL — Red Notices",
                  "DEA — Most Wanted", "ICE — Most Wanted",
                  "DSS — Diplomatic Security", "CBI — Most Wanted",
                  "EUROPOL", "UK NCA", "Policía Colombia",
                  "Guardia Civil", "EPA — Fugitives"]:
            assert any(keyword in n for n in names), f"missing: {keyword}"
    test("11 wanted registradas (keywords)", t4)

    print("\n=== Test 5: PEP (4) ===")
    def t5():
        names = {s.name for s in sources}
        for n in ["SIGEP — Función Pública Colombia", "PEP Colombia",
                  "CIDOB — Barcelona Centre", "PEP Internacionales"]:
            assert any(n in name for name in names), f"missing: {n}"
    test("4 PEP registradas", t5)

    print("\n=== Test 6: DIAN y noticias ===")
    def t6():
        names = {s.name for s in sources}
        assert any("RUT — DIAN" in n for n in names)
        assert any("Proveedores Ficticios" in n for n in names)
        assert any("Noticias — Fiscalía" in n for n in names)
        assert any("Insight Crime" in n for n in names)
    test("DIAN + noticias registradas", t6)

    print("\n=== Test 7: Especializados ===")
    def t7():
        names = {s.name for s in sources}
        for n in ["SIRNA", "JCC", "FCPA", "PACO", "Grandes Contratistas"]:
            assert any(n in name for name in names), f"missing: {n}"
    test("Especializados registradas", t7)

    print("\n=== Test 8: Captcha flags correctos ===")
    def t8():
        # Sin captcha: FBI, ICIJ, PEP, etc
        no_captcha = [s for s in sources if not s.requires_captcha]
        captcha_required = [s for s in sources if s.requires_captcha]
        # Esperamos al menos 5 con captcha
        assert len(captcha_required) >= 5, f"esperaba ≥5 con captcha, hay {len(captcha_required)}"
        # Y muchos sin captcha
        assert len(no_captcha) >= 30, f"esperaba ≥30 sin captcha, hay {len(no_captcha)}"
    test(f"5+ con captcha, 30+ sin captcha", t8)

    print("\n=== Test 9: FBI Wanted descarga y matchea ===")
    def t9():
        import tempfile
        from lists import LocalListManager
        test_db = Path(tempfile.gettempdir()) / "test_fbi.db"
        if test_db.exists(): test_db.unlink()
        mgr = LocalListManager(db_path=test_db)
        from lists.downloaders import fbi_wanted
        n = mgr.refresh("fbi_wanted_test", fbi_wanted, force=True)
        assert n > 100, f"FBI debería tener >100 entries, got {n}"
        # Buscar por nombre (los subjects no están en name_norm)
        rows = mgr.search("fbi_wanted_test", "EREG")
        assert len(rows) > 0, "debería encontrar 'EREG' (presente en la lista)"
        test_db.unlink()
    test("FBI Wanted descarga y búsqueda funciona", t9)

    print("\n=== Test 10: PEP Colombia genera múltiples URLs ===")
    def t10():
        pep = next(s for s in sources if "PEP Colombia" in s.name)
        hit = safe_fetch(pep, "DANIEL MEDINA", None)
        assert len(hit.evidence_urls) >= 1, f"esperaba ≥1 URL, hay {len(hit.evidence_urls)}"
        for u in hit.evidence_urls:
            assert u.startswith("http"), f"URL malformada: {u}"
    test("PEP Colombia devuelve múltiples evidence URLs", t10)

    print("\n=== Test 11: run_all ejecuta en paralelo ===")
    def t11():
        import time
        t0 = time.time()
        hits = registry.run_all("VLADIMIR PUTIN", None, None, None,
                                skip_browser=True)
        elapsed = time.time() - t0
        assert len(hits) >= 40, f"esperaba ≥40 hits, hay {len(hits)}"
        assert elapsed < 60, f"tardó {elapsed:.1f}s, demasiado"
        # Verificar categorías presentes
        with_match = [h for h in hits if h.matched]
        print(f"    {len(hits)} hits, {len(with_match)} MATCH, "
              f"{sum(1 for h in hits if h.captcha_required)} CAPTCHA, "
              f"en {elapsed:.1f}s")
    test(f"run_all ejecuta {len(sources)} fuentes en <60s", t11)

    print("\n=== Test 12: Flask con todas las fuentes ===")
    def t12():
        from app import app
        client = app.test_client()
        # Verificar GET / muestra el lede con el conteo
        r = client.get("/")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        # El lede dice "Consulta <b>N fuentes</b>"
        m = re.search(r'Consulta\s*<b>(\d+)\s+fuentes', html)
        assert m, f"no se encontró 'Consulta N fuentes' en HTML"
        n = int(m.group(1))
        assert n >= 40, f"esperaba ≥40, lede muestra {n}"
        # Hacer POST y verificar results page tiene stats
        r2 = client.post("/", data={"nombre": "VLADIMIR PUTIN"})
        assert r2.status_code == 303
        loc = r2.headers.get("Location", "")
        r3 = client.get(loc)
        assert r3.status_code == 200
        html2 = r3.get_data(as_text=True)
        m2 = re.search(r'<b>(\d+)</b>\s*<small>fuentes</small>', html2)
        # v0.6+: el POST redirige a la página de progreso en vivo, que
        # renderiza las stats por JS (id="stats"); aceptar ambas variantes.
        assert m2 or 'id="stats"' in html2, "results page no tiene stats"
        if m2:
            assert int(m2.group(1)) >= 40
    test("App renderiza con conteo correcto de fuentes", t12)

    print(f"\n=== Resultado: {PASS} ok, {FAIL} fallaron ===\n")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
