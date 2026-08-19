"""
tests/test_estados.py — Tests de los casos críticos de la revisión v0.7.

Cubre:
  1. Trivia: capital con typo de la fuente ("Vallle del Cauca") → Cali.
  2. _status_kind: un error técnico JAMÁS clasifica como "sin registro";
     estados explícitos se respetan; timeout se detecta.
  3. Datasets: lista ausente/vacía → dataset_missing, nunca "0 coincidencias".
  4. Registry: sin duplicados; inventario 1:1 con el registry.
  5. Serialización: los campos estructurados del Hit sobreviven el
     round-trip worker → runs → PDF.

Ejecutar:
  cd busqueda_datos
  python3 tests/test_estados.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} {extra}")


# ---------- 1. Trivia: typos de la fuente ----------

def test_trivia_typos():
    print("\n[1] Trivia — capitales con typos")
    from solvers.trivia import answer_capital
    casos = [
        ("¿Cual es la Capital del Vallle del Cauca?", "Cali"),
        ("¿Cuál es la capital de Antioquia?", "Medellín"),
        ("Capital de Colombia (sin tilde)", "Bogota"),
        ("¿cual es la capital del atlantico?", "Barranquilla"),
        ("¿Capital del Vale del Cauca?", "Cali"),       # typo inverso
        ("¿Cuánto es 9-2?", None),                       # no es capital
    ]
    for q, want in casos:
        got = answer_capital(q)
        check(f"answer_capital({q!r}) == {want!r}", got == want,
              f"(got {got!r})")


# ---------- 2. _status_kind ----------

def test_status_kind():
    print("\n[2] _status_kind — errores nunca son 'sin registro'")
    from sources.base import Hit
    from report import _status_kind, _status_group

    # error técnico
    h = Hit("X", False, "", error="ReadTimeout: foo timed out")
    check("error con timeout → 'timeout'", _status_kind(h) == "timeout")
    check("timeout agrupa como error", _status_group(h) == "error")

    h = Hit("X", False, "", error="ValueError: boom")
    check("error genérico → 'error'", _status_kind(h) == "error")

    # status explícito gana
    h = Hit("X", False, "0 coincidencias", status="dataset_missing")
    check("dataset_missing explícito se respeta",
          _status_kind(h) == "dataset_missing")
    check("dataset_missing agrupa como nodisp (no nomatch)",
          _status_group(h) == "nodisp")

    # manual_review
    h = Hit("X", False, "Búsqueda ejecutada", requires_manual_review=True)
    check("requires_manual_review → manual_review",
          _status_kind(h) == "manual_review")
    check("manual_review agrupa como review", _status_group(h) == "review")

    # match con confianza
    h = Hit("X", True, "REGISTRA", confidence="exacta")
    check("matched+exacta → match_exact", _status_kind(h) == "match_exact")
    h = Hit("X", True, "REGISTRA", confidence="posible")
    check("matched+posible → possible_homonym",
          _status_kind(h) == "possible_homonym")

    # nomatch verificado por keywords (legacy)
    h = Hit("X", False, "NO REGISTRA antecedentes")
    check("summary limpio → nomatch_verified",
          _status_kind(h) == "nomatch_verified")
    check("nomatch_verified agrupa como nomatch",
          _status_group(h) == "nomatch")


# ---------- 3. Datasets: ausentes/vacíos nunca son '0 coincidencias' ----------

def test_dataset_guard():
    print("\n[3] Datasets — guard de listas ausentes/vacías")
    from lists.manager import LocalListManager, DatasetMissing

    with tempfile.TemporaryDirectory() as td:
        mgr = LocalListManager(db_path=Path(td) / "test.db")

        # 4a. Descarga falla y no hay cache → DatasetMissing
        def fetcher_roto():
            raise RuntimeError("descarga rota")
        try:
            mgr.ensure_dataset("lista_x", fetcher_roto, min_rows=10)
            check("fetcher roto sin cache → DatasetMissing", False)
        except DatasetMissing:
            check("fetcher roto sin cache → DatasetMissing", True)

        # 4b. Descarga devuelve vacío → DatasetMissing
        def fetcher_vacio():
            return [], "http://x", "csv"
        try:
            mgr.ensure_dataset("lista_y", fetcher_vacio, min_rows=1)
            check("fetcher vacío → DatasetMissing", False)
        except DatasetMissing:
            check("fetcher vacío → DatasetMissing", True)

        # 4c. Descarga OK → meta con count
        def fetcher_ok():
            return ([{"name": f"PERSONA {i}"} for i in range(50)],
                    "http://x", "csv")
        meta = mgr.ensure_dataset("lista_z", fetcher_ok, min_rows=10)
        check("fetcher ok → count=50", meta.get("last_count") == 50,
              f"({meta})")
        check("fetcher ok → no stale", meta.get("stale") is False)

        # 4d. Cache previa + descarga rota → stale, no DatasetMissing
        calls = {"n": 0}
        def fetcher_intermitente():
            calls["n"] += 1
            raise RuntimeError("caída temporal")
        # forzar refresh venciendo el TTL con ttl=0
        from datetime import timedelta
        meta = mgr.ensure_dataset("lista_z", fetcher_intermitente,
                                  min_rows=10, ttl=timedelta(seconds=0))
        check("cache previa + descarga rota → stale=True",
              meta.get("stale") is True, f"({meta})")
        check("cache previa + descarga rota → conserva count",
              meta.get("last_count") == 50)


def test_list_lookup_status():
    print("\n[4b] _list_lookup — status correcto")
    from sources.internacionales import _list_lookup
    from lists.manager import LocalListManager
    import sources.internacionales as inter

    with tempfile.TemporaryDirectory() as td:
        mgr_test = LocalListManager(db_path=Path(td) / "t.db")
        old = inter._mgr
        inter._mgr = mgr_test
        try:
            def fetcher_roto():
                raise RuntimeError("no hay red")
            h = _list_lookup("Fuente X", "lx", fetcher_roto, "Juan Perez",
                             "Lista X", min_rows=5)
            check("dataset ausente → status dataset_missing",
                  h.status == "dataset_missing", f"({h.status})")
            check("dataset ausente → matched=False", h.matched is False)
            check("dataset ausente → summary NO dice '0 coincidencias'",
                  "0 coincidencias" not in (h.summary or ""))

            def fetcher_ok():
                return ([{"name": "JUAN PEREZ GOMEZ"},
                         *({"name": f"OTRO {i}"} for i in range(20))],
                        "http://x", "csv")
            h = _list_lookup("Fuente X", "ly", fetcher_ok, "Juan Perez",
                             "Lista X", min_rows=5)
            check("match por nombre → match_probable",
                  h.status == "match_probable", f"({h.status})")
            check("match → dataset_records=21", h.dataset_records == 21)

            h = _list_lookup("Fuente X", "ly", fetcher_ok,
                             "Nadie Inexistente Xyz", "Lista X", min_rows=5)
            check("sin match con dataset OK → nomatch_verified",
                  h.status == "nomatch_verified", f"({h.status})")
        finally:
            inter._mgr = old


# ---------- 5. Registry: sin duplicados, inventario 1:1 ----------

def test_registry_consistency():
    print("\n[5] Registry — sin duplicados; inventario 1:1")
    import json
    from sources import registry
    srcs = registry.all_sources()
    names = [s.name for s in srcs]
    check("sin nombres duplicados", len(names) == len(set(names)),
          f"({[n for n in names if names.count(n) > 1]})")
    # slugs/URLs duplicadas del MISMO origen (heurística: url base repetida
    # entre fuentes con nombres distintos es sospechosa solo si el nombre
    # comparte prefijo)
    dup_stubs = [n for n in names if "(browser)" in n
                 and n.replace(" (browser)", "") in names]
    check("sin pares stub+browser del mismo origen", not dup_stubs,
          f"({dup_stubs})")
    inv = json.load(open(ROOT / "inventory.json"))
    inv_names = {it.get("name") or it.get("nombre") for it in inv}
    check("inventario == registry (mismos nombres)",
          inv_names == set(names),
          f"(solo inv: {inv_names - set(names)}; "
          f"solo reg: {set(names) - inv_names})")


# ---------- 6. Serialización round-trip ----------

def test_hit_roundtrip():
    print("\n[6] Hit — round-trip de evidencia estructurada")
    from sources.base import Hit
    from runs import hit_to_dict
    h = Hit("Fuente", True, "REGISTRA", [{"a": 1}],
            status="match_exact", confidence="exacta",
            matched_name="JUAN PEREZ", matched_document="123",
            role="Demandado", case_number="1100140...",
            dataset_version="2026-07-02", dataset_records=30242,
            error_type=None, requires_manual_review=False,
            notes="obs")
    d = hit_to_dict(h)
    h2 = Hit(**{k: v for k, v in d.items() if k != "source_url"})
    for f in ("status", "confidence", "matched_name", "matched_document",
              "role", "case_number", "dataset_version", "dataset_records",
              "requires_manual_review", "notes"):
        check(f"round-trip conserva {f}",
              getattr(h, f) == getattr(h2, f),
              f"({getattr(h, f)!r} != {getattr(h2, f)!r})")

    # PDF: un Hit con error y otro dataset_missing deben aparecer en la caja
    # de "no disponibles/error" del ejecutivo (no como 'sin registro').
    from report import _build_executive
    hits = [
        Hit("F-err", False, "", error="ReadTimeout: x timed out"),
        Hit("F-ds", False, "no consultado", status="dataset_missing"),
        Hit("F-rev", False, "ver screenshot", requires_manual_review=True),
        Hit("F-ok", False, "NO REGISTRA nada"),
    ]
    els = _build_executive({"nombre": "T", "cedula": "1"}, hits)

    def _texts(flowable) -> str:
        """Extrae texto recursivamente (Paragraph y celdas de Table)."""
        out = [str(getattr(flowable, "text", ""))]
        cells = getattr(flowable, "_cellvalues", None)
        if cells:
            for row in cells:
                for cell in row:
                    if isinstance(cell, (list, tuple)):
                        out.extend(_texts(c) for c in cell)
                    elif cell is not None:
                        out.append(_texts(cell))
        return " ".join(out)

    blob = " ".join(_texts(e) for e in els)
    check("PDF ejecutivo menciona la fuente con error", "F-err" in blob)
    check("PDF ejecutivo menciona la fuente dataset_missing", "F-ds" in blob)
    check("PDF ejecutivo menciona la fuente por revisar", "F-rev" in blob)
    check("PDF ejecutivo NO lista la fuente con nomatch verificado",
          "F-ok" not in blob)


if __name__ == "__main__":
    test_trivia_typos()
    test_status_kind()
    test_dataset_guard()
    test_list_lookup_status()
    test_registry_consistency()
    test_hit_roundtrip()
    print(f"\n===== {PASS} ok, {FAIL} fail =====")
    sys.exit(1 if FAIL else 0)
