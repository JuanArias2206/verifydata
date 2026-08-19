"""
sources/opensanctions_lists.py — Listas internacionales adicionales vía
mirrors diarios de OpenSanctions (mismo mecanismo que worldbank_debarred).

Cierra los gaps frente al reporte de referencia TusDatos:
  - BID (Comité de Sanciones del Grupo BID)         → iadb_sanctions
  - ADB (Asian Development Bank sanctions)          → adb_sanctions
  - EBRD (BERD ineligible entities)                 → ebrd_ineligible
  - EEUU Depto. de Estado — FTO (org. terroristas)  → us_state_terrorist_orgs
  - EEUU Consolidated Screening List (Entity List,
    Unverified List, AECA/DDTC debarred, ISN, MEU)  → us_trade_csl

Todas usan el guard de datasets (`ensure_dataset`): un dataset ausente o
vacío JAMÁS se reporta como "0 coincidencias". Verificado 2026-07-02:
iadb/adb/ebrd/fto/csl responden 200 (CSL: 23.956 filas).
"""
from __future__ import annotations
import time
from .base import Hit
from .registry import register
from .internacionales import _list_lookup
from lists.downloaders import opensanctions_csv


def _make_fetch(list_key: str, slug: str, label: str, min_rows: int):
    downloader = opensanctions_csv(slug, min_rows=min_rows)

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            return _list_lookup(self.name, list_key, downloader, nombre,
                                label, min_rows=min_rows, t0=t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time() - t0)
    return fetch


@register
class IadbSanctionsSource:
    name = "BID — Comité de Sanciones (empresas y personas)"
    source_url = "https://www.iadb.org/en/who-we-are/transparency/sanctions-system/sanctioned-firms-and-individuals"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("iadb_sanctions", "iadb_sanctions",
                        "BID Sanciones", min_rows=50)


@register
class AdbSanctionsSource:
    name = "ADB — Banco Asiático de Desarrollo (sancionados)"
    source_url = "https://lnadbg4.adb.org/oai001p.nsf/sancALLPublic?OpenView"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("adb_sanctions", "adb_sanctions",
                        "ADB Sanciones", min_rows=50)


@register
class EbrdIneligibleSource:
    name = "BERD — Entidades Inelegibles (EBRD)"
    source_url = "https://www.ebrd.com/home/who-we-are/strategies-governance-compliance/ineligible-entities.html"
    category = "Corrupción internacional"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("ebrd_ineligible", "ebrd_ineligible",
                        "EBRD Ineligible", min_rows=10)


@register
class UsStateFtoSource:
    name = "EEUU Depto. de Estado — Organizaciones Terroristas (FTO)"
    source_url = "https://www.state.gov/foreign-terrorist-organizations/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("us_state_fto", "us_state_terrorist_orgs",
                        "US State FTO", min_rows=20)


@register
class UsTradeCslSource:
    name = "EEUU — Consolidated Screening List (Entity/Unverified/AECA)"
    source_url = "https://www.trade.gov/consolidated-screening-list"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("us_trade_csl", "us_trade_csl",
                        "Trade.gov CSL", min_rows=1000)


@register
class InterpolRedNoticesSource:
    # Reemplaza la consulta browser (WAF 403) por el mirror OpenSanctions,
    # que refresca a diario las Red Notices publicadas. Búsqueda por nombre.
    name = "INTERPOL — Red Notices (dataset)"
    source_url = "https://www.interpol.int/How-we-work/Notices/Red-Notices/View-Red-Notices"
    category = "Crimen y fugitivos"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("interpol_red_notices", "interpol_red_notices",
                        "INTERPOL Red Notices", min_rows=100)


@register
class CiaWorldLeadersSource:
    name = "PEP Internacional — CIA World Leaders"
    source_url = "https://www.cia.gov/resources/world-leaders/"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("us_cia_world_leaders", "us_cia_world_leaders",
                        "CIA World Leaders", min_rows=100)


@register
class EveryPoliticianSource:
    name = "PEP Internacional — EveryPolitician (parlamentarios)"
    source_url = "https://www.opensanctions.org/datasets/everypolitician/"
    category = "PEP (Personas Expuestas Políticamente)"
    requires_captcha = False
    captcha_type = None
    fetch = _make_fetch("everypolitician", "everypolitician",
                        "EveryPolitician", min_rows=1000)
