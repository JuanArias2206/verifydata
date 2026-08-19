"""
rues_nit.py — Resolución de una EMPRESA (por NIT) a sus REPRESENTANTES LEGALES
en el RUES, con iteración recursiva cuando un representante es a su vez una
persona jurídica.

A diferencia de `RuesSource` (en `rama_judicial.py`), que responde a una
consulta por persona (nombre/cédula) devolviendo un `Hit`, este módulo es la
pieza de la **búsqueda por NIT**: dado un NIT, encuentra la(s) matrícula(s)
mercantil(es), extrae sus representantes legales y los normaliza a una lista de
PERSONAS NATURALES a las que luego se les corre la búsqueda estándar en todas
las fuentes.

Descubrimiento de endpoints (verificado 2026-07-06, NIT 800170337):

  1) `POST elasticprd.rues.org.co/api/ConsultasRUES/BusquedaAvanzadaRM`
     body JSON plano `{"nit": "<doc>", "start":0, "length":20}`
     → `{"registros":[{id_rm, razon_social, cod_camara, matricula,
                        organizacion_juridica, estado_matricula, categoria}...]}`

  2) `POST elasticprd.rues.org.co/api/ConsultFacultadesXCamYMatricula`
     body JSON plano `{"codigo_camara":"<cod>", "matricula":"<mat 10 dígitos>"}`
     → texto/HTML del certificado con la tabla
        `CARGO   NOMBRE   IDENTIFICACION|CEDULA`
     ⚠ La matrícula DEBE ir con ceros a la izquierda hasta 10 dígitos
       (`"0016927912"`); sin padding el endpoint responde 200 vacío.

Ambos endpoints bloquean a clientes externos (403) pero responden 200 desde el
contexto de un navegador que ya cargó `www.rues.org.co` (clearance del WAF por
cookies/fingerprint) — mismo truco que usa `RuesSource._rues_query`.
"""
from __future__ import annotations

import html
import re
import time

RUES_HOME = "https://www.rues.org.co/"
RUES_BUSQUEDA_RM = ("https://elasticprd.rues.org.co/api/ConsultasRUES/"
                    "BusquedaAvanzadaRM")
RUES_FACULTADES = ("https://elasticprd.rues.org.co/api/"
                   "ConsultFacultadesXCamYMatricula")
RUES_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Marcadores que delatan una persona jurídica (para decidir recursión).
_JURIDICA_MARKERS = (
    "SAS", "S.A.S", "S A S", "LTDA", "LIMITADA", "S.A.", " SA ", "S A",
    "& CIA", "Y CIA", "ESAL", "SOCIEDAD", "FUNDACION", "FUNDACIÓN",
    "ASOCIACION", "ASOCIACIÓN", "CORPORACION", "CORPORACIÓN", "E.S.P",
    "E.U", "EMPRESA UNIPERSONAL", "COOPERATIVA", "S.C.A", "S EN C",
)
# Palabras de la columna `categoria`/`organizacion_juridica` que confirman
# que un registro es una persona jurídica con representantes propios.
_JURIDICA_CATEGORIA = ("SOCIEDAD", "PERSONA JURIDICA", "PERSONA JURÍDICA",
                       "ESAL", "SAS", "LTDA")

# Cargos reconocidos en la tabla del certificado (orden largo→corto al matchear).
_CARGOS = (
    "REPRESENTANTE LEGAL SUPLENTE", "REPRESENTANTE LEGAL PRINCIPAL",
    "PRIMER REPRESENTANTE LEGAL", "SEGUNDO REPRESENTANTE LEGAL",
    "REPRESENTANTE LEGAL", "PRIMER SUPLENTE", "SEGUNDO SUPLENTE",
    "SUPLENTE DEL GERENTE", "SUBGERENTE", "GERENTE GENERAL", "GERENTE",
    "PRESIDENTE", "VICEPRESIDENTE", "DIRECTOR EJECUTIVO", "DIRECTOR",
    "LIQUIDADOR", "APODERADO GENERAL", "APODERADO", "ADMINISTRADOR",
    "SUPLENTE", "REPRESENTANTE",
)


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _nit_variants(doc: str) -> list[str]:
    """Variantes de un NIT a probar en RUES, en orden de preferencia.

    Un NIT colombiano de persona jurídica tiene 9 dígitos de BASE + 1 dígito de
    verificación (DV). RUES/Supersociedades se consultan por la BASE (sin DV).
    El usuario suele pegar el NIT CON DV (p.ej. `9004184621` = base `900418462`
    + DV `1`), así que si viene con 10 dígitos probamos también sin el último.
    """
    doc = _digits(doc)
    out = [doc] if doc else []
    if len(doc) == 10:            # base(9) + DV(1) → probar la base
        base = doc[:9]
        if base not in out:
            out.append(base)
    return out


def _looks_juridica(nombre: str) -> bool:
    u = f" {(nombre or '').upper()} "
    return any(m in u for m in _JURIDICA_MARKERS)


def _parse_identificacion(id_text: str) -> tuple[str, bool]:
    """Normaliza la columna IDENTIFICACIÓN de un representante del RUES.

    Formatos reales:
      'N.I.T. No. 901116616 4'  → ('901116616', True)   # NIT: descarta el DV
      'P.P. No. 668746266'      → ('668746266', False)  # pasaporte
      'C.C. No. 79.123.456'     → ('79123456', False)   # cédula
      '7.415.830'               → ('7415830', False)
    Devuelve `(numero, es_nit)`. El NIT se devuelve SIN dígito de verificación
    porque RUES se consulta por el número base.
    """
    up = (id_text or "").upper()
    es_nit = "N.I.T" in up or "NIT" in up
    runs = re.findall(r"\d[\d.,]*", id_text or "")
    nums = [re.sub(r"\D", "", r) for r in runs]
    nums = [n for n in nums if n]
    if not nums:
        return "", es_nit
    if es_nit:
        # El cuerpo del NIT es el grupo más largo; el DV (grupo suelto de
        # 1 dígito) se descarta.
        return max(nums, key=len), True
    # Documento de persona natural (CC/CE/PP): unir los grupos (p.ej. una
    # cédula '7.415.830' llega como un solo grupo con puntos).
    return "".join(nums), False


def _parse_reps_tokenwise(lines: list[str], start: int) -> list[dict]:
    """Parser de respaldo (línea a línea) por si el layout de columnas no es
    fiable. Menos robusto con representantes persona-jurídica multilínea."""
    id_re = re.compile(r"(\d[\d\.\,\-]{3,})\s*$")
    cargos = sorted(_CARGOS, key=len, reverse=True)
    reps: list[dict] = []
    cur: dict | None = None
    for raw in lines[start:]:
        s = raw.strip()
        if not s or s == ".":
            continue
        u = s.upper()
        if (u.startswith("CERTIFICA") or "FACULTADES" in u or "LIMITACIONES" in u
                or u.startswith("NOMBRAD") or u.startswith("MEDIANTE")
                or u.startswith("POR ACTA") or u.startswith("ACTA ")):
            break
        cargo = next((c for c in cargos if u.startswith(c)), None)
        if cargo:
            rest = s[len(cargo):].strip()
            m = id_re.search(rest)
            ident, nombre = "", rest
            if m:
                ident = _digits(m.group(1))
                nombre = rest[:m.start()].strip()
            cur = {"cargo": cargo, "nombre": re.sub(r"\s+", " ", nombre).strip(),
                   "identificacion": ident, "es_empresa": _looks_juridica(nombre)}
            if cur["nombre"] or cur["identificacion"]:
                reps.append(cur)
        elif cur is not None:
            m = id_re.search(s)
            if m and not cur["identificacion"]:
                cur["identificacion"] = _digits(m.group(1))
                extra = s[:m.start()].strip()
                if extra and not any(ch.isdigit() for ch in extra):
                    cur["nombre"] = re.sub(r"\s+", " ",
                                           f"{cur['nombre']} {extra}").strip()
            elif not any(ch.isdigit() for ch in s) and len(s) < 40 and s.upper() == s:
                cur["nombre"] = re.sub(r"\s+", " ",
                                       f"{cur['nombre']} {s}").strip()
    return [r for r in reps if r.get("nombre")]


def parse_representantes(cert: str) -> list[dict]:
    """Parsea la tabla `CARGO / NOMBRE / IDENTIFICACION` del certificado de
    facultades del RUES. Devuelve `[{cargo, nombre, identificacion, es_empresa}]`.

    El certificado llega como HTML con `<br />` y `&nbsp;`. Es texto de ANCHO
    FIJO: CARGO, NOMBRE e IDENTIFICACIÓN son columnas alineadas por posición de
    carácter, y CADA columna puede envolverse en varias líneas de forma
    INDEPENDIENTE. Ej. representante persona jurídica (NIT 900579568):

        CARGO             NOMBRE                    IDENTIFICACIÓN
        Representante     COLOMBIAN        SHARED   N.I.T. No. 901116616 4
        Legal         -   SERVICES S A S
        Persona
        Juridica

    → cargo 'Representante Legal', nombre 'COLOMBIAN SHARED SERVICES S A S',
      identificación '901116616' (NIT → es_empresa=True → se itera ese NIT).

    Por eso se parsea POR COLUMNAS (offsets del encabezado), no por tokens: se
    detecta el inicio de un registro cuando la columna CARGO empieza con un
    cargo conocido, y las líneas siguientes acumulan cada columna hasta el
    próximo registro o el fin de la sección.
    """
    if not cert:
        return []
    txt = re.sub(r"<br\s*/?>", "\n", cert, flags=re.I)
    txt = html.unescape(txt.replace("&nbsp;", " "))
    lines = txt.split("\n")

    hdr_idx, header = None, ""
    for i, ln in enumerate(lines):
        u = ln.upper()
        if "CARGO" in u and "NOMBRE" in u and ("IDENTIFICA" in u or "CEDULA" in u
                                               or "CÉDULA" in u or "DOCUMENTO" in u):
            hdr_idx, header = i, ln
            break
    if hdr_idx is None:
        return []

    up_hdr = header.upper()
    col_nombre = up_hdr.find("NOMBRE")
    m_id = re.search(r"IDENTIFICA|C[EÉ]DULA|DOCUMENTO", up_hdr)
    col_id = m_id.start() if m_id else -1
    # Si los offsets de columna no son coherentes, usar el parser de respaldo.
    if not (0 < col_nombre < col_id):
        return _parse_reps_tokenwise(lines, hdr_idx + 1)

    cargos = sorted(_CARGOS, key=len, reverse=True)

    def _starts_cargo(cargo_col: str) -> bool:
        u = re.sub(r"\s+", " ", (cargo_col or "")).strip().upper()
        return any(u.startswith(c) for c in cargos)

    def _is_prose(text: str) -> bool:
        # Párrafo de acta ("Por Acta No. 36 del 15 de octubre de 2024, ...")
        # tiene varias palabras en minúscula; los fragmentos de columna
        # (cargo/nombre) son en mayúscula o Título, de 1-2 palabras.
        return len(re.findall(r"\b[a-záéíóúñ]{3,}\b", text)) >= 2

    raw_recs: list[dict] = []
    cur: dict | None = None
    for ln in lines[hdr_idx + 1:]:
        s = ln.strip()
        # Separador (línea vacía o ".") → CIERRA el registro actual. Así la
        # prosa del acta y un eventual segundo encabezado que vienen después
        # NO se acumulan dentro del representante anterior.
        if not s or s == ".":
            cur = None
            continue
        un = re.sub(r"\s+", " ", s).upper()   # normalizado (espacios múltiples)
        # Fin de TODA la sección de nombramientos.
        if ("FACULTADES" in un or "LIMITACIONES" in un
                or un.startswith("SON FUNCIONES")
                or un.startswith("CERTIFICA")):
            break
        cargo_col = ln[:col_nombre].strip()
        nombre_col = ln[col_nombre:col_id].strip()
        id_col = ln[col_id:].strip()
        if _starts_cargo(cargo_col):
            cur = {"cargo_raw": cargo_col, "nombre": nombre_col, "id_raw": id_col}
            raw_recs.append(cur)
        elif cur is not None:
            # Prosa (párrafo del acta) sin separador previo → cerrar registro.
            if _is_prose(s):
                cur = None
                continue
            if cargo_col:
                cur["cargo_raw"] += " " + cargo_col
            if nombre_col:
                cur["nombre"] += " " + nombre_col
            if id_col:
                cur["id_raw"] += " " + id_col
        # (si cur is None y no empieza cargo → prosa/encabezado repetido: ignorar)

    out: list[dict] = []
    for rec in raw_recs:
        nombre = re.sub(r"\s+", " ", rec["nombre"]).strip()
        if not nombre:
            continue
        num, es_nit = _parse_identificacion(rec["id_raw"])
        cu = rec["cargo_raw"].upper()
        es_empresa = (es_nit or _looks_juridica(nombre)
                      or "JURIDICA" in cu or "JURÍDICA" in cu)
        cargo = re.sub(r"\s+", " ", rec["cargo_raw"].replace("-", " ")).strip()
        cargo = re.sub(r"(?i)\bPERSONA\s+(JUR[IÍ]DICA|NATURAL)\b", "", cargo).strip()
        out.append({"cargo": cargo or "Representante", "nombre": nombre,
                    "identificacion": num, "es_empresa": es_empresa})
    return out


class RuesSession:
    """Sesión de navegador con clearance del WAF de RUES. Permite hacer varias
    consultas (BusquedaAvanzadaRM / ConsultFacultades) en un solo Chromium,
    lo cual es clave para la recursión (no relanzar navegador por empresa)."""

    def __init__(self, timeout_ms: int = 35000):
        self._timeout = timeout_ms
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"])
        ctx = self._browser.new_context(user_agent=RUES_UA, locale="es-CO",
                                        ignore_https_errors=True)
        self._page = ctx.new_page()
        self._page.goto(RUES_HOME, wait_until="domcontentloaded",
                        timeout=self._timeout)
        self._page.wait_for_timeout(3500)  # clearance del WAF
        return self

    def __exit__(self, *exc):
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass
        return False

    def _post(self, url: str, body: dict) -> dict:
        """POST JSON plano desde el contexto del navegador. Devuelve
        `{status, json, text}` o `{error}`."""
        return self._page.evaluate(
            """async (args) => {
              try {
                const r = await fetch(args.url, {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json',
                            'Accept': 'application/json'},
                  body: JSON.stringify(args.body)});
                const t = await r.text();
                let j = null; try { j = JSON.parse(t); } catch (e) {}
                return {status: r.status, json: j, text: t};
              } catch (e) { return {error: String(e)}; }
            }""",
            {"url": url, "body": body})

    def buscar_por_nit(self, nit: str) -> dict:
        return self._post(RUES_BUSQUEDA_RM,
                          {"nit": _digits(nit), "start": 0, "length": 20})

    def facultades(self, cod_camara: str, matricula: str) -> str:
        """Certificado de facultades (contiene la tabla de representantes).
        La matrícula se envía con ceros a la izquierda (10 dígitos)."""
        mat = _digits(matricula).zfill(10)
        res = self._post(RUES_FACULTADES,
                         {"codigo_camara": str(cod_camara), "matricula": mat})
        return res.get("text") or ""


def _es_registro_juridico(reg: dict) -> bool:
    blob = f"{reg.get('categoria','')} {reg.get('organizacion_juridica','')}".upper()
    return any(k in blob for k in _JURIDICA_CATEGORIA)


def _registro_activo_primero(registros: list[dict]) -> list[dict]:
    """Ordena poniendo las matrículas ACTIVAS primero (para preferir la
    sociedad vigente sobre versiones históricas del mismo NIT)."""
    def _key(r):
        est = (r.get("estado_matricula") or "").upper()
        return (0 if "ACTIV" in est else 1,)
    return sorted(registros, key=_key)


def consultar_empresa(session: RuesSession, nit: str) -> dict:
    """Consulta una empresa por NIT: matrícula(s) mercantil(es) + sus
    representantes legales. Devuelve:

        {"ok", "error", "nit", "razon_social", "estado", "camara",
         "registros": [...], "reps": [{cargo, nombre, identificacion}...]}
    """
    doc = _digits(nit)
    out = {"ok": False, "error": None, "nit": doc, "razon_social": "",
           "estado": "", "camara": "", "matricula": "", "organizacion": "",
           "categoria": "", "registros": [], "reps": []}
    if not doc:
        out["error"] = "NIT vacío"
        return out
    # Probar el NIT tal cual y, si trae DV (10 dígitos), también la base de 9.
    res = {}
    registros = []
    for variant in _nit_variants(doc):
        res = session.buscar_por_nit(variant)
        if res.get("status") in (403, 429):
            out["error"] = f"RUES respondió {res['status']} (rate-limit / anti-bot)"
            return out
        if res.get("error"):
            out["error"] = str(res["error"])[:200]
            return out
        registros = (res.get("json") or {}).get("registros") or []
        if registros:
            doc = variant           # el NIT que efectivamente resolvió en RUES
            out["nit"] = doc
            break
    # Solo personas jurídicas tienen representantes legales.
    juridicos = [r for r in registros if _es_registro_juridico(r)]
    out["registros"] = juridicos or registros
    if not juridicos:
        out["error"] = ("El documento no figura como persona jurídica en RUES "
                        if registros else "No figura matrícula mercantil en RUES")
        return out

    ordenados = _registro_activo_primero(juridicos)
    principal = ordenados[0]
    out["razon_social"] = principal.get("razon_social", "")
    out["estado"] = principal.get("estado_matricula", "")
    out["camara"] = principal.get("nom_camara", "") or principal.get("camara", "")
    # Datos de registro mercantil (para el encabezado del reporte de empresa).
    out["matricula"] = principal.get("matricula", "")
    out["organizacion"] = principal.get("organizacion_juridica", "")
    out["categoria"] = principal.get("categoria", "")

    seen_ids = set()
    for reg in ordenados:
        cod = reg.get("cod_camara") or reg.get("codigo_camara")
        mat = reg.get("matricula")
        if not (cod and mat):
            continue
        try:
            cert = session.facultades(cod, mat)
        except Exception as e:  # noqa: BLE001
            out.setdefault("warnings", []).append(
                f"facultades {mat}: {type(e).__name__}")
            continue
        for rep in parse_representantes(cert):
            key = (_digits(rep.get("identificacion")),
                   rep.get("nombre", "").upper())
            if key in seen_ids:
                continue
            seen_ids.add(key)
            rep["razon_social_empresa"] = out["razon_social"]
            rep["matricula"] = mat
            out["reps"].append(rep)
    out["ok"] = True
    return out


def datos_empresa(nit: str, session: RuesSession | None = None) -> dict:
    """Ruta rápida para la búsqueda POR EMPRESA (info de la compañía, estilo
    tusdatos): resuelve SOLO los datos de registro mercantil del NIT raíz
    (razón social, matrícula, estado, cámara, organización jurídica) y sus
    representantes legales directos, SIN recursión a subsidiarias.

    A diferencia de `resolver_nit` (que hace BFS recursivo para producir la
    lista de personas naturales a consultar una por una), aquí solo necesitamos
    la identidad de la empresa para correr UNA búsqueda por razón social + NIT
    en las fuentes de empresa. Devuelve el dict de `consultar_empresa`
    enriquecido con `ok`/`error`.
    """
    doc = _digits(nit)
    if not doc:
        return {"ok": False, "error": "Ingresa un NIT válido.", "nit": doc,
                "razon_social": "", "estado": "", "camara": "", "matricula": "",
                "organizacion": "", "categoria": "", "registros": [], "reps": []}
    _own = session is None
    if _own:
        try:
            session = RuesSession().__enter__()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "nit": doc, "razon_social": "", "estado": "",
                    "camara": "", "matricula": "", "organizacion": "",
                    "categoria": "", "registros": [], "reps": [],
                    "error": (f"No se pudo abrir el navegador para RUES "
                              f"({type(e).__name__}: {e}).")}
    try:
        return consultar_empresa(session, doc)
    finally:
        if _own:
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass


def resolver_nit(nit: str, max_depth: int = 2,
                 session: RuesSession | None = None) -> dict:
    """Resuelve un NIT a la lista de PERSONAS NATURALES a consultar, siguiendo
    recursivamente a los representantes que son a su vez personas jurídicas.

    Devuelve:
        {
          "ok": bool, "error": str|None, "nit": str,
          "empresa": str,                      # razón social raíz
          "tree": [                            # una entrada por empresa visitada
             {"nivel", "nit", "razon_social", "estado", "camara",
              "reps": [{cargo, nombre, identificacion, es_empresa}...],
              "error": str|None}
          ],
          "personas": [                        # personas naturales deduplicadas
             {"nombre", "cedula", "cargo", "empresa", "empresa_nit", "nivel"}
          ],
        }
    """
    doc = _digits(nit)
    result = {"ok": False, "error": None, "nit": doc, "empresa": "",
              "empresa_datos": {}, "tree": [], "personas": []}
    if not doc:
        result["error"] = "Ingresa un NIT válido."
        return result

    _own_session = session is None
    if _own_session:
        try:
            session = RuesSession().__enter__()
        except Exception as e:  # noqa: BLE001
            result["error"] = (f"No se pudo abrir el navegador para RUES "
                               f"({type(e).__name__}: {e}).")
            return result

    try:
        personas: dict[str, dict] = {}     # cedula -> persona (dedupe)
        visitados_nit: set[str] = {doc}
        # BFS por niveles de empresa.
        cola = [(doc, 0)]
        while cola:
            cur_nit, nivel = cola.pop(0)
            emp = consultar_empresa(session, cur_nit)
            node = {"nivel": nivel, "nit": cur_nit,
                    "razon_social": emp.get("razon_social", ""),
                    "estado": emp.get("estado", ""),
                    "camara": emp.get("camara", ""),
                    "matricula": emp.get("matricula", ""),
                    "organizacion": emp.get("organizacion", ""),
                    "categoria": emp.get("categoria", ""),
                    "reps": [], "error": emp.get("error")}
            if nivel == 0:
                result["empresa"] = emp.get("razon_social", "")
                result["empresa_datos"] = {
                    "nit": cur_nit,
                    "razon_social": emp.get("razon_social", ""),
                    "estado": emp.get("estado", ""),
                    "camara": emp.get("camara", ""),
                    "matricula": emp.get("matricula", ""),
                    "organizacion": emp.get("organizacion", ""),
                    "categoria": emp.get("categoria", ""),
                    "error": emp.get("error"),
                }
            for rep in emp.get("reps", []):
                rep_id = _digits(rep.get("identificacion"))
                nombre = rep.get("nombre", "")
                # Pista del parser: el representante es persona jurídica si su
                # identificación es un NIT o su nombre tiene marcador (SAS/LTDA…)
                # o el certificado lo marcó "Persona Jurídica".
                es_empresa = bool(rep.get("es_empresa")) or _looks_juridica(nombre)
                # Verificación fuerte SOLO cuando ya hay indicio de empresa:
                # ¿el id resuelve a una persona jurídica en RUES? (evita una
                # consulta extra por cada cédula de persona natural).
                if (es_empresa and rep_id and nivel < max_depth
                        and rep_id not in visitados_nit):
                    sub = consultar_empresa(session, rep_id)
                    if not (sub.get("ok") and sub.get("razon_social")):
                        # El indicio no se confirma en RUES → tratar como persona.
                        es_empresa = False
                node["reps"].append({
                    "cargo": rep.get("cargo", ""),
                    "nombre": nombre,
                    "identificacion": rep_id,
                    "es_empresa": es_empresa,
                })
                if es_empresa:
                    if rep_id and rep_id not in visitados_nit and nivel < max_depth:
                        visitados_nit.add(rep_id)
                        cola.append((rep_id, nivel + 1))
                    continue
                # Persona natural → candidata a búsqueda.
                if not rep_id:
                    continue
                if rep_id not in personas:
                    personas[rep_id] = {
                        "nombre": nombre,
                        "cedula": rep_id,
                        "cargo": rep.get("cargo", ""),
                        "empresa": node["razon_social"] or emp.get("razon_social", ""),
                        "empresa_nit": cur_nit,
                        "nivel": nivel,
                    }
            result["tree"].append(node)

        result["personas"] = list(personas.values())
        # ok si al menos resolvimos la empresa raíz sin error de red.
        root = result["tree"][0] if result["tree"] else None
        if root and root.get("error") and not result["personas"]:
            result["error"] = root["error"]
            result["ok"] = False
        else:
            result["ok"] = True
        return result
    finally:
        if _own_session:
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass
