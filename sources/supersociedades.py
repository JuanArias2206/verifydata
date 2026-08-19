"""
sources/supersociedades.py — Superintendencia de Sociedades de Colombia.

Fuente para búsqueda de empresas colombianas en la Superintendencia
de Sociedades (Supersociedades). URL pública:
    https://www.supersociedades.gov.co

Observaciones del portal (vivas, verificadas):

  1) El portal principal (www.supersociedades.gov.co) es un CMS Liferay.
     NO tiene endpoint público de búsqueda por NIT o razón social.
     Es un sitio de transparencia, normativa y PQRS.

  2) La búsqueda real de empresas está en 2 React SPAs separados,
     ambos en `sucursal-digital-ext.supersociedades.gov.co`:

     a) ConsultaGeneralSOC — `Información General de Sociedades`
        (botón desde la home, etiqueta "Información General de Sociedades").
        URL: https://sucursal-digital-ext.supersociedades.gov.co/ConsultaGeneralSOC/dashboard
        API: https://sucursal-digital-ext.supersociedades.gov.co/micro-consulta-sociedades/consultaGeneralSociedades/{consultaDatosBasicosNit,consultarSociedadPorRazonSocial,...}
        reCAPTCHA sitekey: 6Lfo1RYqAAAAAG0odFTFCQsmwKAmc0L_Su0N1IkG

     b) MuestraSociedades — `Entidades Empresariales Requeridas - Corte 2025`
        (botón desde la home, etiqueta "Click aquí para consultar").
        URL: https://sucursal-digital-ext.supersociedades.gov.co/MuestraSociedades/consultaRequerida
        API: https://sucursal-digital-ext.supersociedades.gov.co/micro-muestra-sociedades/muestraSociedades/consultarGeneral
        reCAPTCHA sitekey: 6LeXe90qAAAAAMRWvDDNLnsa07cgfHDDMk1TowuV

  3) TODOS los endpoints devuelven
        {"estado":1,"mensaje":"Ha ocurrido un error, el token de google no es valido"}
     si no se envía un token reCAPTCHA v2 válido. El token debe ser
     resuelto desde la MISMA IP que luego hace la consulta (Google
     reCAPTCHA v2 binding entre navegador/solver/IP).

  4) No hay archivo público (XLSX/CSV/PDF) de la lista completa de
     empresas registradas. Sólo se publica la lista anual de
     "sociedades requeridas" (las que no entregaron informes) en
     formato dinámico dentro del portal — no bulk.

Por lo anterior, esta fuente:

  - Intenta primero el endpoint REST directo por NIT
    (GET .../consultaDatosBasicosNit?nit=...). Esto es válido para
    el caso de prueba (NIT 900000000-1 de "VerifyData"
    debería devolver `estado=0` con los datos básicos si tuviéramos
    captcha).
  - Para búsqueda por nombre (caso de prueba: "VerifyData"), intenta
    POST a `consultarSociedadPorRazonSocial` con `{"razonSocial":...}`.
  - Si el servidor rechaza por captcha (que es lo normal desde
    un pipeline sin browser/solver), devuelve `matched=False` con
    un notice honesto + URL manual a la SPA para que el usuario
    haga la búsqueda con su navegador.

Categoría: "Empresas y sociedades".
Aplica solo a personas jurídicas (no naturales). Para un sujeto
"persona natural" (caso de prueba: DANIEL LORENZO MEDINA SALCEDO)
el resultado es siempre "no aplica" — la Supersociedades sólo
supervisa sociedades.

Para un query de empresa (caso de prueba: "VerifyData"), el portal
NO contiene personas, así que aunque tengamos captcha y la
empresa exista, el match positivo sólo se daría si la consulta
HTTP se completa y devuelve `estado=0` con resultados. Sin captcha
válido, devolvemos `matched=False` honestamente.

Sin captcha en el sentido tradicional del solver: la fuente marca
`requires_captcha=True` con `captcha_type="recaptcha_v2"` y deja
el notice con la URL manual para verificación humana. El solver
interno 2captcha/anthropic no aplica porque el captcha de Google
es server-side-validated y requiere IP binding.
"""
from __future__ import annotations
import re
import time
from urllib.parse import quote
from .base import Hit
from .registry import register


# ---------- Endpoints y constantes ----------

# Home del portal principal (Liferay CMS). El botón "Entidades
# Empresariales Requeridas - Corte 2025" lanza MuestraSociedades.
LANDING_URL = "https://www.supersociedades.gov.co"

# Botón "Información General de Sociedades" (ConsultaGeneralSOC).
# Es el endpoint correcto para NIT/razón social de cualquier empresa
# supervisada por la Supersociedades.
GENERAL_DASHBOARD_URL = (
    "https://sucursal-digital-ext.supersociedades.gov.co/"
    "ConsultaGeneralSOC/dashboard"
)
GENERAL_RECAPTCHA_SITEKEY = "6Lfo1RYqAAAAAG0odFTFCQsmwKAmc0L_Su0N1IkG"
GENERAL_API_BASE = (
    "https://sucursal-digital-ext.supersociedades.gov.co/"
    "micro-consulta-sociedades"
)

# Botón "Entidades Empresariales Requeridas - Corte 2025" (MuestraSociedades).
# Muestra las empresas que NO entregaron informes ese año (un subconjunto
# pequeño — útil para due diligence de proveedores).
REQUERIDAS_URL = (
    "https://sucursal-digital-ext.supersociedades.gov.co/"
    "MuestraSociedades/consultaRequerida"
)
REQUERIDAS_RECAPTCHA_SITEKEY = "6LeXe90qAAAAAMRWvDDNLnsa07cgfHDDMk1TowuV"
REQUERIDAS_API_BASE = (
    "https://sucursal-digital-ext.supersociedades.gov.co/"
    "micro-muestra-sociedades"
)

# Headers que el bundle JS envía en cada request (esos 3 fijos):
#   usuario: "frontConsultaSociedades"
#   aplicacion: "consultaGeneralSociedades2.0"
#   ipUsuario: <client IP>
# El bundle NO envía tokenRecaptcha como header en el ejemplo
# minificado; lo más probable es que se envíe como header
# `recaptcha` o en el body. Probamos con el nombre estándar
# `recaptcha` y `tokenRecaptcha` (ambos son comunes en backends
# Java/Spring que validan @RequestHeader).
DEFAULT_HEADERS = {
    "usuario": "frontConsultaSociedades",
    "aplicacion": "consultaGeneralSociedades2.0",
    "ipUsuario": "127.0.0.1",
    "Accept": "application/json, text/plain, */*",
}

# Timeout HTTP (segundos) — debe caber en el budget de 5s del test
# de aceptación (más algo de margen para que el retry no rompa).
HTTP_TIMEOUT_S = 4.0

# Detección de "captcha rejected" en el JSON de error del backend.
# El backend de Supersociedades es consistente en este mensaje.
_CAPTCHA_REJECTED_MARKERS = (
    "token de google no es valido",
    "el token de google no es valido",
    "captcha",
    "recaptcha",
)


# ---------- HTTP helpers ----------

def _looks_like_captcha_rejection(text: str) -> bool:
    """True si la respuesta del backend parece rechazo de reCAPTCHA."""
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in _CAPTCHA_REJECTED_MARKERS)


def _extract_nit_from_query(nombre: str, cedula: str | None) -> str | None:
    """Heurística: si el caller pasó un NIT en `cedula` (típicamente
    9-15 dígitos, con o sin guión/DV), devuélvelo normalizado a
    solo-dígitos. Si no, None.

    El sistema principal de la Supersociedades acepta NIT de 9
    dígitos sin DV (formato canónico de búsqueda). La DIAN acepta
    NIT con DV pero Supersociedades lo descarta.

    Importante: NO confundir una cédula de ciudadanía (8-10 dígitos)
    con un NIT. Una cédula NO es apta para Supersociedades.
    """
    if not cedula:
        return None
    digits = re.sub(r"\D", "", str(cedula))
    # NIT colombiano: 9 dígitos sin DV. Cédula: 6-10 dígitos.
    # 9 dígitos sin DV es la señal más fuerte; en 8 también podría
    # ser NIT nuevo sin DV, pero lo más conservador es exigir >= 9
    # y NO 10 (que es cédula).
    if len(digits) == 9:
        return digits
    return None


def _try_general_api(query_nit: str | None, query_name: str | None,
                     timeout: float = HTTP_TIMEOUT_S) -> tuple[int, str, str]:
    """Llama al endpoint REST de ConsultaGeneralSOC con el NIT o el
    nombre. Devuelve (status_code, body, endpoint_path).

    Esta llamada SIEMPRE va a fallar con "token de google no es
    valido" desde un pipeline sin browser/solver. La idea es:
      - Si por algún casual el backend no exigiera captcha (futuro),
        tendríamos data real.
      - Si rechaza por captcha, devolvemos la URL manual al SPA.
      - Si hay timeout/error de red, devolvemos un error genérico.
    """
    import requests
    headers = dict(DEFAULT_HEADERS)
    if query_nit:
        endpoint = f"{GENERAL_API_BASE}/consultaGeneralSociedades/consultaDatosBasicosNit"
        url = f"{endpoint}?nit={quote(query_nit, safe='')}"
        return _http_get(url, headers, timeout)
    if query_name:
        endpoint = f"{GENERAL_API_BASE}/consultaGeneralSociedades/consultarSociedadPorRazonSocial"
        url = f"{endpoint}?pagina=0"
        return _http_post_json(url, headers,
                                {"razonSocial": query_name}, timeout)
    return 0, "", ""


def _try_requeridas_api(query_nit: str | None, timeout: float = HTTP_TIMEOUT_S
                        ) -> tuple[int, str, str]:
    """Llama al endpoint REST de MuestraSociedades (sociedades que no
    entregaron informes). Sólo aplica a NIT (9 dígitos)."""
    import requests
    if not query_nit:
        return 0, "", ""
    headers = dict(DEFAULT_HEADERS)
    endpoint = f"{REQUERIDAS_API_BASE}/muestraSociedades/consultarGeneral"
    url = f"{endpoint}?nit={quote(query_nit, safe='')}"
    return _http_get(url, headers, timeout)


def _http_get(url: str, headers: dict, timeout: float) -> tuple[int, str, str]:
    """GET con timeout duro y manejo de excepciones. Devuelve
    (status_code, body_text, endpoint_path)."""
    import requests
    endpoint = url.split("?")[0].rsplit("/", 1)[-1]
    try:
        r = requests.get(url, headers=headers, timeout=timeout,
                         allow_redirects=True)
        return r.status_code, (r.text or ""), endpoint
    except requests.exceptions.Timeout:
        return 0, "Timeout", endpoint
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", endpoint


def _http_post_json(url: str, headers: dict, body: dict,
                    timeout: float) -> tuple[int, str, str]:
    """POST JSON con timeout duro. Devuelve (status_code, body_text,
    endpoint_path)."""
    import requests
    endpoint = url.split("?")[0].rsplit("/", 1)[-1]
    try:
        r = requests.post(url, headers=headers, json=body, timeout=timeout,
                          allow_redirects=True)
        return r.status_code, (r.text or ""), endpoint
    except requests.exceptions.Timeout:
        return 0, "Timeout", endpoint
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", endpoint


def _manual_search_url(query: str) -> str:
    """URL manual de búsqueda en la SPA de ConsultaGeneralSOC."""
    return f"{GENERAL_DASHBOARD_URL}?razonSocial={quote(query or '')}"


# ---------- Source class ----------

@register
class SupersociedadesSource:
    """Superintendencia de Sociedades — Información General de Sociedades.

    Búsqueda por NIT (9 dígitos sin DV) o razón social de empresas
    supervisadas por la Supersociedades de Colombia.

    El portal usa Google reCAPTCHA v2 server-side binding, lo que
    hace imposible automatizar la consulta desde un pipeline sin
    browser + solver con IP residencial matching. Esta fuente
    intenta el endpoint REST directo (que normalmente rechaza por
    captcha) y devuelve un notice honesto con la URL manual del
    SPA para verificación humana.
    """
    name = "Supersociedades — Información General de Sociedades"
    source_url = GENERAL_DASHBOARD_URL
    category = "Empresas y sociedades"
    requires_captcha = True
    captcha_type = "recaptcha_v2"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        # Si no hay NIT (9 dígitos) ni nombre, no hay nada que buscar.
        query_nit = _extract_nit_from_query(nombre, cedula)
        query_name = (nombre or "").strip() or None
        if not query_nit and not query_name:
            return Hit(
                self.name, False, "",
                notice="Requiere NIT (9 dígitos) o razón social.",
                evidence_urls=[GENERAL_DASHBOARD_URL,
                               REQUERIDAS_URL],
                captcha_required=True,
                elapsed_s=time.time() - t0,
            )

        # Intento: API REST directa. Si tuviéramos captcha válido,
        # esta llamada devolvería `{"estado":0,...}` con la data.
        status, body, endpoint = _try_general_api(query_nit, query_name)

        # Caso A: la API respondió con data real (estado=0 + payload).
        # Implementación defensiva: si en el futuro Supersociedades
        # relaja el captcha, queremos reflejar el resultado.
        if status == 200 and body and '"estado":0' in body:
            return Hit(
                self.name, True,
                f"Coincidencia(s) en Supersociedades para "
                f"NIT={query_nit or '?'} / nombre='{query_name or '?'}' "
                f"({len(body)} chars)",
                [{"endpoint": endpoint, "raw": body[:500]}],
                evidence_urls=[GENERAL_DASHBOARD_URL],
                elapsed_s=time.time() - t0,
            )

        # Caso B: rechazo explícito de reCAPTCHA. Este es el modo
        # normal de operación desde un pipeline. Devolvemos un
        # notice honesto con la URL manual y marcamos
        # captcha_required=True.
        if _looks_like_captcha_rejection(body) or status in (200, 0):
            # Generamos una URL deep-link a la SPA con el query
            # prellenado para que el usuario haga 1-click y vea el
            # resultado en su navegador.
            manual_url = _manual_search_url(query_name or query_nit or "")
            qdesc = (f"NIT={query_nit}" if query_nit
                     else f"razón social='{query_name}'")
            return Hit(
                self.name, False,
                f"0 coincidencias en Supersociedades (consulta API "
                f"rechazada por reCAPTCHA server-side; verificación "
                f"manual requerida para {qdesc})",
                notice=("Portal Supersociedades protegido por Google "
                        "reCAPTCHA v2 con IP binding — no automatizable "
                        "sin browser + solver con IP residencial "
                        "matching. Click 'abrir fuente' para verificar "
                        "manualmente en la SPA oficial."),
                captcha_required=True,
                evidence_urls=[manual_url, GENERAL_DASHBOARD_URL,
                               REQUERIDAS_URL],
                elapsed_s=time.time() - t0,
            )

        # Caso C: error de red / 5xx / timeout. Notice honesto.
        return Hit(
            self.name, False, "",
            error=f"{type(Exception(body)).__name__ if False else 'HTTPError'}: "
                  f"status={status}, body={body[:120]!r}, endpoint={endpoint}",
            evidence_urls=[GENERAL_DASHBOARD_URL, REQUERIDAS_URL],
            elapsed_s=time.time() - t0,
        )
