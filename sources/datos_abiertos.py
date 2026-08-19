"""
sources/datos_abiertos.py — Fuentes colombianas vía datos.gov.co (Socrata)
y noticias vía Google News RSS.

Datasets oficiales verificados 2026-07-02:
  - SIRI — Antecedentes (Procuraduría General de la Nación)
      iaeu-rcn6 · 43.275 registros · numero_identificacion, nombres,
      cargo, sanciones, tipo_inhabilidad. Búsqueda POR DOCUMENTO (exacta)
      y por nombre. Complementa la consulta en línea con trivia: si el
      portal falla, este dataset sigue disponible.
  - Registro de Sanciones Contadores (Junta Central de Contadores)
      fs36-azrv · sanciones vigentes · c_dula, contador, proceso, resolución.
      REEMPLAZA el stub "requiere pago": la sanción es dato abierto.
  - Responsabilidad Fiscal (Contraloría General de la República)
      jr8e-e8tu · multas y sanciones fiscales · n_mero_de_identificaci_n.
      Complementa el certificado CGR (WAF): la vía de datos abiertos no
      tiene captcha.

Noticias:
  - Google News RSS (news.google.com/rss/search) — búsqueda del nombre
    exacto entre comillas, edición es-419/CO. Sin API key. Da la tabla de
    "Noticias reputacionales" estilo TusDatos (titular, medio, fecha, link).

Nota Socrata: sin app token hay rate limit blando; las consultas son
2-3 requests por corrida (count + where), muy por debajo del límite.
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
import urllib.request
from .base import Hit
from .registry import register

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SOCRATA_BASE = "https://www.datos.gov.co/resource"


def _socrata_get(path: str, timeout: int = 30):
    req = urllib.request.Request(f"{SOCRATA_BASE}/{path}",
                                 headers={"User-Agent": UA,
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _socrata_count(dataset_id: str) -> int:
    d = _socrata_get(f"{dataset_id}.json?$select=count(*)")
    try:
        return int(d[0]["count"])
    except Exception:
        return 0


def _norm_tokens(s: str) -> set[str]:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode(
        "ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z ]", " ", s).upper()
    return {t for t in s.split() if len(t) > 2}


class _SocrataSource:
    """Base: consulta un dataset Socrata por documento y por nombre.

    La búsqueda por documento produce match_exact; por nombre (full-text
    $q) produce match_probable si TODOS los tokens coinciden en el nombre
    del registro, possible_homonym si la coincidencia es parcial."""

    dataset_id = ""
    doc_field = ""          # campo del número de documento
    name_fields: tuple = () # campos que componen el nombre
    detail_fields: tuple = ()  # campos a mostrar en detalles
    min_dataset_rows = 10
    label = ""

    def _record_name(self, rec: dict) -> str:
        return " ".join(filter(None, (str(rec.get(f, "") or "").strip()
                                      for f in self.name_fields))).strip()

    def _details(self, recs: list[dict]) -> list[dict]:
        out = []
        for rec in recs[:15]:
            d = {}
            for f in self.detail_fields:
                v = str(rec.get(f, "") or "").strip()
                if v:
                    d[f.replace("_", " ")] = v[:160]
            if d:
                out.append(d)
        return out

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            total = _socrata_count(self.dataset_id)
            if total < self.min_dataset_rows:
                return Hit(self.name, False,
                           "NO FUE POSIBLE CONSULTAR: dataset de datos "
                           f"abiertos vacío/incompleto ({total} filas)",
                           status="dataset_missing",
                           error_type="dataset_missing",
                           dataset_records=total,
                           evidence_urls=[self.source_url],
                           elapsed_s=time.time() - t0)

            # 1) Por DOCUMENTO — coincidencia exacta. Los campos de Socrata
            # a veces vienen con padding de espacios ('9847638      '), así
            # que se intenta trim() en SoQL; si el dataset no lo soporta
            # (campo numérico → 400), se cae a igualdad directa.
            doc = re.sub(r"\D", "", cedula or "")
            recs_doc: list[dict] = []
            if doc and self.doc_field:
                where = urllib.parse.quote(
                    f"trim({self.doc_field})='{doc}'")
                try:
                    recs_doc = _socrata_get(
                        f"{self.dataset_id}.json?$where={where}&$limit=25")
                except Exception:
                    q = urllib.parse.urlencode({self.doc_field: doc,
                                                "$limit": 25})
                    recs_doc = _socrata_get(f"{self.dataset_id}.json?{q}")
            if recs_doc:
                hit = Hit(self.name, True,
                          f"REGISTRA: {len(recs_doc)} registro(s) por "
                          f"documento {doc} en {self.label} "
                          f"({total:,} registros)".replace(",", "."),
                          self._details(recs_doc),
                          status="match_exact", confidence="exacta",
                          matched_document=doc,
                          matched_name=self._record_name(recs_doc[0]),
                          dataset_records=total,
                          evidence_urls=[self.source_url],
                          elapsed_s=time.time() - t0)
                return hit

            # 2) Por NOMBRE — full-text
            recs_name: list[dict] = []
            if nombre:
                q = urllib.parse.urlencode({"$q": nombre, "$limit": 25})
                try:
                    recs_name = _socrata_get(f"{self.dataset_id}.json?{q}")
                except Exception:
                    recs_name = []
            if recs_name:
                qtok = _norm_tokens(nombre)
                full = [r for r in recs_name
                        if qtok.issubset(_norm_tokens(self._record_name(r)))]
                use = full or recs_name
                strong = bool(full)
                hit = Hit(self.name, True,
                          (f"REGISTRA {len(use)} registro(s) por NOMBRE en "
                           f"{self.label}"
                           + ("" if strong else
                              " — coincidencia PARCIAL (posible homónimo)")),
                          self._details(use),
                          status="match_probable" if strong
                          else "possible_homonym",
                          confidence="fuerte" if strong else "posible",
                          matched_name=self._record_name(use[0]),
                          dataset_records=total,
                          notes="Coincidencia por nombre sin verificación "
                                "de documento: confirmar identidad.",
                          evidence_urls=[self.source_url],
                          elapsed_s=time.time() - t0)
                return hit

            return Hit(self.name, False,
                       f"NO REGISTRA en {self.label} (consulta por documento "
                       f"y nombre; dataset verificado: {total:,} "
                       "registros)".replace(",", "."),
                       status="nomatch_verified",
                       matched_document=doc or None,
                       dataset_records=total,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time() - t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time() - t0)


@register
class ProcuraduriaSiriSource(_SocrataSource):
    name = "Procuraduría — SIRI Sanciones e Inhabilidades (Datos Abiertos)"
    source_url = "https://www.datos.gov.co/d/iaeu-rcn6"
    category = "Antecedentes disciplinarios"
    requires_captcha = False
    captcha_type = None
    dataset_id = "iaeu-rcn6"
    doc_field = "numero_identificacion"
    name_fields = ("primer_nombre", "segundo_nombre",
                   "primer_apellido", "segundo_apellido")
    detail_fields = ("numero_siri", "tipo_inhabilidad", "calidad_persona",
                     "numero_identificacion", "primer_nombre",
                     "primer_apellido", "cargo", "sanciones",
                     "lugar_hechos_departamento")
    min_dataset_rows = 1000
    label = "SIRI (Procuraduría, datos abiertos)"


@register
class JccSancionadosDatosSource(_SocrataSource):
    # Mismo nombre que el stub anterior: reemplaza la "consulta con pago"
    # por el dataset oficial de sanciones (dato abierto de la JCC).
    name = "JCC — Junta Central de Contadores (Contadores Sancionados)"
    source_url = "https://www.datos.gov.co/d/fs36-azrv"
    category = "Otros registros especializados"
    requires_captcha = False
    captcha_type = None
    dataset_id = "fs36-azrv"
    doc_field = "c_dula"
    name_fields = ("contador",)
    detail_fields = ("tipo", "contador", "c_dula", "proceso_jur_dico",
                     "resoluci_n", "fecha_resoluci_n", "meses", "fecha_fin")
    min_dataset_rows = 10
    label = "Registro de Sanciones JCC"


@register
class ContraloriaMultasDatosSource(_SocrataSource):
    name = "Contraloría — Multas y Sanciones Fiscales (Datos Abiertos)"
    source_url = "https://www.datos.gov.co/d/jr8e-e8tu"
    category = "Antecedentes disciplinarios"
    requires_captcha = False
    captcha_type = None
    dataset_id = "jr8e-e8tu"
    doc_field = "n_mero_de_identificaci_n"
    name_fields = ("raz_n_social_de_la_entidad",)
    detail_fields = ("raz_n_social_de_la_entidad", "n_mero_de_identificaci_n",
                     "tipo_de_sanci_n_multa", "tema_clasificaci_n_o_motivo",
                     "monto_de_la_multa_o_sanci", "fecha_de_resoluci_n_de_la")
    # El registro de multas CGR es legítimamente corto (~60 sanciones
    # publicadas); el guard solo protege contra dataset vacío/roto.
    min_dataset_rows = 20
    label = "Multas y Sanciones CGR (datos abiertos)"


@register
class PerdidaInvestiduraSource(_SocrataSource):
    name = "Pérdida de Investidura de Congresistas (Consejo de Estado)"
    source_url = "https://www.datos.gov.co/d/pywa-cq2f"
    category = "Antecedentes judiciales"
    requires_captcha = False
    captcha_type = None
    dataset_id = "pywa-cq2f"
    doc_field = ""  # el dataset no trae documento; solo búsqueda por nombre
    name_fields = ("demandado",)
    detail_fields = ("demandado", "cargo_del_investigado", "partido_pol_tico",
                     "circunscripci_n_territorial", "n_mero_de_radicaci_n",
                     "fecha_de_la_decisi_n_dd_mm", "decisi_n", "causales")
    min_dataset_rows = 20
    label = "Pérdida de Investidura (Consejo de Estado)"


# Léxico de respaldo (sin red) para clasificar sentimiento de titulares.
_NEG_WORDS = (
    "captur", "condena", "conden", "imputa", "proces", "fraude", "corrup",
    "soborno", "lavado", "narco", "delito", "sancion", "investiga",
    "escandalo", "escándalo", "detenid", "prófug", "profug", "homicid",
    "asesin", "extradi", "irregular", "millonari", "peculado", "cohecho",
    "estaf", "denunci", "carcel", "cárcel", "ilegal", "criminal", "prisión",
    "prision", "capturado", "acusa")
_POS_WORDS = (
    "premio", "reconocimiento", "galard", "logro", "inaugur", "posesion",
    "posesión", "nombrad", "eleg", "gana", "innovaci", "liderazgo",
    "destaca", "homenaje", "beca", "gradua")


def _sentimiento_local(titular: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", titular.lower()).encode(
        "ascii", "ignore").decode()
    neg = any(w.encode("ascii", "ignore").decode() in t for w in _NEG_WORDS)
    pos = any(w.encode("ascii", "ignore").decode() in t for w in _POS_WORDS)
    if neg and not pos:
        return "negativo"
    if pos and not neg:
        return "positivo"
    return "neutral"


def _clasificar_sentimiento(nombre: str, titulares: list[str]) -> list[str]:
    """Clasifica cada titular en negativo/neutral/positivo respecto a la
    reputación de `nombre`. Intenta el LLM configurado; si no hay clave o
    falla, cae al léxico local (determinístico, sin red)."""
    if not titulares:
        return []
    try:
        from config import load_config
        cfg = load_config()
        cap = cfg.get("captcha", {})
        key = ((cap.get("trivia", {}) or {}).get("anthropic_api_key")
               or (cap.get("anthropic", {}) or {}).get("api_key"))
        if key:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            enum = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titulares))
            prompt = (
                f"Clasifica el SENTIMIENTO REPUTACIONAL de cada titular de "
                f"noticia respecto a la persona '{nombre}'. Responde SOLO con "
                f"una línea por titular en el formato 'N:sentimiento' donde "
                f"sentimiento es exactamente negativo, neutral o positivo. "
                f"Negativo = implica delito, sanción, investigación o "
                f"escándalo. Sin explicaciones.\n\n{enum}")
            resp = client.messages.create(
                model="claude-haiku-4-5", max_tokens=300,
                messages=[{"role": "user", "content": prompt}])
            txt = resp.content[0].text
            out = ["neutral"] * len(titulares)
            for line in txt.splitlines():
                m = re.match(r"\s*(\d+)\s*[:.\-]\s*(negativo|neutral|positivo)",
                             line.strip().lower())
                if m:
                    idx = int(m.group(1)) - 1
                    if 0 <= idx < len(out):
                        out[idx] = m.group(2)
            return out
    except Exception:
        pass
    return [_sentimiento_local(t) for t in titulares]


# ---------- Noticias — Google News RSS ----------

@register
class GoogleNewsSource:
    name = "Noticias — Búsqueda en medios (Google News)"
    source_url = "https://news.google.com/"
    category = "Reputacional y noticias"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "", notice="Requiere nombre.",
                       elapsed_s=time.time() - t0)
        try:
            q = urllib.parse.quote(f'"{nombre.strip()}"')
            url = (f"https://news.google.com/rss/search?q={q}"
                   f"&hl=es-419&gl=CO&ceid=CO:es-419")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                xml = r.read().decode("utf-8", "ignore")
            items = re.findall(r"<item>(.*?)</item>", xml, re.S)
            details = []
            for it in items[:12]:
                title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?"
                                  r"</title>", it, re.S)
                link = re.search(r"<link>(.*?)</link>", it, re.S)
                date = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
                src = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
                t = (title.group(1) if title else "").strip()
                if not t:
                    continue
                details.append({
                    "titular": t[:150],
                    "medio": (src.group(1) if src else "")[:60],
                    "fecha": (date.group(1) if date else "")[:25],
                    "link": (link.group(1) if link else "")[:180],
                })
            if details:
                # Sentimiento por titular (estilo TusDatos: positivo/neutral/
                # negativo). Usa el LLM ya configurado; si falla, neutral.
                sentimientos = _clasificar_sentimiento(
                    nombre.strip(), [d["titular"] for d in details])
                counts = {"negativo": 0, "neutral": 0, "positivo": 0}
                for d, s in zip(details, sentimientos):
                    d["sentimiento"] = s
                    counts[s] = counts.get(s, 0) + 1
                adverso = counts.get("negativo", 0)
                resumen = (f"{len(details)} noticia(s) mencionan '{nombre.strip()}' "
                           f"({counts.get('negativo',0)} negativa(s), "
                           f"{counts.get('neutral',0)} neutral(es), "
                           f"{counts.get('positivo',0)} positiva(s)).")
                return Hit(self.name, True, resumen, details,
                           status="possible_homonym", confidence="posible",
                           notes="Menciones por nombre en prensa: alta "
                                 "probabilidad de homónimos y contexto "
                                 "variado. Sentimiento estimado "
                                 "automáticamente; revisar cada titular."
                                 + (" HAY titulares con sentimiento NEGATIVO."
                                    if adverso else ""),
                           evidence_urls=[
                               f"https://news.google.com/search?q={q}"
                               "&hl=es-419&gl=CO"],
                           elapsed_s=time.time() - t0)
            return Hit(self.name, False,
                       f"0 noticias con el nombre exacto '{nombre.strip()}' "
                       "en Google News (es-419/CO).",
                       status="nomatch_verified",
                       evidence_urls=[
                           f"https://news.google.com/search?q={q}"
                           "&hl=es-419&gl=CO"],
                       elapsed_s=time.time() - t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       error=f"{type(e).__name__}: {e}",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time() - t0)
