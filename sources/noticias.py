"""
sources/noticias.py — Fuentes de noticias LAFT (lavado de activos / financiación terrorismo).

Cada fuente devuelve un link a la búsqueda en el portal respectivo.
Los portales son JS-driven y bloquean scrapers, por lo que
se documenta la URL y se delega al usuario.
"""
from __future__ import annotations
import time
from urllib.parse import quote
from .base import Hit
from .registry import register


def _make_news_source(name: str, base_url: str, search_pattern: str):
    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        search_url = base_url + search_pattern.format(q=quote(nombre))
        return Hit(name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en {name.split('—')[1].strip()}",
                   status="not_implemented",
                   notice="Portal JS-driven. Click 'abrir fuente' para buscar.",
                   evidence_urls=[search_url],
                   elapsed_s=time.time()-t0)
    return fetch


# ---------- Fiscalía General de la Nación - LAFT ----------
@register
class NoticiasFiscaliaSource:
    name = "Noticias — Fiscalía General (LAFT)"
    source_url = "https://www.fiscalia.gov.co/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Fiscalía",
                   status="not_implemented",
                   notice="Para búsqueda real se requiere Fase 5 (browser). "
                          "Por ahora abrir la web de la Fiscalía.",
                   evidence_urls=[
                       f"https://www.fiscalia.gov.co/busqueda/?q={quote(nombre)}",
                       "https://www.fiscalia.gov.co/"],
                   elapsed_s=time.time()-t0)


# ---------- Policía Nacional - LAFT ----------
@register
class NoticiasPoliciaSource:
    name = "Noticias — Policía Nacional (LAFT)"
    source_url = "https://www.policia.gov.co/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Policía",
                   status="not_implemented",
                   notice="Para búsqueda real se requiere Fase 5 (browser). "
                          "Por ahora abrir la web de la Policía.",
                   evidence_urls=[
                       f"https://www.policia.gov.co/buscar?q={quote(nombre)}",
                       "https://www.policia.gov.co/"],
                   elapsed_s=time.time()-t0)


# ---------- Presidencia - LAFT ----------
@register
class NoticiasPresidenciaSource:
    name = "Noticias — Presidencia (LAFT)"
    source_url = "https://www.presidencia.gov.co/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Presidencia",
                   status="not_implemented",
                   notice="Click 'abrir fuente' para buscar en comunicados.",
                   evidence_urls=[
                       f"https://www.presidencia.gov.co/buscar?q={quote(nombre)}",
                       "https://www.presidencia.gov.co/prensa",
                   ],
                   elapsed_s=time.time()-t0)


# ---------- Procuraduría - LAFT ----------
@register
class NoticiasProcuraduriaSource:
    name = "Noticias — Procuraduría General (LAFT)"
    source_url = "https://www.procuraduria.gov.co/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Procuraduría",
                   status="not_implemented",
                   notice="Click 'abrir fuente' para buscar en boletines.",
                   evidence_urls=[
                       f"https://www.procuraduria.gov.co/buscar?q={quote(nombre)}",
                       "https://www.procuraduria.gov.co/"],
                   elapsed_s=time.time()-t0)


# ---------- Insight Crime ----------
@register
class InsightCrimeSource:
    name = "Insight Crime — Crimen Organizado"
    source_url = "https://insightcrime.org/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre.",
                       elapsed_s=time.time()-t0)
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre}' en Insight Crime",
                   status="not_implemented",
                   notice="Click 'abrir fuente' para buscar en perfiles.",
                   evidence_urls=[
                       f"https://insightcrime.org/?s={quote(nombre)}",
                       "https://insightcrime.org/"],
                   elapsed_s=time.time()-t0)
