"""
sources/pep.py — Personas Expuestas Políticamente (PEP).

Fuentes sin captcha:
  - SIGEP — Función Pública Colombia (directorio de servidores)
  - PEP Colombia — links a bases de datos oficiales
  - CIDOB — Barcelona Centre for International Affairs (PEP mundial)
  - PEP Internacionales — links a bases por país
"""
from __future__ import annotations
import time
import json
import urllib.request
import urllib.parse
from urllib.parse import quote
from .base import Hit
from .registry import register


# ---------- Screening PEP vía Wikidata (keyless, sin API key) ----------
# Wikidata es una base abierta y consultable que registra a jefes de estado,
# ministros, congresistas, magistrados, alcaldes, diplomáticos y militares de
# alto rango de todo el mundo. Consultar el nombre y verificar si corresponde
# a una persona (Q5) que ocupa/ocupó un cargo público (P39 = position held) o
# cuya ocupación (P106) es política/diplomática/judicial/militar es una
# pantalla PEP real y automatizable — a diferencia de un simple link.
_WD_API = "https://www.wikidata.org/w/api.php"
_WD_UA = {"User-Agent": "VerifyData/1.0 (compliance PEP screening)"}
_COLOMBIA_QID = "Q739"
# Ocupaciones (P106) que implican exposición política.
_PEP_OCCUPATIONS = {
    "Q82955": "político", "Q193391": "diplomático", "Q16533": "juez",
    "Q47064": "militar", "Q372436": "funcionario", "Q212238": "funcionario público",
    "Q1734662": "concejal", "Q30461": "presidente", "Q83307": "ministro",
    "Q486839": "parlamentario", "Q4175034": "gobernador", "Q30185": "alcalde",
}


def _wd_api(params: dict) -> dict:
    params = dict(params, format="json")
    url = _WD_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_WD_UA)
    with urllib.request.urlopen(req, timeout=18) as r:
        return json.load(r)


def _wd_claim_ids(claims: dict, prop: str) -> list[str]:
    out = []
    for c in claims.get(prop, []) or []:
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except Exception:
            pass
    return out


def wikidata_pep_screen(nombre: str, only_colombia: bool) -> dict:
    """Consulta Wikidata y determina si `nombre` corresponde a una Persona
    Expuesta Políticamente. Devuelve dict con: matched(bool), summary(str),
    details(list[dict]), evidence(str|None). Nunca lanza excepción."""
    try:
        r = _wd_api({"action": "wbsearchentities", "search": nombre,
                     "language": "es", "limit": 7, "type": "item"})
    except Exception as e:
        return {"matched": False, "summary": None, "details": [],
                "error": f"Wikidata no accesible: {type(e).__name__}: {e}",
                "evidence": None, "reviewed": 0}
    cand = [h["id"] for h in r.get("search", []) if h.get("id")]
    if not cand:
        return {"matched": False, "reviewed": 0, "details": [], "evidence": None,
                "summary": f"'{nombre}' NO figura como PEP: sin coincidencias en Wikidata."}
    try:
        ent = _wd_api({"action": "wbgetentities", "ids": "|".join(cand),
                       "props": "claims|descriptions|labels",
                       "languages": "es|en"})
    except Exception as e:
        return {"matched": False, "summary": None, "details": [],
                "error": f"Wikidata detalle no accesible: {e}",
                "evidence": None, "reviewed": len(cand)}
    matches = []
    for qid, e in (ent.get("entities", {}) or {}).items():
        cl = e.get("claims", {}) or {}
        if "Q5" not in _wd_claim_ids(cl, "P31"):   # solo personas humanas
            continue
        has_position = bool(cl.get("P39"))
        occ = _wd_claim_ids(cl, "P106")
        pol_occ = [_PEP_OCCUPATIONS[o] for o in occ if o in _PEP_OCCUPATIONS]
        if not (has_position or pol_occ):
            continue
        cit = _wd_claim_ids(cl, "P27")
        desc = ((e.get("descriptions", {}).get("es")
                 or e.get("descriptions", {}).get("en") or {}).get("value", ""))
        label = ((e.get("labels", {}).get("es")
                  or e.get("labels", {}).get("en") or {}).get("value", nombre))
        is_col = (_COLOMBIA_QID in cit) or ("colombia" in desc.lower())
        if only_colombia and not is_col:
            continue
        matches.append({
            "qid": qid, "label": label, "descripcion": desc,
            "ocupacion": ", ".join(pol_occ) or "cargo público",
            "cargo_registrado": "sí" if has_position else "no",
            "colombiano": "sí" if is_col else "no",
            "referencia": f"https://www.wikidata.org/wiki/{qid}",
        })
    ambito = "Colombia" if only_colombia else "internacional"
    if matches:
        best = matches[0]
        return {
            "matched": True, "reviewed": len(cand), "details": matches[:5],
            "evidence": best["referencia"],
            "summary": (f"COINCIDENCIA PEP {ambito}: '{best['label']}' — "
                        f"{best['descripcion'] or best['ocupacion']}. "
                        f"({len(matches)} coincidencia(s) verificada(s) en Wikidata)."),
        }
    return {"matched": False, "reviewed": len(cand), "details": [], "evidence": None,
            "summary": (f"'{nombre}' NO figura como PEP {ambito}: se revisaron "
                        f"{len(cand)} candidato(s) en Wikidata y ninguno es figura "
                        f"política/pública.")}


# ---------- SIGEP — Función Pública Colombia ----------
# DEDUP 2026-07-02: DESREGISTRADA. La fuente canónica es
# `browser_sources.py::SigepBrowserSource` (búsqueda real con Playwright).
# Este stub solo devolvía un link manual y duplicaba SIGEP en el PDF.
# @register
class SigepSource:
    name = "SIGEP — Función Pública Colombia"
    source_url = "https://www.funcionpublica.gov.co/web/sigep2/directorio"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula.",
                       elapsed_s=time.time()-t0)
        # SIGEP usa JS, la búsqueda directa requiere navegador
        # (Fase 5). Generamos link con el query.
        query = cedula or nombre
        return Hit(self.name, False,
                   f"Búsqueda: '{query}' en SIGEP",
                   notice="SIGEP usa JS. Click 'abrir fuente' para buscar.",
                   evidence_urls=[
                       f"https://www.funcionpublica.gov.co/web/sigep2/directorio?q={quote(query)}"],
                   elapsed_s=time.time()-t0)


# ---------- PEP Colombia (links agregados) ----------
@register
class PepColombiaSource:
    name = "PEP Colombia — Consulta agregada"
    source_url = "https://www.funcionpublica.gov.co/web/sigep2/directorio"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        res = wikidata_pep_screen(nombre, only_colombia=True)
        ev = [res["evidence"]] if res.get("evidence") else []
        ev.append(f"https://www.funcionpublica.gov.co/web/sigep2/directorio?q={quote(nombre)}")
        if res.get("error"):
            return Hit(self.name, False,
                       f"Búsqueda de '{nombre}' en fuentes PEP Colombia",
                       notice=f"{res['error']} — verificar manualmente en SIGEP.",
                       evidence_urls=ev, elapsed_s=time.time()-t0)
        return Hit(self.name, res["matched"], res["summary"],
                   details=res["details"],
                   evidence_urls=ev,
                   elapsed_s=time.time()-t0)


# ---------- CIDOB — PEP mundial ----------
@register
class CidobPepSource:
    name = "CIDOB — Barcelona Centre for International Affairs (PEP mundial)"
    source_url = "https://www.cidob.org/bios"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en CIDOB",
                   status="not_implemented",
                   notice="CIDOB es JS-driven. Click 'abrir fuente' para buscar.",
                   evidence_urls=[
                       f"https://www.cidob.org/bios?q={quote(nombre)}",
                       "https://www.cidob.org/bios"],
                   elapsed_s=time.time()-t0)


# ---------- PEP Internacionales (links) ----------
@register
class PepInternacionalSource:
    name = "PEP Internacionales — Consulta agregada"
    source_url = "https://www.offshoreleaks.icij.org/"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        res = wikidata_pep_screen(nombre, only_colombia=False)
        ev = [res["evidence"]] if res.get("evidence") else []
        ev.append(f"https://www.offshoreleaks.icij.org/search?q={quote(nombre)}")
        if res.get("error"):
            return Hit(self.name, False,
                       f"Búsqueda de '{nombre}' en fuentes PEP internacionales",
                       notice=f"{res['error']} — verificar manualmente.",
                       evidence_urls=ev, elapsed_s=time.time()-t0)
        return Hit(self.name, res["matched"], res["summary"],
                   details=res["details"],
                   evidence_urls=ev,
                   elapsed_s=time.time()-t0)
