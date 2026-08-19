"""
sources/rama_judicial.py — Rama Judicial de Colombia.

Fase 3: intentos reales con manejo de captcha/login.

  - SIUGJ: portal principal, requiere login
  - JEPMS: Juzgados de Ejecución de Penas (público)
  - Juzgados TYBA (Justicia XXI): público, captcha a veces
  - Consulta Procesos: portal público por nombre
"""
from __future__ import annotations
import re
import time
import requests
from urllib.parse import quote
from .base import Hit
from .registry import register


_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        from lists.downloaders import make_session
        _SESSION = make_session()
    return _SESSION


# ---------- Rama Judicial — Consulta de Procesos Nacional Unificada (CPNU) ----------
# "Rama Unificada": la fuente que dice si el sujeto es DEMANDANTE o DEMANDADO.
# API pública REAL en el puerto :448 (NO :443 — :443 responde IIS 406). Sin
# captcha, sin navegador. Verificado 2026-07-02 con Daniel Lorenzo Medina Salcedo
# (3 procesos: Demandante, Demandado, Tercero Interviniente) — coincide con el
# reporte de referencia TusDatos.
CPNU_API = ("https://consultaprocesos.ramajudicial.gov.co:448/api/v2/"
            "Procesos/Consulta/NombreRazonSocial")
CPNU_LINK = "https://consultaprocesos.ramajudicial.gov.co/"
CPNU_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Roles que implican riesgo penal (más grave que civil demandante/demandado).
_PENAL_ROLES = ("procesado", "imputado", "indiciado", "condenado", "acusado",
                "sindicado")


def _cpnu_consulta(nombre: str, max_pages: int = 5) -> tuple[list[dict], int]:
    """Consulta procesos por nombre en la CPNU. Devuelve (procesos, total)."""
    import json as _json
    import urllib.request
    import urllib.parse
    procesos: list[dict] = []
    total = 0
    for page in range(1, max_pages + 1):
        qs = urllib.parse.urlencode({
            "nombre": nombre.strip(), "tipoPersona": "nat",
            "SoloActivos": "false", "pagina": page})
        req = urllib.request.Request(
            f"{CPNU_API}?{qs}",
            headers={"Accept": "application/json", "User-Agent": CPNU_UA,
                     "Referer": CPNU_LINK})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = _json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            break
        pr = data.get("procesos") or []
        procesos.extend(pr)
        pag = data.get("paginacion") or {}
        total = pag.get("cantidadRegistros", len(procesos)) or len(procesos)
        if page >= (pag.get("cantidadPaginas") or 1):
            break
    return procesos, total


def _split_parties(quien: str) -> list[str]:
    """Una entrada 'Rol: A, B, C' de sujetosProcesales puede listar VARIAS
    personas separadas por coma / 'Y' / punto-y-coma. Devuelve los nombres
    INDIVIDUALES para evaluarlos por separado.

    Esto es CLAVE: sin separar, los tokens del nombre consultado podían
    aparecer REPARTIDOS entre varias personas distintas de la misma parte
    (p.ej. 'JOSE TAMAYO MOSQUERA, JHON JAIRO GOMEZ FORERO, JAIRO FERNANDO
    MARTINEZ') y dar un falso 'match' de 'JHON JAIRO TAMAYO MARTINEZ'.
    """
    parts = re.split(r"[,;/]|\s+Y\s+", quien or "")
    return [p.strip() for p in parts if p and p.strip()]


def _is_contiguous_subseq(q: list[str], r: list[str]) -> bool:
    """True si la secuencia de tokens `q` aparece como sublista CONTIGUA y
    EN ORDEN dentro de `r` (p.ej. ['JHON','JAIRO','TAMAYO','MARTINEZ'] dentro
    de ['JHON','JAIRO','TAMAYO','MARTINEZ','GOMEZ']). Exige mismo orden y
    adyacencia — no basta con que los tokens estén presentes sueltos."""
    if not q or len(q) > len(r):
        return False
    for i in range(len(r) - len(q) + 1):
        if r[i:i + len(q)] == q:
            return True
    return False


def _roles_de(nombre: str, sujetos: str) -> list[str]:
    """Roles del sujeto consultado en 'sujetosProcesales' (formato
    'Rol: NOMBRE, NOMBRE | Rol: NOMBRE | ...'). Solo cuenta el rol si el
    nombre consultado aparece como secuencia EXACTA y en ORDEN en ALGUNA de
    las personas individuales de esa parte."""
    q = _rues_name_seq(nombre)
    roles = []
    for seg in (sujetos or "").split("|"):
        if ":" not in seg:
            continue
        rol, _, quien = seg.partition(":")
        for party in _split_parties(quien):
            if _is_contiguous_subseq(q, _rues_name_seq(party)):
                roles.append(rol.strip())
                break
    return roles


def _match_strength(nombre: str, sujetos: str) -> tuple[str | None, str]:
    """Qué tan fuerte es la coincidencia del sujeto consultado en el proceso.

    Política estricta (2026-07-06): la CPNU busca por nombre de forma laxa y
    puede devolver procesos donde los tokens del nombre están repartidos entre
    VARIAS personas distintas de una misma parte. Aquí se filtra a posteriori
    exigiendo que el nombre aparezca como SECUENCIA EXACTA Y EN ORDEN dentro de
    UNA sola persona.

    Devuelve (strength, nombre_en_fuente):
      - "full":    el nombre consultado aparece contiguo y en orden en una
                   persona individual → coincidencia real.
      - "partial": todos los tokens del nombre están en UNA sola persona pero
                   en otro orden/con palabras intercaladas → posible homónimo.
      - None:      no aparece en ninguna persona individual (tokens repartidos
                   entre varias personas, u otro criterio de la búsqueda).
    """
    q = _rues_name_seq(nombre)
    if not q:
        return None, ""
    qset = set(q)
    partial_name = ""
    for seg in (sujetos or "").split("|"):
        if ":" not in seg:
            continue
        _, _, quien = seg.partition(":")
        for party in _split_parties(quien):
            r = _rues_name_seq(party)
            if not r:
                continue
            if _is_contiguous_subseq(q, r):
                return "full", party.strip()
            # Subconjunto dentro de UNA sola persona (no repartido) → homónimo
            if not partial_name and qset.issubset(set(r)):
                partial_name = party.strip()
    return ("partial", partial_name) if partial_name else (None, "")


@register
class RamaJudicialSiugjSource:
    name = "Rama Judicial — Procesos (demandante/demandado)"
    source_url = CPNU_LINK
    category = "Antecedentes judiciales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere nombre para consultar procesos "
                              "judiciales por Rama Unificada.",
                       evidence_urls=[CPNU_LINK],
                       elapsed_s=time.time()-t0)
        try:
            procesos, total = _cpnu_consulta(nombre)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Rama Judicial: error consultando la CPNU "
                              f"({type(e).__name__}: {e}).",
                       evidence_urls=[CPNU_LINK],
                       elapsed_s=time.time()-t0)

        if not procesos:
            return Hit(self.name, False,
                       f"NO REGISTRA procesos judiciales a nombre de "
                       f"'{nombre.strip()}' en la Rama Judicial (CPNU).",
                       status="nomatch_verified",
                       notes="La CPNU expone consulta por documento "
                             "(/Consulta/Identificacion) pero exige "
                             "autenticación (401); la verificación exacta "
                             "por cédula requiere consulta manual.",
                       evidence_urls=[CPNU_LINK],
                       elapsed_s=time.time()-t0)

        # FILTRO A POSTERIORI (2026-07-06): la CPNU busca por nombre de forma
        # laxa. Solo se reportan los procesos donde el nombre consultado
        # aparece como SECUENCIA EXACTA Y EN ORDEN en una persona individual
        # (coincidencia "full"). Los procesos con solo coincidencia parcial /
        # tokens repartidos NO se cuentan como registro (posibles homónimos).
        from collections import Counter
        role_counts: Counter = Counter()
        details: list[dict] = []
        penal = False
        n_full = n_partial = 0
        first_rad_full = ""
        matched_name_src = ""
        i_full = 0
        for p in procesos:
            sujetos = p.get("sujetosProcesales", "") or ""
            strength, quien = _match_strength(nombre, sujetos)
            if strength != "full":
                if strength == "partial":
                    n_partial += 1
                continue
            n_full += 1
            i_full += 1
            radicado = p.get("llaveProceso", "")
            first_rad_full = first_rad_full or radicado
            matched_name_src = matched_name_src or quien
            roles = _roles_de(nombre, sujetos) or ["(rol no identificado)"]
            for r in roles:
                role_counts[r] += 1
                if any(k in r.lower() for k in _PENAL_ROLES):
                    penal = True
            rol_txt = ", ".join(dict.fromkeys(roles))
            despacho = (p.get("despacho", "") or "").strip()
            fecha = (p.get("fechaProceso", "") or "")[:10]
            if i_full <= 25:
                details.append({
                    f"Proceso {i_full} — {rol_txt}":
                        f"{despacho} · {p.get('departamento','')} · {fecha} · "
                        f"rad. {radicado}"})

        # Sin coincidencia EXACTA de nombre → NO REGISTRA (aunque la CPNU haya
        # devuelto procesos con tokens sueltos de terceros).
        if n_full == 0:
            nota = ("La CPNU devolvió resultados pero NINGUNO corresponde al "
                    "nombre exacto y en orden consultado")
            if n_partial:
                nota += (f" ({n_partial} coincidencia(s) parcial(es) "
                         "descartada(s) como posible(s) homónimo(s))")
            return Hit(self.name, False,
                       f"NO REGISTRA procesos judiciales a nombre exacto de "
                       f"'{nombre.strip()}' en la Rama Judicial (CPNU).",
                       status="nomatch_verified",
                       notes=nota + ". El match exige el nombre completo en "
                             "el orden consultado dentro de una sola persona.",
                       evidence_urls=[CPNU_LINK],
                       elapsed_s=time.time()-t0)

        # Resumen HONESTO con los roles (findings.py clasifica por
        # 'demandado'→MEDIO / 'demandante'→BAJO / penal→ALTO).
        def _n(role_key):
            return sum(v for k, v in role_counts.items() if role_key in k.lower())
        n_demandado = _n("demandado")
        n_demandante = _n("demandante")
        partes = []
        if penal:
            partes.append("vinculado a proceso PENAL")
        if n_demandado:
            partes.append(f"DEMANDADO en {n_demandado}")
        if n_demandante:
            partes.append(f"DEMANDANTE en {n_demandante}")
        otros = sum(role_counts.values()) - n_demandado - n_demandante
        if otros > 0:
            partes.append(f"otros roles en {otros}")
        extra = (f" ({n_partial} coincidencia(s) parcial(es) descartada(s) "
                 "como posible homónimo)" if n_partial else "")
        resumen = (f"REGISTRA {n_full} proceso(s) judicial(es) con nombre "
                   f"EXACTO coincidente — " + "; ".join(partes) + "."
                   + extra + " Búsqueda por nombre (verificar por radicado).")
        hit = Hit(self.name, True, resumen, details,
                  notice="Coincidencia por NOMBRE EXACTO en la Rama Judicial. "
                         "Verificar el radicado en 'abrir fuente'.",
                  evidence_urls=[CPNU_LINK],
                  elapsed_s=time.time()-t0)
        hit.status = "match_probable"
        hit.confidence = "fuerte"
        hit.matched_name = matched_name_src
        hit.case_number = first_rad_full
        roles_top = [k for k, _ in role_counts.most_common(3)
                     if k != "(rol no identificado)"]
        if roles_top:
            hit.role = ", ".join(roles_top)
        return hit


# ---------- JEPMS — Juzgados de Ejecución de Penas ----------
@register
class JepmsSource:
    name = "JEPMS — Juzgados de Ejecución de Penas y Medidas de Seguridad"
    source_url = "https://procesojudicial.ramajudicial.gov.co/justicia21/Administracion/Ciudadanos/frmConsulta.aspx"
    category = "Antecedentes judiciales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula.",
                       elapsed_s=time.time()-t0)
        # Generar URL de búsqueda en cada ciudad
        ciudades = ["Bogota","Medellin","Cali","Barranquilla","Cartagena",
                   "Bucaramanga","Pereira","Manizales","Armenia",
                   "Ibague","Tunja","Neiva","Pasto","Popayan",
                   "Villavicencio","SantaMarta","Buga","Palmira",
                   "Valledupar","Monteria","Quibdo","Florencia",
                   "Riohacha","Sincelejo","Yopal","Mocoa","Arauca",
                   "SanAndres","Cucuta","Tunja","Manizales","Cali"]
        urls = [f"https://procesojudicial.ramajudicial.gov.co/justicia21/"
                f"Administracion/Ciudadanos/frmConsulta.aspx?"
                f"ciudad={c}&nombre={quote(nombre or '')}"
                f"&cedula={cedula or ''}" for c in ciudades[:5]]
        return Hit(self.name, False,
                   f"CONSULTA MANUAL REQUERIDA: '{nombre or cedula}' en JEPMS "
                   "(consulta por ciudad, sin API pública)",
                   status="not_implemented",
                   notice="Click 'abrir fuente' para consultar JEPMS por ciudad. "
                          "Los procesos por nombre/documento se cubren vía "
                          "Rama Judicial Unificada (CPNU).",
                   evidence_urls=urls,
                   elapsed_s=time.time()-t0)


# ---------- Juzgados TYBA — Justicia XXI ----------
@register
class JuzgadosTybaSource:
    name = "Juzgados TYBA — Justicia XXI (procesos)"
    source_url = "https://procesojudicial.ramajudicial.gov.co/justicia21/Administracion/Ciudadanos/frmConsulta.aspx?opcion=consulta"
    category = "Antecedentes judiciales"
    requires_captcha = True
    captcha_type = "image"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        # URL prellenada
        url = (f"https://procesojudicial.ramajudicial.gov.co/justicia21/"
               f"Administracion/Ciudadanos/frmConsulta.aspx?opcion=consulta"
               f"&nombre={quote(nombre or '')}&cedula={cedula or ''}")
        return Hit(self.name, False,
                   "NO FUE POSIBLE CONSULTAR: captcha visual obligatorio",
                   status="captcha_blocked", error_type="captcha",
                   notice="Requiere captcha visual. Completar manualmente. "
                          "Los procesos por nombre se cubren vía Rama "
                          "Judicial Unificada (CPNU).",
                   captcha_required=True,
                   evidence_urls=[url, self.source_url],
                   elapsed_s=time.time()-t0)


# ---------- RUES — Registro Único Empresarial y Social ----------
# --- RUES: endpoint moderno de búsqueda (elastic) ---
# La antigua SPA de RUES estaba protegida por reCAPTCHA v2 con IP binding
# (no automatizable). El RUES modernizado expone un endpoint elastic
# (`elasticprd.rues.org.co/api/ConsultasRUES/BusquedaAvanzadaRM`) que NO
# requiere captcha. El host bloquea con 403 a clientes externos (curl), pero
# responde 200 cuando la petición se hace DESDE el contexto del navegador que
# ya cargó www.rues.org.co (clearance del WAF por cookies/fingerprint).
# Verificado 2026-07-02: búsqueda por `nit` y por `razon` devuelve JSON real.
RUES_ELASTIC_URL = ("https://elasticprd.rues.org.co/api/ConsultasRUES/"
                    "BusquedaAvanzadaRM")
RUES_HOME = "https://www.rues.org.co/"
RUES_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _rues_norm_tokens(s: str) -> set[str]:
    """Normaliza un nombre a un conjunto de tokens (mayúsculas, sin acentos,
    solo letras, sin tokens de <=2 chars).

    NOTA: se conserva el set de tokens para compatibilidad con código que
    compara sin importar el orden (`_match_strength`, `_roles_de`). Para
    matching ESTRICTAMENTE en orden, usar `_rues_name_seq` + `_rues_seq_match`.
    """
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z ]", " ", s).upper()
    return {t for t in s.split() if len(t) > 2}


def _rues_name_seq(s: str) -> list[str]:
    """Normaliza un nombre a una SECUENCIA ordenada de tokens (mismo criterio
    que `_rues_norm_tokens` pero preservando el ORDEN de las palabras).

    Esto es crítico para RUES: el portal indexa personas naturales comerciantes
    con su nombre completo y el matching debe respetar el ORDEN de los tokens.
    Ejemplo: 'JUAN MANUEL GALLEGO ARIAS' es la misma persona, pero
    'JUAN MANUEL ARIAS GALLEGO' NO (apellidos en orden distinto).
    """
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z ]", " ", s).upper()
    return [t for t in s.split() if len(t) > 2]


def _rues_seq_match(query_name: str, record_name: str) -> bool:
    """True si las dos secuencias de tokens (ordenadas) son EXACTAMENTE
    iguales — misma cantidad de tokens y mismo token en cada posición.

    Política (2026-07-02, solicitada por el usuario):
      - RUES: si la cédula matchea con un registro pero el nombre NO
        coincide en orden EXACTO, el registro NO corresponde a la persona
        consultada y se descarta del resultado.
      - Antes (set membership) 'Juan Manuel Gallego Arias' matcheaba con
        'Juan Manuel Arias Gallego' — falso positivo. Esta versión corrige
        eso.
    """
    q = _rues_name_seq(query_name)
    r = _rues_name_seq(record_name)
    if not q or not r:
        return False
    return q == r


def _rues_name_matches(query_name: str, record_name: str) -> bool:
    """True si los nombres corresponden a la MISMA persona, con
    coincidencia ESTRICTA en orden y composición.

    Cambio 2026-07-02: antes era `q.issubset(r) or r.issubset(q)` (set
    membership sin orden). Ahora exige secuencia EXACTA — mismo número
    de tokens y mismo token en cada posición. Esto es lo correcto para
    RUES: la cédula debe corresponder al mismo nombre completo.

    Para matching flexible (sin orden), usar `_rues_norm_tokens` directo.
    """
    return _rues_seq_match(query_name, record_name)


def _rues_query(body: dict) -> dict | None:
    """Ejecuta una búsqueda en el endpoint elastic de RUES desde el contexto
    de un navegador (que pasa el WAF). Devuelve el JSON parseado o None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(user_agent=RUES_UA, locale="es-CO",
                                      ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(RUES_HOME, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3500)   # clearance del WAF
            res = page.evaluate(
                """async (args) => {
                  try {
                    const r = await fetch(args.url, {
                      method: 'POST',
                      headers: {'Content-Type': 'application/json',
                                'Accept': 'application/json'},
                      body: JSON.stringify(args.body)});
                    const t = await r.text();
                    let j = null; try { j = JSON.parse(t); } catch(e) {}
                    return {status: r.status, json: j, text: t.slice(0, 400)};
                  } catch (e) { return {error: String(e)}; }
                }""",
                {"url": RUES_ELASTIC_URL, "body": body})
            return res
        finally:
            try: browser.close()
            except Exception: pass
    finally:
        try: pw.stop()
        except Exception: pass


@register
class RuesSource:
    """RUES — Registro Único Empresarial y Social (Confecámaras).

    Consulta real (sin captcha) contra el endpoint elastic moderno de RUES,
    ejecutada desde el contexto de un navegador para pasar el WAF. Busca:
      1) por NIT/cédula (coincidencia exacta del documento consultado), y
      2) por razón social (nombre) para detectar registros mercantiles a
         nombre de la persona (homónimos = informativo).

    matched=True cuando el documento consultado figura inscrito en el RUES
    (persona natural comerciante o jurídica). Categoría: registros básicos.
    """
    name = "RUES — Registro Único Empresarial y Social"
    source_url = "https://www.rues.org.co/"
    category = "Identidad y registros básicos"
    requires_captcha = False
    captcha_type = None

    def _rows(self, res: dict | None) -> list[dict]:
        if not res or not isinstance(res, dict):
            return []
        j = res.get("json")
        if not isinstance(j, dict):
            return []
        return j.get("registros") or []

    def _fmt(self, r: dict) -> dict:
        return {
            "razon_social": r.get("razon_social", ""),
            "nit": r.get("nit", ""),
            "matricula": r.get("matricula", ""),
            "camara": r.get("nom_camara", ""),
            "organizacion": r.get("organizacion_juridica", ""),
            "estado_matricula": r.get("estado_matricula", ""),
            "ultimo_ano_renovado": r.get("ultimo_ano_renovado", ""),
        }

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere cédula/NIT o nombre.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return Hit(self.name, False, "",
                       notice="Playwright no instalado — no se puede consultar "
                              "el endpoint de RUES (bloqueado a clientes "
                              "externos). Click 'abrir fuente' para consulta "
                              "manual.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)

        doc = re.sub(r"\D", "", str(cedula or ""))
        try:
            # 1) Búsqueda por DOCUMENTO (la identidad real en RUES).
            res_doc = _rues_query({"nit": doc, "start": 0, "length": 20}) if doc else None
            # Fast-fail ante rate-limit / bloqueo anti-bot (importante con
            # varios usuarios consultando a la vez): NO confundir 429/403 con
            # "NO REGISTRA".
            if res_doc and res_doc.get("status") in (403, 429):
                return Hit(self.name, False, "",
                           notice=(f"RUES respondió {res_doc['status']} "
                                   "(rate-limit / anti-bot) — no se pudo "
                                   "confirmar. Reintentar más tarde o consultar "
                                   "manualmente."),
                           captcha_required=True,
                           evidence_urls=[self.source_url],
                           elapsed_s=time.time()-t0)
            by_doc = self._rows(res_doc)
            if by_doc:
                # Política 2026-07-02: SOLO reportar `matched=True` para
                # registros cuyo NOMBRE coincida en orden EXACTO con el
                # consultado. Si por cédula hay registros pero ninguno
                # matchea el nombre → descartar y reportar `NO REGISTRA`
                # (el match de cédula sin match de nombre es un falso
                # positivo: típicamente índice antiguo, persona jurídica
                # con NIT = cédula, o registro a nombre de tercero).
                kept = ([r for r in by_doc
                         if _rues_name_matches(nombre or "", r.get("razon_social", ""))]
                        if nombre else list(by_doc))
                if not kept:
                    nombres_vistos = sorted({
                        r.get("razon_social", "")
                        for r in by_doc if r.get("razon_social")
                    })[:5]
                    return Hit(
                        self.name, False,
                        (f"NO REGISTRA en RUES: el documento {doc} figura "
                         f"asignado a {len(by_doc)} matrícula(s), pero NINGUNA "
                         f"coincide en orden con el nombre consultado "
                         f"('{nombre.strip()}'). Se descartaron los registros "
                         f"para evitar falsos positivos."),
                        notice=("RUES indexa por documento; sin coincidencia "
                                "exacta del nombre en orden, no se puede "
                                "confirmar identidad. Registros descartados: "
                                + ", ".join(nombres_vistos)),
                        evidence_urls=[self.source_url],
                        elapsed_s=time.time()-t0)
                # Nombre verificado: reportar SOLO los registros que
                # coinciden (no los homónimos de la misma cédula).
                details = [self._fmt(r) for r in kept[:10]]
                estados = ", ".join(sorted({d["estado_matricula"]
                                            for d in details if d["estado_matricula"]}))
                summary = (
                    f"REGISTRA en RUES: el documento {doc} figura inscrito con "
                    f"{len(kept)} matrícula(s) mercantil(es) a nombre de "
                    f"'{nombre.strip()}'"
                    + (f" (estado: {estados})" if estados else "") + "."
                )
                return Hit(
                    self.name, True,
                    summary,
                    details=details,
                    evidence_urls=[self.source_url],
                    elapsed_s=time.time()-t0)

            # 2) Sin registro por cédula. NO buscamos por nombre para
            #    evitar el falso positivo histórico: el matching por nombre
            #    trae homónimos (personas con mismo nombre y OTRA cédula),
            #    que NO son la persona consultada. El usuario solo pidió
            #    "no colocar el registro si la cédula no concuerda" — esto
            #    se respeta con step 1; un homónimo sin match de cédula
            #    tampoco debe aparecer como 'registra'.
            return Hit(
                self.name, False,
                (f"NO REGISTRA en RUES: el documento {doc or '—'} no figura "
                 f"inscrito con matrícula mercantil"
                 + (f" para '{nombre.strip()}'." if nombre else ".")),
                evidence_urls=[self.source_url],
                elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"RUES: error consultando el endpoint elastic "
                              f"({type(e).__name__}: {e}). Verificar manualmente.",
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
