"""
lists/downloaders.py — Funciones de descarga y parseo de listas bulk.

Cada downloader retorna (rows, url, format) donde:
  - rows: list[dict] — cada dict es una entrada de la lista
  - url:  la URL original
  - format: "csv", "xml", "json", "html"

Las funciones son reusables: pueden llamarse desde una fuente o desde
LocalListManager.refresh().
"""
from __future__ import annotations
import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import requests

UA = "VerifyData-Demo/1.0 (contacto: verifydata.local)"
TIMEOUT = 60


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retries = Retry(total=3, backoff_factor=0.6,
                    status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s


S = make_session()


def http_get(url: str, **kwargs) -> requests.Response:
    r = S.get(url, timeout=kwargs.pop("timeout", TIMEOUT), **kwargs)
    r.raise_for_status()
    return r


def http_post(url: str, data: dict, headers: dict | None = None,
              **kwargs) -> requests.Response:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers: h.update(headers)
    r = S.post(url, data=data, headers=h,
               timeout=kwargs.pop("timeout", TIMEOUT), **kwargs)
    r.raise_for_status()
    return r


# ---------- OFAC SDN ----------

def ofac_sdn() -> tuple[list[dict], str, str]:
    """Descarga y parsea la OFAC SDN list (CSV)."""
    url = "https://www.treasury.gov/ofac/downloads/sdn.csv"
    r = http_get(url)
    rows: list[dict] = []
    reader = csv.reader(io.StringIO(r.text))
    for row in reader:
        if not row or row[0].startswith("0"): continue
        if len(row) < 12: continue
        rows.append({
            "name": row[1],
            "type": row[2],
            "program": row[3],
            "title": row[4] if len(row) > 4 else "",
            "remarks": (row[11] if len(row) > 11 else "").strip(),
        })
    return rows, url, "csv"


# ---------- UN Security Council ----------

def un_consolidated() -> tuple[list[dict], str, str]:
    url = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    r = http_get(url)
    root = ET.fromstring(r.content)
    rows: list[dict] = []
    for ind in root.findall(".//INDIVIDUAL"):
        d = {c.tag: (c.text or "").strip() for c in ind}
        full = " ".join(filter(None, [
            d.get("FIRST_NAME",""), d.get("SECOND_NAME",""),
            d.get("THIRD_NAME",""), d.get("FOURTH_NAME","")])).strip()
        rows.append({
            "name": full,
            "alias": d.get("ALIAS_NAME",""),
            "dob": d.get("DATE_OF_BIRTH",""),
            "pob": d.get("PLACE_OF_BIRTH",""),
            "nationality": d.get("NATIONALITY",""),
            "list_type": d.get("UN_LIST_TYPE",""),
            "comments": (d.get("COMMENTS1","") or "")[:300],
            "type": "INDIVIDUAL",
        })
    for ent in root.findall(".//ENTITY"):
        d = {c.tag: (c.text or "").strip() for c in ent}
        rows.append({
            "name": d.get("FIRST_NAME",""),
            "alias": d.get("ALIAS_NAME",""),
            "list_type": d.get("UN_LIST_TYPE",""),
            "comments": (d.get("COMMENTS1","") or "")[:300],
            "type": "ENTITY",
        })
    return rows, url, "xml"


# ---------- EU Consolidated Sanctions ----------

def eu_consolidated() -> tuple[list[dict], str, str]:
    """Descarga la lista consolidada de sanciones financieras de la UE (FSF).

    El XML de webgate pone los nombres en ATRIBUTOS de <nameAlias>
    (wholeName/firstName/lastName), no en texto — el parser anterior leía
    texto y cacheaba ~2 filas, produciendo falsos negativos silenciosos.
    El token 'dG9rZW4tMjAxNw' es el token público documentado del servicio."""
    url = ("https://webgate.ec.europa.eu/fsd/fsf/public/files/"
           "xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw")
    r = http_get(url, timeout=120)
    ns = "{http://eu.europa.ec/fpi/fsd/export}"
    root = ET.fromstring(r.content)
    rows: list[dict] = []
    for ent in root.iter(f"{ns}sanctionEntity"):
        eu_ref = ent.get("euReferenceNumber", "")
        subj = ent.find(f"{ns}subjectType")
        stype = subj.get("code", "") if subj is not None else ""
        reg = ent.find(f"{ns}regulation")
        prog = reg.get("programme", "") if reg is not None else ""
        for alias in ent.iter(f"{ns}nameAlias"):
            name = (alias.get("wholeName") or " ".join(filter(None, (
                alias.get("firstName"), alias.get("middleName"),
                alias.get("lastName"))))).strip()
            if name:
                rows.append({"name": name, "type": stype,
                             "program": prog, "eu_ref": eu_ref})
    # Sanidad: la lista real tiene miles de alias. Si el parseo devuelve
    # poquísimos, el formato cambió — mejor fallar que cachear basura.
    if len(rows) < 100:
        raise RuntimeError(
            f"EU FSF: parseo sospechosamente corto ({len(rows)} filas); "
            "posible cambio de formato del XML")
    return rows, url, "xml"


# ---------- BIS Denied Persons List (CSV) ----------

def bis_dpl() -> tuple[list[dict], str, str]:
    url = (
        "https://www.bis.doc.gov/index.php/documents/consolidated-"
        "screening-list/817-csl-bureau-of-industry-and-security-"
        "file/download"
    )
    r = http_get(url)
    rows = []
    for row in csv.reader(io.StringIO(r.text)):
        if not row or len(row) < 5: continue
        if row[0].upper() in ("NAME","LAST NAME","FIRST NAME"): continue
        name = f"{row[0]} {row[1]}".strip() if len(row) > 1 else row[0]
        rows.append({"name": name, "type": "Denied Person",
                     "country": row[3] if len(row) > 3 else "",
                     "program": "BIS Denied Persons List"})
    return rows, url, "csv"


# ---------- World Bank Ineligible ----------

def worldbank_ineligible() -> tuple[list[dict], str, str]:
    url = ("https://www.worldbank.org/en/projects-operations/"
           "procurement/debarred-firms")
    r = http_get(url)
    text = re.sub(r"<[^>]+>", " ", r.text)
    rows: list[dict] = []
    seen = set()
    for token in re.findall(r"[A-Z][A-Z &\.\-]{6,80}", text):
        t = token.strip()
        if t in seen or "WORLD BANK" in t.upper(): continue
        seen.add(t)
        rows.append({"name": t, "type": "ineligible",
                     "program": "World Bank Listing"})
    return rows, url, "html"


# ---------- OFAC Non-SDN Consolidated (XML) ----------

def ofac_consolidated() -> tuple[list[dict], str, str]:
    """Lista consolidada OFAC (Non-SDN + SDN + agregado) en XML.
    Incluye Non-SDN, FSE, SSI, CAPTA, etc."""
    url = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml"
    r = http_get(url, timeout=90)
    root = ET.fromstring(r.content)
    rows: list[dict] = []
    # El XML usa un namespace por defecto
    ns = {"ofac": "https://sanctionslistservice.ofac.treas.gov/"
          "api/PublicationPreview/exports/XML"}
    # SDN entries
    for entry in root.findall(".//ofac:sdnEntry", ns):
        first = entry.findtext("ofac:firstName", default="", namespaces=ns)
        last = entry.findtext("ofac:lastName", default="", namespaces=ns)
        sdn_type = entry.findtext("ofac:sdnType", default="", namespaces=ns)
        programs = entry.findall("ofac:programList/ofac:program", ns)
        prog_str = ", ".join(p.text or "" for p in programs)
        name = f"{first} {last}".strip()
        if name:
            rows.append({"name": name, "type": sdn_type,
                         "program": prog_str,
                         "list": "OFAC Consolidated"})
    return rows, url, "xml"


# ---------- OFAC Address / Alt names ----------

def ofac_addrs() -> tuple[list[dict], str, str]:
    """OFAC alt.csv — aliases (aka/fka) de entidades sancionadas.

    Antes se descargaba add.csv, que solo trae DIRECCIONES (sin columna de
    nombre): el parser exigía ≥7 columnas y cacheaba 0 filas. Los alias
    buscables por nombre viven en alt.csv (ent_num, alt_num, tipo, alias)."""
    url = "https://www.treasury.gov/ofac/downloads/alt.csv"
    r = http_get(url, timeout=60)
    rows: list[dict] = []
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) < 4:
            continue
        name = (row[3] or "").strip()
        if not name or name == "-0-":
            continue
        rows.append({"name": name,
                     "type": f"Alias ({(row[2] or '').strip()})",
                     "ent_num": (row[0] or '').strip(),
                     "program": "OFAC alias"})
    if len(rows) < 100:
        raise RuntimeError(
            f"OFAC alt.csv: parseo sospechosamente corto ({len(rows)} filas)")
    return rows, url, "csv"


# ---------- OpenSanctions mirrors (CSV simple) ----------
# data.opensanctions.org publica mirrors diarios de listas oficiales en
# targets.simple.csv (mismo mecanismo ya probado en worldbank_debarred.py).
# Columnas: id,schema,name,aliases,birth_date,countries,addresses,
# identifiers,sanctions,phones,emails,dataset,first_seen,last_seen,last_change

def opensanctions_csv(slug: str, min_rows: int = 10):
    """Factory: devuelve un downloader para el mirror OpenSanctions `slug`."""
    url = f"https://data.opensanctions.org/datasets/latest/{slug}/targets.simple.csv"

    def _download() -> tuple[list[dict], str, str]:
        r = http_get(url, timeout=120)
        r.encoding = "utf-8"
        rows: list[dict] = []
        for rec in csv.DictReader(io.StringIO(r.text)):
            name = (rec.get("name") or "").strip()
            if not name:
                continue
            row = {"name": name,
                   "type": rec.get("schema", ""),
                   "countries": rec.get("countries", ""),
                   "program": (rec.get("sanctions") or "")[:140],
                   "identifiers": (rec.get("identifiers") or "")[:120]}
            aliases = (rec.get("aliases") or "").strip()
            if aliases:
                row["aliases"] = aliases[:200]
                # indexar también los alias como filas buscables
                for al in aliases.split(";"):
                    al = al.strip()
                    if al and al.lower() != name.lower():
                        rows.append({**row, "name": al,
                                     "type": f"alias de {name[:60]}"})
            rows.append(row)
        if len(rows) < min_rows:
            raise RuntimeError(
                f"OpenSanctions {slug}: parseo sospechosamente corto "
                f"({len(rows)} filas)")
        return rows, url, "csv"

    _download.__name__ = f"opensanctions_{slug}"
    return _download


# ---------- Canada SEMA-LMES (XML) ----------

def canada_sema() -> tuple[list[dict], str, str]:
    """Canada — Special Economic Measures Act (SEMA) + LMES listings."""
    url = ("https://www.international.gc.ca/world-monde/assets/office_docs/"
           "international_relations-relations_internationales/"
           "sanctions/sema-lmes.xml")
    r = http_get(url, timeout=60)
    root = ET.fromstring(r.content)
    rows: list[dict] = []
    for rec in root.findall(".//record"):
        last = rec.findtext("LastName", default="").strip()
        given = rec.findtext("GivenName", default="").strip()
        name = f"{given} {last}".strip()
        if not name: continue
        rows.append({
            "name": name,
            "type": "individual",
            "country": rec.findtext("Country", default=""),
            "schedule": rec.findtext("Schedule", default=""),
            "item": rec.findtext("Item", default=""),
            "list": "Canada SEMA-LMES",
        })
    return rows, url, "xml"


# ---------- FBI Most Wanted (JSON API) ----------

def fbi_wanted(limit: int = 500) -> tuple[list[dict], str, str]:
    """FBI Wanted API. Paginada."""
    url = "https://api.fbi.gov/wanted"
    rows: list[dict] = []
    page = 1
    while True:
        r = http_get(f"{url}?pageSize=20&page={page}", timeout=20)
        d = r.json()
        items = d.get("items", [])
        if not items: break
        for it in items:
            rows.append({
                "name": it.get("title", ""),
                "type": "Fugitive",
                "subjects": ", ".join(it.get("subjects", [])),
                "reward": it.get("reward_text", ""),
                "nationality": it.get("nationality", ""),
                "sex": it.get("sex", ""),
                "hair": it.get("hair", ""),
                "weight": it.get("weight", ""),
                "list": "FBI Wanted",
            })
        page += 1
        if page > 100 or len(rows) >= limit: break
    return rows, url, "json"


# ---------- INTERPOL Red Notices (intenta API público) ----------

def interpol_red() -> tuple[list[dict], str, str]:
    """INTERPOL Red Notices. La API pública está protegida;
    como fallback devuelve link a la búsqueda web."""
    candidates = [
        "https://ws-public.interpol.int/notices/v1/red?page=1&resultPerPage=200",
        "https://www.interpol.int/notice/search?nationality=&"
        "name=&forename=&ageMin=&ageMax=&sex=&"
        "nationality=&noticeType=2&charge=&"
        "wantedFor=&__cf_chl_jschl_tk__=",
    ]
    for url in candidates:
        try:
            r = http_get(url, timeout=15)
            if r.status_code == 200 and "notice" in r.text.lower():
                rows: list[dict] = []
                d = r.json() if "json" in r.headers.get("Content-Type","") else {}
                for n in d.get("_embedded", {}).get("notices", []):
                    name = " ".join(filter(None, [n.get("forename",""),
                                                  n.get("name","")])).strip()
                    rows.append({
                        "name": name, "type": "Red Notice",
                        "nationality": n.get("nationalities",""),
                        "sex": n.get("sex",""),
                        "dob": n.get("date_of_birth",""),
                        "list": "INTERPOL Red Notice",
                    })
                return rows, url, "json"
        except Exception:
            continue
    # Fallback: lista vacía con metadata
    return [], "https://www.interpol.int/How-we-work/Notices/Red-Notices", "blocked"


# ---------- ICIJ (links a búsqueda manual) ----------

def icij_search(name: str) -> str:
    """Genera URL de búsqueda en offshoreleaks.icij.org."""
    from urllib.parse import quote
    return f"https://offshoreleaks.icij.org/search?q={quote(name)}"


# ---------- PEP Colombia (links) ----------

def pep_colombia_search(name: str) -> str:
    from urllib.parse import quote
    return f"https://www.funcionpublica.gov.co/web/sigep2/directorio?q={quote(name)}"


# ---------- UK NCA Most Wanted (estática) ----------

def nca_uk_most_wanted() -> tuple[list[dict], str, str]:
    """Lista estática de UK NCA Most Wanted. Se puede refrescar
    scrapeando https://www.nationalcrimeagency.gov.uk/most-wanted."""
    from .nca_uk import all_nca_uk
    return list(all_nca_uk()), "https://www.nationalcrimeagency.gov.uk/most-wanted", "static"


# ---------- DEA Most Wanted Fugitives (HTML paginated) ----------

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0.0.0 Safari/537.36")


def _dea_fugitives_session() -> requests.Session:
    """Session con browser-like headers para evitar bloqueo Akamai."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def _dea_fugitives_parse(html: str) -> list[dict]:
    """Extrae entradas de fugitivos del HTML de la página de DEA.

    Cada fugitivo aparece en un <h3 class="teaser__heading"><a href=
    "...fugitives/<slug>"><nombre></a></h3> con foto y descripción."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    seen: set[str] = set()
    for h3 in soup.select("h3.teaser__heading a"):
        href = h3.get("href", "")
        name = h3.get_text(strip=True)
        if not name or "fugitives/" not in href:
            continue
        # slug estable como id (deduplica si está repetido en el DOM)
        slug = href.split("fugitives/")[-1].split("?")[0].rstrip("/")
        if not slug or slug in seen or slug == "all":
            continue
        seen.add(slug)
        # URL canónica del fugitivo
        if "/web/" in href:
            # viene del Wayback — extraer URL original
            try:
                original = "https://" + href.split("https://", 1)[1]
            except Exception:
                original = f"https://www.dea.gov/fugitives/{slug}"
        else:
            original = f"https://www.dea.gov/fugitives/{slug}"
        # descripción de cargos (párrafo siguiente al h3)
        parent = h3.find_parent("article") or h3.find_parent("div")
        desc = ""
        if parent is not None:
            txt = parent.find("div", class_="teaser__text")
            if txt is not None:
                desc = txt.get_text(" ", strip=True)[:300]
            else:
                p = parent.find("p")
                if p:
                    desc = p.get_text(" ", strip=True)[:300]
        rows.append({
            "name": name,
            "slug": slug,
            "url": original,
            "description": desc,
            "list": "DEA Most Wanted Fugitives",
        })
    return rows


def _dea_fugitives_total_pages(html: str) -> int:
    """Lee el total de páginas del pager."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    last = soup.select_one("a.pager__link--last")
    if last and last.get("href"):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(last["href"]).query)
        try:
            return max(1, int(q.get("page", ["1"])[-1]) + 1)
        except (ValueError, IndexError):
            pass
    return 1


def _dea_fugitives_wayback_snapshot() -> str | None:
    """Devuelve la URL de Wayback Machine con la snapshot más reciente,
    o None si la API no responde."""
    try:
        r = requests.get(
            "https://archive.org/wayback/available",
            params={"url": "https://www.dea.gov/fugitives/all"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        snap = d.get("archived_snapshots", {}).get("closest", {})
        if snap.get("available") and snap.get("url"):
            return snap["url"]
    except Exception:
        pass
    return None


def dea_fugitives(max_pages: int = 80) -> tuple[list[dict], str, str]:
    """Descarga la lista de fugitivos de la DEA (Most Wanted).

    Estrategia:
      1) Intenta https://www.dea.gov/fugitives/all con browser UA.
         Si recibe 200 y HTML útil, parsea directamente.
      2) Si está bloqueado (403/Akamai), usa Wayback Machine para
         obtener un snapshot navegable con la misma estructura.
      3) Pagina todas las páginas en paralelo y devuelve la lista
         completa (slug + nombre + URL canónica + descripción).

    Devuelve (rows, source_url, format)."""
    import concurrent.futures as cf

    # --- 1) intentar DEA directo ---
    direct_url = "https://www.dea.gov/fugitives/all"
    try:
        sess = _dea_fugitives_session()
        r = sess.get(direct_url, timeout=15)
        if r.status_code == 200 and "fugitives/" in r.text:
            page1_rows = _dea_fugitives_parse(r.text)
            if page1_rows:
                total = _dea_fugitives_total_pages(r.text)
                total = min(total, max_pages)
                rows = list(page1_rows)
                if total > 1:
                    def _fetch(p: int) -> list[dict]:
                        try:
                            rr = sess.get(
                                f"{direct_url}?page={p}",
                                timeout=15,
                            )
                            if rr.status_code == 200:
                                return _dea_fugitives_parse(rr.text)
                        except Exception:
                            pass
                        return []
                    with cf.ThreadPoolExecutor(max_workers=8) as ex:
                        for batch in cf.as_completed(
                            ex.submit(_fetch, p) for p in range(1, total)
                        ):
                            rows.extend(batch.result())
                # dedupe
                seen_slugs: set[str] = set()
                uniq: list[dict] = []
                for row in rows:
                    if row["slug"] in seen_slugs:
                        continue
                    seen_slugs.add(row["slug"])
                    uniq.append(row)
                return uniq, direct_url, "html"
    except Exception:
        pass

    # --- 2) fallback Wayback Machine ---
    snap = _dea_fugitives_wayback_snapshot()
    if not snap:
        return [], direct_url, "blocked"
    # snap típico: http://web.archive.org/web/20260608180010/https://www.dea.gov/fugitives/all
    # base = prefijo hasta la URL original
    if "/https://" in snap:
        base = snap.split("/https://", 1)[0] + "/https://"
    elif "/http://" in snap:
        base = snap.split("/http://", 1)[0] + "/http://"
    else:
        return [], direct_url, "blocked"
    try:
        sess = _dea_fugitives_session()
        r1 = sess.get(snap, timeout=20)
        r1.raise_for_status()
        page1_rows = _dea_fugitives_parse(r1.text)
        if not page1_rows:
            return [], direct_url, "blocked"
        total = _dea_fugitives_total_pages(r1.text)
        total = min(total, max_pages)
        rows = list(page1_rows)
        if total > 1:
            def _fetch(p: int) -> list[dict]:
                try:
                    rr = sess.get(
                        f"{base}www.dea.gov/fugitives/all?page={p}",
                        timeout=20,
                    )
                    if rr.status_code == 200:
                        return _dea_fugitives_parse(rr.text)
                except Exception:
                    pass
                return []
            with cf.ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_fetch, p): p for p in range(1, total)}
                for f in cf.as_completed(futs):
                    rows.extend(f.result())
        # dedupe
        seen_slugs = set()
        uniq: list[dict] = []
        for row in rows:
            if row["slug"] in seen_slugs:
                continue
            seen_slugs.add(row["slug"])
            uniq.append(row)
        return uniq, snap, "html"
    except Exception:
        return [], direct_url, "blocked"
