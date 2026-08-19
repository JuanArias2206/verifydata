"""
sources/especializados.py — Fuentes especializadas varias (Fase 2 base).
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from urllib.parse import quote
from .base import Hit
from .registry import register
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


# ---------- FCPA Stanford ----------
# ---------- FCPA Stanford ----------
@register
class FcpaStanfordSource:
    name = "FCPA — Foreign Corrupt Practices Act (Stanford)"
    source_url = "https://fcpa.stanford.edu/enforcement-actions.html"
    category = "Corrupción internacional"
    requires_captcha = True
    captcha_type = "login"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   "CONSULTA MANUAL REQUERIDA: exige cuenta/login",
                   status="requires_login",
                   notice=f"Requiere login en Stanford FCPA. Abrir "
                          f"{self.source_url} para buscar '{nombre}'.",
                   captcha_required=True,
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)


# ---------- SIRNA — Abogados ----------
@register
class SirnaSource:
    name = "SIRNA — Registro Nacional de Abogados"
    source_url = "https://sirna.ramajudicial.gov.co/"
    category = "Otros registros especializados"
    requires_captcha = True
    captcha_type = "login"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   "CONSULTA MANUAL REQUERIDA: exige cuenta/login",
                   status="requires_login",
                   notice=f"Requiere login en SIRNA. Abrir {self.source_url} "
                          f"para buscar.",
                   captcha_required=True,
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)


# ---------- Junta Central de Contadores ----------
# DEDUP 2026-07-02: DESREGISTRADA. La fuente canónica es
# `datos_abiertos.py::JccSancionadosDatosSource` — el Registro de
# Sanciones de la JCC es dato abierto (datos.gov.co fs36-azrv), no
# requiere el trámite con pago del certificado.
# @register
class JccContadoresSource:
    name = "JCC — Junta Central de Contadores (Contadores Sancionados)"
    source_url = "https://www.jcc.gov.co/mis-tramites/certificado-digital"
    category = "Otros registros especializados"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre. Trámite con pago ($82.000 COP).",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA (trámite con pago): '{nombre}' en JCC",
                   status="requires_payment",
                   notice="Trámite con pago. Solo aplica para contadores públicos.",
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)


# ---------- PACO — Portal Anticorrupción ----------
# El portal PACO (portal.paco.gov.co) es una SPA que consume una API pública
# de Azure APIM (paco-api-v2-prod.azure-api.net/paco-v2). Verificado 2026-07-02:
#   - GET secop/list_contractors/{doc}?limit=N&sort=COUNT  → contratistas por
#     documento (NIT/cédula). ABIERTO, sin captcha ni subscription key.
#   - siri/sanctions/contractors/{doc}   → sanciones disciplinarias (SIRI)
#   - secop/penalty/contractors/{doc}    → sanciones penales
#   - fiscal/contractors/{doc}           → responsabilidad fiscal
#   - secop/contract/contractors/{doc}   → contratos (requiere Captcha-token
#     hCaptcha, sitekey abc4a788-...). Se resuelve con la cadena CapSolver→2captcha
#     SOLO si el documento resultó ser contratista (para no gastar captcha en
#     personas sin contratación pública).
PACO_API = "https://paco-api-v2-prod.azure-api.net/paco-v2"
PACO_SITEKEY = "abc4a788-c580-4110-b4e0-a7ae9fbed1ca"
PACO_PAGE = "https://portal.paco.gov.co/index.php?pagina=contratista"
PACO_HEADERS = {
    "Origin": "https://portal.paco.gov.co",
    "Referer": "https://portal.paco.gov.co/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}


def _paco_get(path: str, token: str | None = None, timeout: int = 20):
    """GET a la API de PACO. Devuelve (status, json|None). Nunca lanza."""
    import json as _json
    import urllib.request
    headers = dict(PACO_HEADERS)
    if token:
        headers["Captcha-token"] = token
    req = urllib.request.Request(f"{PACO_API}/{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
            try:
                return r.status, _json.loads(body)
            except Exception:
                return r.status, None
    except Exception as e:
        code = getattr(e, "code", None)
        return (code or 0), None


@register
class PacoSource:
    name = "PACO — Portal Anticorrupción de Colombia"
    source_url = PACO_PAGE
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = "hcaptcha"

    def _build_solver(self, injected):
        try:
            from config import load_config
            from solvers.factory import build_chain
            cfg = load_config()
            return build_chain(cfg, use_proxy=False,
                               timeout=cfg.get("captcha", {}).get("capsolver", {})
                                          .get("default_timeout", 180))
        except Exception:
            return injected

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        doc = re.sub(r"\D", "", str(cedula or ""))
        if not doc:
            return Hit(self.name, False, "",
                       notice="Requiere cédula/NIT para consultar PACO.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)

        # 1) list_contractors — abierto, sin captcha. ¿Figura como contratista?
        st, contractors = _paco_get(
            f"secop/list_contractors/{doc}?limit=10&sort=COUNT")
        if st in (403, 429):
            return Hit(self.name, False, "",
                       notice=f"PACO respondió {st} (rate-limit/bloqueo). "
                              "Reintentar más tarde.",
                       captcha_required=True,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
        contractors = contractors if isinstance(contractors, list) else []
        nombres = sorted({c.get("contractor_name", "").strip()
                          for c in contractors if c.get("contractor_name")})

        details: list[dict] = []
        if nombres:
            details.append({"documento": doc, "razon_social": " / ".join(nombres[:5])})

        # 2) Sanciones disciplinarias (SIRI) / penales / fiscales POR DOCUMENTO.
        #    Endpoints abiertos (HTTP 200) sin captcha. Se consultan SIEMPRE,
        #    sea o no contratista (una persona puede tener sanción sin contrato).
        hallazgos = []
        for path, etiqueta in (
            (f"siri/sanctions/contractors/{doc}?limit=20", "Sanción disciplinaria (SIRI)"),
            (f"secop/penalty/contractors/{doc}?limit=20", "Sanción penal"),
            (f"fiscal/contractors/{doc}?limit=20", "Responsabilidad fiscal"),
        ):
            _st, data = _paco_get(path)
            if isinstance(data, list) and data:
                for row in data[:10]:
                    if isinstance(row, dict):
                        resumen = "; ".join(f"{k}={v}" for k, v in list(row.items())[:4]
                                            if v not in (None, "", "N/A"))
                        details.append({etiqueta: resumen[:200]})
                        hallazgos.append(etiqueta)

        es_contratista = bool(nombres)
        n_sanc = len(hallazgos)

        if not es_contratista and n_sanc == 0:
            return Hit(self.name, False,
                       f"NO REGISTRA en PACO: el documento {doc} no figura como "
                       f"contratista del Estado (SECOP) ni con sanciones "
                       f"disciplinarias (SIRI), penales o fiscales asociadas.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)

        if n_sanc:
            resumen = (f"REGISTRA en PACO con {n_sanc} sanción(es)/hallazgo(s) — "
                       f"{', '.join(sorted(set(hallazgos)))}"
                       + (" (contratista del Estado)." if es_contratista else "."))
        else:
            resumen = (f"REGISTRA en PACO: el documento {doc} figura como "
                       f"contratista del Estado (SECOP). Sin sanciones "
                       f"disciplinarias/penales/fiscales detectadas.")

        return Hit(self.name, True, resumen, details,
                   evidence_urls=[self.source_url],
                   elapsed_s=time.time()-t0)
