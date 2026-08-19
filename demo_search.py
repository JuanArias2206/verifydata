#!/usr/bin/env python3
from __future__ import annotations
"""
Demo: búsqueda automatizada de información en 5 fuentes públicas.

Fuentes (todas sin captcha, todas automatizables hoy):
  1. SECOP II — Multas y Sanciones (SODA API datos.gov.co)
  2. SECOP I  — Multas y Sanciones (SODA API datos.gov.co)
  3. OFAC SDN — Specially Designated Nationals (CSV US Treasury)
  4. ONU      — UN Security Council Consolidated List (XML)
  5. UK HM Treasury — Consolidated Sanctions List (HTML scraping)

Uso:
  python3 demo_search.py "DANIEL LORENZO MEDINA SALCEDO"
  python3 demo_search.py "DANIEL LORENZO MEDINA SALCEDO" 80793180
"""
import csv
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
REPORT = ROOT / "report.html"

UA = "VerifyData-Demo/0.1 (contacto: verifydata.local)"
TIMEOUT = 30
NOMBRE_MIN_LEN = 4


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    retries = Retry(total=2, backoff_factor=0.6,
                    status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s


S = make_session()


@dataclass
class Hit:
    source: str
    matched: bool
    summary: str
    details: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    elapsed_s: float = 0.0


# ---------- helpers ----------

def normalize(s: str) -> str:
    s = s.upper()
    for a, b in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"),
                 ("Ñ", "N")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 1000 and not force:
        return dest
    print(f"  · descargando {url[:80]}…")
    r = S.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def search_text(needle_norm: str, haystack: str) -> bool:
    """Coincidencia por tokens: todos los tokens del needle deben estar
    presentes en haystack. Maneja orden invertido (PUTIN, Vladimir vs
    Vladimir PUTIN) y omite tokens muy cortos."""
    if len(needle_norm) < NOMBRE_MIN_LEN:
        return False
    tokens = [t for t in needle_norm.split() if len(t) >= 3]
    if not tokens:
        return needle_norm in haystack
    return all(t in haystack for t in tokens)


# ---------- 1. SECOP II (SODA API) ----------

def source_secop_ii(nombre: str, cedula: str | None) -> Hit:
    t0 = time.time()
    src = "SECOP II — Multas y Sanciones"
    try:
        # Esta tabla no tiene columna de documento; búsqueda solo por nombre.
        params = {"$limit": 50,
                  "nombre_proveedor_objeto_de": nombre.upper()}
        r = S.get("https://www.datos.gov.co/resource/it5q-hg94.json",
                  params=params, timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json()
        details = []
        needle = normalize(nombre)
        for row in rows:
            blob = normalize(json.dumps(row, ensure_ascii=False))
            if not search_text(needle, blob):
                continue
            details.append({k: row.get(k) for k in
                            ("id_proceso", "referencia_proceso",
                             "nombre_proveedor_objeto_de",
                             "valor", "valor_pagado", "fecha_evento",
                             "numero_de_acto", "tipo_de_sancion", "estado")
                            if k in row})
        return Hit(src, len(details) > 0,
                   f"{len(details)} coincidencia(s) en {len(rows)} registros",
                   details, elapsed_s=time.time() - t0)
    except Exception as e:
        return Hit(src, False, "", error=f"{type(e).__name__}: {e}",
                   elapsed_s=time.time() - t0)


# ---------- 2. SECOP I (SODA API) ----------

def source_secop_i(nombre: str, cedula: str | None) -> Hit:
    t0 = time.time()
    src = "SECOP I — Multas y Sanciones"
    try:
        # Schema real: nombre_contratista, documento_contratista
        params = {"$limit": 50}
        if cedula:
            params["documento_contratista"] = cedula
        else:
            params["nombre_contratista"] = nombre.upper()
        r = S.get("https://www.datos.gov.co/resource/4n4q-k399.json",
                  params=params, timeout=TIMEOUT)
        r.raise_for_status()
        rows = r.json()
        details = []
        needle = normalize(nombre)
        for row in rows:
            blob = normalize(json.dumps(row, ensure_ascii=False))
            if (not cedula and not search_text(needle, blob)) or \
               (cedula and cedula not in blob):
                continue
            details.append({k: row.get(k) for k in
                            ("nombre_entidad", "nit_entidad",
                             "nombre_contratista", "documento_contratista",
                             "numero_de_contrato", "valor_sancion",
                             "fecha_de_publicacion", "numero_de_resolucion",
                             "fecha_de_firmeza")
                            if k in row})
        return Hit(src, len(details) > 0,
                   f"{len(details)} coincidencia(s) en {len(rows)} registros",
                   details, elapsed_s=time.time() - t0)
    except Exception as e:
        return Hit(src, False, "", error=f"{type(e).__name__}: {e}",
                   elapsed_s=time.time() - t0)


# ---------- 3. OFAC SDN (CSV) ----------

def source_ofac(nombre: str, cedula: str | None) -> Hit:
    t0 = time.time()
    src = "OFAC SDN — Specially Designated Nationals"
    try:
        path = download(
            "https://www.treasury.gov/ofac/downloads/sdn.csv",
            DATA / "ofac_sdn.csv")
        needle = normalize(nombre)
        details = []
        with path.open(encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if not row or len(row) < 1 or row[0].startswith("0"):
                    continue
                if len(row) < 4:
                    continue
                _, sdn_name, sdn_type, program, *_ = row
                if search_text(needle, normalize(sdn_name)):
                    details.append({"nombre_lista": sdn_name,
                                    "tipo": sdn_type, "programa": program})
                    if len(details) >= 50:
                        break
        return Hit(src, len(details) > 0,
                   f"{len(details)} coincidencia(s) (OFAC SDN List)",
                   details, elapsed_s=time.time() - t0)
    except Exception as e:
        return Hit(src, False, "", error=f"{type(e).__name__}: {e}",
                   elapsed_s=time.time() - t0)


# ---------- 4. UN Security Council Consolidated (XML) ----------

def source_un(nombre: str, cedula: str | None) -> Hit:
    t0 = time.time()
    src = "ONU — UN Security Council Consolidated List"
    try:
        path = download(
            "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            DATA / "un_consolidated.xml")
        tree = ET.parse(path)
        root = tree.getroot()
        needle = normalize(nombre)
        details = []
        for ind in root.findall(".//INDIVIDUAL"):
            data = {c.tag: (c.text or "").strip() for c in ind}
            blob = normalize(" ".join(data.values()))
            if search_text(needle, blob):
                details.append({
                    "nombre": f"{data.get('FIRST_NAME','')} "
                              f"{data.get('SECOND_NAME','')} "
                              f"{data.get('THIRD_NAME','')} "
                              f"{data.get('FOURTH_NAME','')}".strip(),
                    "alias": data.get("ALIAS_NAME", ""),
                    "fecha_nacimiento": data.get("DATE_OF_BIRTH", ""),
                    "lugar_nacimiento": data.get("PLACE_OF_BIRTH", ""),
                    "nacionalidad": data.get("NATIONALITY", ""),
                    "designacion": data.get("UN_LIST_TYPE", ""),
                    "motivo": (data.get("COMMENTS1", "") or "")[:200],
                })
                if len(details) >= 30:
                    break
        return Hit(src, len(details) > 0,
                   f"{len(details)} coincidencia(s) (INDIVIDUAL entries)",
                   details, elapsed_s=time.time() - t0)
    except Exception as e:
        return Hit(src, False, "", error=f"{type(e).__name__}: {e}",
                   elapsed_s=time.time() - t0)


# ---------- 5. UK HM Treasury Sanctions (HTML scraping) ----------

def source_uk_treasury(nombre: str, cedula: str | None) -> Hit:
    t0 = time.time()
    src = "UK HM Treasury — Consolidated Sanctions List"
    try:
        needle = normalize(nombre)
        # La página de búsqueda devuelve HTML; los nombres están en <a> con
        # la clase govuk-link. Buscamos por palabra o apellido.
        last = (nombre.split()[-1] if nombre else "").upper()
        first = (nombre.split()[0] if nombre else "").upper()
        query = f"{first} {last}".strip()
        r = S.get(
            "https://search-uk-sanctions-list.service.gov.uk/search",
            params={"q": query, "type": "individual"},
            timeout=TIMEOUT)
        r.raise_for_status()
        details = []
        # cada resultado está en una <li class="app-search-result"> …
        # Nombre aparece en <a class="govuk-link" …>…</a>
        names = re.findall(
            r'class="govuk-link"[^>]*>([^<]{4,100})</a>', r.text)
        for nm in names:
            if search_text(needle, normalize(nm)):
                details.append({"nombre_listado": nm.strip(),
                                "url_busqueda":
                                f"https://search-uk-sanctions-list.service.gov.uk/"
                                f"search?q={query}"})
                if len(details) >= 30:
                    break
        return Hit(src, len(details) > 0,
                   f"{len(details)} coincidencia(s) en UK Sanctions List",
                   details, elapsed_s=time.time() - t0)
    except Exception as e:
        return Hit(src, False, "", error=f"{type(e).__name__}: {e}",
                   elapsed_s=time.time() - t0)


SOURCES = [
    ("SECOP II", source_secop_ii),
    ("SECOP I", source_secop_i),
    ("OFAC SDN", source_ofac),
    ("ONU Security Council", source_un),
    ("UK HM Treasury", source_uk_treasury),
]


# ---------- rendering ----------

def render_terminal(nombre: str, cedula: str | None, hits: list[Hit]) -> None:
    print()
    print("=" * 72)
    print(f"BÚSQUEDA AUTOMATIZADA — {nombre}" +
          (f" (CC {cedula})" if cedula else ""))
    print(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)
    total = 0
    for h in hits:
        mark = "❌ ERROR" if h.error else ("✅ MATCH" if h.matched else "○  sin coincidencia")
        print(f"\n[{mark}] {h.source}  ({h.elapsed_s:.2f}s)")
        if h.error:
            print(f"    → {h.error}")
        else:
            print(f"    {h.summary}")
            for d in h.details[:3]:
                kv = " | ".join(f"{k}={str(v)[:80]}" for k, v in d.items()
                                if v not in (None, "", "N/A"))
                print(f"      · {kv}")
            if len(h.details) > 3:
                print(f"      · … y {len(h.details) - 3} más")
        total += len(h.details)
    print()
    print("-" * 72)
    print(f"Total de coincidencias: {total}")
    print(f"Reporte HTML: {REPORT}")
    print("-" * 72)


def render_html(nombre: str, cedula: str | None, hits: list[Hit]) -> None:
    total = sum(len(h.details) for h in hits)
    n_match = sum(1 for h in hits if h.matched)
    n_err = sum(1 for h in hits if h.error)
    cards = []
    for h in hits:
        if h.error:
            status = "ERROR"
            color = "#d33"
        elif h.matched:
            status = "MATCH"
            color = "#0a7a3a"
        else:
            status = "SIN COINCIDENCIA"
            color = "#888"
        rows_html = ""
        for d in h.details:
            kv = "".join(
                f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>"
                for k, v in d.items() if v not in (None, "", "N/A"))
            if kv:
                rows_html += f"<table>{kv}</table>"
        if not rows_html and not h.error:
            rows_html = '<p class="muted">Sin coincidencias para el criterio de búsqueda.</p>'
        if h.error:
            rows_html = f'<p class="err">{escape(h.error)}</p>'
        cards.append(f"""
        <section class="card" style="border-left:6px solid {color}">
          <header>
            <span class="badge" style="background:{color}">{status}</span>
            <h2>{escape(h.source)}</h2>
            <span class="elapsed">{h.elapsed_s:.2f}s</span>
          </header>
          <p class="summary">{escape(h.summary)}</p>
          {rows_html}
        </section>""")
    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Búsqueda — {escape(nombre)}</title>
<style>
  :root {{
    --bg:#f5f6fa; --card:#fff; --ink:#1d1f23; --muted:#6b7280;
    --line:#e5e7eb; --accent:#4f46e5;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.5}}
  .wrap{{max-width:960px;margin:0 auto;padding:32px 24px}}
  h1{{margin:0 0 4px;font-size:24px}}
  .meta{{color:var(--muted);font-size:14px;margin-bottom:24px}}
  .stats{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
  .stat{{background:var(--card);padding:14px 18px;border-radius:10px;
        box-shadow:0 1px 3px rgba(0,0,0,.05);flex:1;min-width:140px}}
  .stat b{{display:block;font-size:24px;color:var(--accent)}}
  .card{{background:var(--card);border-radius:10px;padding:20px 24px;
        margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
  .card header{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
  .card h2{{margin:0;font-size:17px;flex:1}}
  .badge{{color:#fff;font-size:11px;font-weight:700;letter-spacing:.5px;
         padding:3px 8px;border-radius:999px}}
  .elapsed{{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}}
  .summary{{color:var(--muted);font-size:14px;margin:4px 0 12px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
  th{{text-align:left;width:200px;color:var(--muted);font-weight:500;
     padding:6px 8px 6px 0;vertical-align:top}}
  td{{padding:6px 0;border-top:1px solid var(--line)}}
  tr:first-child th,tr:first-child td{{border-top:0}}
  .muted{{color:var(--muted);font-style:italic}}
  .err{{color:#b91c1c;font-family:ui-monospace,Menlo,Consolas,monospace;
       font-size:13px}}
  footer{{margin-top:32px;text-align:center;color:var(--muted);
         font-size:12px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Búsqueda automatizada de información</h1>
  <div class="meta">
    <b>Sujeto:</b> {escape(nombre)}{(' · <b>CC</b> ' + escape(cedula)) if cedula else ''}
    · <b>Fecha:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  </div>
  <div class="stats">
    <div class="stat"><b>{len(hits)}</b>fuentes consultadas</div>
    <div class="stat"><b>{n_match}</b>con coincidencias</div>
    <div class="stat"><b>{n_err}</b>con error técnico</div>
    <div class="stat"><b>{total}</b>registros retornados</div>
  </div>
  {''.join(cards)}
  <footer>Demo local · 5 fuentes públicas sin captcha</footer>
</div>
</body>
</html>"""
    REPORT.write_text(html, encoding="utf-8")


def escape(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------- main ----------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    nombre = sys.argv[1].strip()
    cedula = sys.argv[2].strip() if len(sys.argv) > 2 else None

    print(f"\nBuscando '{nombre}'" + (f" (CC {cedula})" if cedula else ""))
    print("Ejecutando 5 fuentes en paralelo…\n")

    import concurrent.futures as cf
    hits: list[Hit] = []
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fn, nombre, cedula): name
                   for name, fn in SOURCES}
        for fut in cf.as_completed(futures):
            try:
                hits.append(fut.result())
            except Exception as e:
                hits.append(Hit(futures[fut], False, "",
                                error=f"{type(e).__name__}: {e}"))
    hits.sort(key=lambda h: [n for n, _ in SOURCES].index(
        next(name for name, fn in SOURCES
             if h.source.startswith(name.split()[0]))))

    render_terminal(nombre, cedula, hits)
    render_html(nombre, cedula, hits)
    print(f"\n✓ Reporte HTML: {REPORT}")


if __name__ == "__main__":
    main()
