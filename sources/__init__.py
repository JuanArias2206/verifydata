"""sources — Paquete con adaptadores de las fuentes de datos.

Importa automáticamente todas las fuentes para que se registren
vía @register en el registry.

Orden de prioridad (último import gana):
  Fase 1: 7 fuentes base (SECOP×3, Registraduría, OFAC, UN, UK)
  Fase 2: 39 fuentes sin captcha
  Fase 3: 10 fuentes con captcha reescritas
  Fase 5: 4 fuentes con browser (SIGEP, INTERPOL, BIS, ICIJ)
"""
from .base import Hit, Source, CaptchaUnsolved, safe_fetch
from . import registry

from . import _existing       # 7 fuentes base (Fase 1)
from . import internacionales  # 6 fuentes (Fase 2)
from . import icij             # 5 fuentes ICIJ (Fase 2)
from . import europol          # 1 fuente EUROPOL Most Wanted (búsqueda HTML + screenshot) — antes de `wanted` para ganar al placeholder "EUROPOL — Most Wanted"
from . import wanted            # 11 fuentes wanted (Fase 2)
from . import pep               # 4 fuentes PEP (Fase 2)
from . import noticias          # 5 fuentes noticias (Fase 2)
from . import especializados   # 6 fuentes especializadas (Fase 2)
from . import dian              # 2 fuentes (Fase 3)
from . import procuraduria      # 1 fuente (Fase 3)
from . import policia           # 2 fuentes (Fase 3)
from . import policia_inhab     # 1 fuente (Fase 4) — Inhabilidades Ley 1918 (NIT fijo)
from . import contraloria       # 2 fuentes (Fase 3)
from . import defuncion         # 1 fuente — Registraduría Defunciones (Fase 5, sin captcha)
from . import rama_judicial     # 4 fuentes (Fase 3)
from . import browser_sources   # 5 fuentes (Fase 5) — ganan a las de Fase 2 con el mismo nombre

# ofac_web (OFAC Sanctions List Search form web oficial) — DEPRECATED 2026-06-13:
#   comentado en sources/ofac_web.py; queda como referencia. Reactivar
#   descomentando el @register y restaurando esta línea.
# from . import ofac_web
from . import bis_dpl           # BIS Denied Persons List (USA) — scraping HTML directo de la tabla "Full list"
from . import worldbank_debarred # World Bank Debarred Firms & Individuals (mirror OpenSanctions, cache 24h)
from . import dea_fugitives     # DEA Most Wanted Fugitives detallado (~540 prófugos, paginado HTML, cache 7d, con fallback a Wayback Machine si Akamai bloquea) — implementación real con datos (slug + descripción cargos) vs. el stub manual-link en `wanted`
from . import opensanctions_lists  # BID, ADB, EBRD, FTO, Trade.gov CSL (mirrors OpenSanctions, cache SQLite + guard)
from . import datos_abiertos    # SIRI Procuraduría, JCC sanciones, CGR multas (datos.gov.co) + Google News RSS
from . import supersociedades   # Supersociedades — Información General de Sociedades (consultaGeneralSociedades REST, reCAPTCHA v2 server-side; manual-link fallback cuando el captcha rechaza)

__all__ = ["Hit", "Source", "CaptchaUnsolved", "safe_fetch", "registry"]
