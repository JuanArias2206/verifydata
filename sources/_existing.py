"""
sources/_existing.py — Migración de las fuentes iniciales que NO fueron
reescritas en Fase 3.

Estas 6 fuentes se registran automáticamente al importar este módulo.
Las fuentes reescritas en Fase 3 (procuraduria, policia, dian, contraloria,
rama_judicial) están en sus propios módulos y prevalecen.

Por ahora, las importamos desde el código legacy para que el demo siga
funcionando. Cada fuente se reescribirá como clase propia en la fase
correspondiente.
"""
from __future__ import annotations
import io
import re
import time
from datetime import datetime as _dt
from pathlib import Path
from typing import Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import Hit, safe_fetch
from .registry import register
from .local_lists import normalize as _normalize, tokenize as _tokenize
from ._browser_helper import _run_in_fresh_browser, goto_with_retry

UA = "VerifyData-Demo/1.0 (contacto: verifydata.local)"
TIMEOUT = 30
DATA = Path(__file__).parent.parent / "data"
NOMBRE_MIN_LEN = 4


def _norm_fecha_ddmmaaaa(s: str | None) -> str | None:
    """Normaliza una fecha a DD/MM/AAAA aceptando varios formatos comunes.

    La fecha de expedición puede llegar en DD/MM/AAAA (lo que teclea el
    usuario en el form web) o en AAAA-MM-DD (ISO). Devuelve None si no se
    puede interpretar, para no romper la búsqueda.
    """
    if not s:
        return None
    s = str(s).strip()
    # DD/MM/AAAA (separadores / - .)
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1900 <= y <= 2100:
            return f"{d:02d}/{mo:02d}/{y:04d}"
    # AAAA-MM-DD (ISO)
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1900 <= y <= 2100:
            return f"{d:02d}/{mo:02d}/{y:04d}"
    return None


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    r = Retry(total=2, backoff_factor=0.6,
              status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=r))
    s.mount("http://", HTTPAdapter(max_retries=r))
    return s

S = make_session()


def search_text(needle_norm: str, haystack: str) -> bool:
    tokens = [t for t in needle_norm.split() if len(t) >= 3] if needle_norm else []
    if not tokens:
        return bool(needle_norm) and needle_norm in haystack
    return all(t in haystack for t in tokens)


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 1000 and not force:
        return dest
    r = S.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


# ---------- 1. SECOP II Multas ----------
@register
class SecopIiMultasSource:
    name = "SECOP II — Multas y Sanciones"
    source_url = "https://www.datos.gov.co/resource/it5q-hg94.json"
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            params = {"$limit": 50,
                      "nombre_proveedor_objeto_de": (nombre or "").upper()}
            r = S.get(self.source_url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            rows = r.json()
            needle = _normalize(nombre)
            details = []
            for row in rows:
                blob = _normalize(str(row))
                if not search_text(needle, blob): continue
                details.append({k: row.get(k) for k in
                    ("id_proceso","referencia_proceso","nombre_proveedor_objeto_de",
                     "valor","valor_pagado","fecha_evento","numero_de_acto",
                     "tipo_de_sancion","estado") if k in row})
            return Hit(self.name, len(details)>0,
                       f"{len(details)} coincidencia(s) en {len(rows)} registros",
                       details, elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- 2. SECOP I Multas ----------
@register
class SecopIMultasSource:
    name = "SECOP I — Multas y Sanciones"
    source_url = "https://www.datos.gov.co/resource/4n4q-k399.json"
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            params = {"$limit": 50}
            if cedula:
                params["documento_contratista"] = cedula
            else:
                params["nombre_contratista"] = (nombre or "").upper()
            r = S.get(self.source_url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            rows = r.json()
            needle = _normalize(nombre)
            details = []
            for row in rows:
                blob = _normalize(str(row))
                if cedula:
                    if cedula not in blob: continue
                elif not search_text(needle, blob):
                    continue
                details.append({k: row.get(k) for k in
                    ("nombre_entidad","nit_entidad","nombre_contratista",
                     "documento_contratista","numero_de_contrato","valor_sancion",
                     "fecha_de_publicacion","numero_de_resolucion",
                     "fecha_de_firmeza") if k in row})
            return Hit(self.name, len(details)>0,
                       f"{len(details)} coincidencia(s) en {len(rows)} registros",
                       details, elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- 3. SECOP II Contratos ----------
@register
class SecopIiContratosSource:
    name = "SECOP II — Contratos Electrónicos"
    source_url = "https://www.datos.gov.co/resource/jbjy-vk9h.json"
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            params = {"$limit": 50}
            if cedula:
                params["documento_proveedor"] = cedula
            else:
                params["proveedor_adjudicado"] = (nombre or "").upper()
            r = S.get(self.source_url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            rows = r.json()
            needle = _normalize(nombre)
            details = []
            for row in rows:
                blob = _normalize(str(row))
                if cedula:
                    if cedula not in blob: continue
                elif not search_text(needle, blob):
                    continue
                details.append({k: row.get(k) for k in
                    ("nombre_entidad","nit_entidad","departamento","ciudad",
                     "id_contrato","referencia_del_contrato","estado_contrato",
                     "descripcion_del_proceso","tipo_de_contrato",
                     "fecha_de_firma","fecha_de_inicio_del_contrato",
                     "fecha_de_fin_del_contrato",
                     "proveedor_adjudicado","documento_proveedor",
                     "valor_del_contrato","valor_pagado") if k in row})
            return Hit(self.name, len(details)>0,
                       f"{len(details)} coincidencia(s) en {len(rows)} contratos",
                       details, elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- 4. Registraduría Estado Cédula ----------
#
# Flujo real (inspeccionado contra https://certvigenciacedula.registraduria.gov.co):
#   1) Datos.aspx — form ASP.NET con:
#        - TextBox1 (input name="ctl00$ContentPlaceHolder1$TextBox1")
#        - DropDownList1/2/3 (día, mes, año)
#        - TextBox2 (input del captcha, 6 letras tipo "LANAP")
#        - img con id 'datos_contentplaceholder1_captcha1_CaptchaImage' (LBD_Captcha)
#        - Button1 ("Continuar")
#   2) Click "Continuar" → POST al mismo Datos.aspx, que redirige a
#      Respuesta.aspx con el texto "La certificación se expedira para el
#      numero de cédula: <cedula>" y botones "Generar Certificado" / "Regresar al menu"
#   3) Click "Generar Certificado" → abre una nueva ventana/pestaña que
#      sirve el PDF (Content-Type: application/pdf) o dispara un download.
#
# Implementación:
#   - Llenar el form con selectores robustos (id exactos de ASP.NET).
#   - Resolver el captcha con 2captcha ImageToTextTask.
#   - Click "Continuar" y esperar a que la URL cambie a Respuesta.aspx
#     (o aparezca el texto "expedira"), con timeout 30s.
#   - Click "Generar Certificado" capturando el download / nueva página
#     y guardando el PDF en data/certs/registraduria_<cedula>_<ts>.pdf.
#   - Poner el path del PDF en download_url del Hit; screenshot en details.
#   - Si el PDF no se puede descargar, fallback al screenshot de la
#     Respuesta.aspx + guardado de HTML para diagnóstico.
@register
class RegistraduriaEstadoSource:
    name = "Registraduría — Estado de cédula"
    source_url = "https://certvigenciacedula.registraduria.gov.co/Datos.aspx"
    category = "Identidad y registros básicos"
    requires_captcha = True
    captcha_type = "image"   # captcha de 6 letras (image-based)

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not cedula or not fecha_exp:
            return Hit(self.name, False, "",
                       notice="Requiere cédula + fecha de expedición (dd/mm/aaaa).",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        # Normalizar la fecha a DD/MM/AAAA. Puede llegar en ISO (AAAA-MM-DD);
        # el form ASP.NET necesita día/mes/año separados. Sin esto, el
        # split("/") de abajo revienta con "not enough values to unpack".
        fecha_norm = _norm_fecha_ddmmaaaa(fecha_exp)
        if not fecha_norm:
            return Hit(self.name, False, "",
                       notice=(f"Fecha de expedición '{fecha_exp}' no tiene un "
                               f"formato reconocible (use dd/mm/aaaa)."),
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        fecha_exp = fecha_norm
        # Si no hay solver, devolver captcha_required
        if not solver or not solver.is_available():
            return Hit(self.name, False, "",
                       notice="Requiere solver de captcha imagen "
                              "(2captcha ImageToTextTask).",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        # Playwright para manejar la página
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        try:
            results: dict = {}

            def _do_registraduria(page):
                # 1) Navegar a Datos.aspx.
                #    El sitio de la Registraduría suele responder con
                #    ERR_CONNECTION_RESET (TCP RST del edge server,
                #    comportamiento visto en producción). Hacemos retry
                #    exponencial 1s/3s/6s antes de declararlo caído.
                page.set_default_timeout(60000)
                try:
                    goto_with_retry(
                        page, self.source_url,
                        wait_until="domcontentloaded",
                        timeout=60000, max_attempts=3,
                        backoff_s=(1.0, 3.0, 6.0),
                    )
                except Exception as goto_exc:
                    # 3 intentos fallaron. Devolver un notice honesto:
                    # el sitio puede estar caído, no es un bug nuestro.
                    msg = str(goto_exc) or ""
                    net_err = ""
                    for pat in ("ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
                                "ERR_CONNECTION_ABORTED", "ERR_TIMED_OUT",
                                "ERR_NAME_NOT_RESOLVED", "ERR_NETWORK_CHANGED"):
                        if pat in msg:
                            net_err = pat
                            break
                    if net_err:
                        results["error"] = (
                            f"Sitio Registraduria no disponible "
                            f"(3 reintentos fallaron con {net_err}). "
                            f"El sitio pudo haber caido temporalmente."
                        )
                    else:
                        results["error"] = (
                            f"Registraduria goto fallo tras 3 intentos: "
                            f"{type(goto_exc).__name__}: {msg[:100]}"
                        )
                    # Intentar capturar evidencia (puede fallar si la
                    # página no cargo NADA — Chromium mostrará "ERR_*").
                    try:
                        results["shot"] = _shot_save(
                            page, "registraduria_goto_fail", cedula)
                    except Exception:
                        pass
                    return
                page.wait_for_timeout(2000)

                # 2+3) Llenar cédula (TextBox1) y fecha (3 selects). Se define
                #      como función porque hay que repetirla en cada reintento
                #      de captcha: al recargar Datos.aspx para obtener un
                #      captcha nuevo, el form vuelve vacío.
                def _fill_cedula_fecha():
                    # Cédula (TextBox1, con maxlength=10)
                    ced_inp = page.locator(
                        "input[name*='TextBox1'], input[id*='TextBox1']"
                    ).first
                    ced_inp.wait_for(state="visible", timeout=15000)
                    ced_inp.click()
                    ced_inp.fill("")
                    ced_inp.type(cedula, delay=10)

                    # Fecha (3 selects: día, mes, año)
                    #   DropDownList1 = día, DropDownList2 = mes, DropDownList3 = año
                    dd, mm, yyyy = fecha_exp.split("/")
                    page.locator(
                        "select[name*='DropDownList1'], select[id*='DropDownList1']"
                    ).first.select_option(value=dd)
                    page.locator(
                        "select[name*='DropDownList2'], select[id*='DropDownList2']"
                    ).first.select_option(value=mm)
                    # Año: el select usa value=YYYY, pero el rango puede variar
                    # (2026 → 1851 → 1792 → 1900 → 1930 → etc., saltar
                    # históricamente). Intentar primero por value, luego por
                    # búsqueda de opción que contenga el año.
                    year_sel = page.locator(
                        "select[name*='DropDownList3'], select[id*='DropDownList3']"
                    ).first
                    try:
                        year_sel.select_option(value=yyyy)
                    except Exception:
                        # Fallback: buscar option por label
                        found = page.evaluate(
                            """(year) => {
                                const sels = document.querySelectorAll('select');
                                for (const s of sels) {
                                    const id = (s.id || s.name || '').toLowerCase();
                                    if (!id.includes('dropdownlist3') &&
                                        !id.includes('anio') &&
                                        !id.includes('año')) continue;
                                    for (const o of s.options) {
                                        if ((o.value || '').trim() === year) {
                                            s.value = year;
                                            s.dispatchEvent(new Event('change', {bubbles:true}));
                                            return true;
                                        }
                                    }
                                }
                                return false;
                            }""",
                            yyyy,
                        )
                        if not found:
                            # Último recurso: dejar el año por defecto; ASP.NET
                            # mostrará RequiredFieldValidator
                            pass
                    page.wait_for_timeout(1500)

                def _reload_datos():
                    # Recargar Datos.aspx para forzar un captcha nuevo y
                    # re-llenar el form (usado entre reintentos de captcha).
                    goto_with_retry(
                        page, self.source_url,
                        wait_until="domcontentloaded",
                        timeout=60000, max_attempts=2,
                        backoff_s=(1.0, 3.0),
                    )
                    page.wait_for_timeout(1500)
                    _fill_cedula_fecha()

                _fill_cedula_fecha()

                # 4-7) Resolver captcha y enviar, con reintentos. El solver
                #      ocasionalmente devuelve una respuesta errónea; cuando el
                #      POST no llega a Respuesta.aspx recargamos Datos.aspx
                #      (captcha nuevo) y reintentamos hasta MAX_CAPTCHA_ATTEMPTS.
                MAX_CAPTCHA_ATTEMPTS = 3
                shot_form = None
                submit_ok = False
                last_fail = None
                for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
                    # 4) Resolver captcha imagen con el solver
                    captcha_img = page.locator(
                        "img[id*='CaptchaImage'], img[src*='Captcha.aspx']"
                    ).first
                    captcha_img.wait_for(state="visible", timeout=15000)
                    img_bytes = captcha_img.screenshot(timeout=10000)
                    # Llamar al solver (puede tardar 20-40s). Un error del
                    # solver (red / API) sí es terminal: no reintentamos.
                    try:
                        answer = solver.solve_image(img_bytes)
                    except Exception as e:
                        results["error"] = f"solver.solve_image: {type(e).__name__}: {e}"
                        return
                    # Limpiar espacios y normalizar a mayúsculas (los captchas
                    # LBD son case-insensitive). Una respuesta vacía o muy
                    # corta es un fallo reintentable con un captcha nuevo.
                    answer = re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()
                    if len(answer) < 4:
                        last_fail = f"Respuesta captcha inválida: '{answer}'"
                        if attempt < MAX_CAPTCHA_ATTEMPTS:
                            _reload_datos()
                            continue
                        break

                    # 5) Llenar el input del captcha (TextBox2)
                    cap_inp = page.locator(
                        "input[name*='TextBox2'], input[id*='TextBox2']"
                    ).first
                    cap_inp.wait_for(state="visible", timeout=10000)
                    cap_inp.click()
                    cap_inp.fill("")
                    cap_inp.type(answer, delay=20)
                    page.wait_for_timeout(500)

                    # 6) Screenshot del form lleno (evidencia; se queda el último)
                    shot_form = _shot_save(page, "registraduria_form", cedula)

                    # 7) Click "Continuar" y esperar a Respuesta.aspx
                    #    (la página hace POST a sí misma y luego redirige)
                    btn_continuar = page.locator(
                        "input[name*='Button1'][value='Continuar'], "
                        "input[id*='Button1'][value='Continuar']"
                    ).first
                    btn_continuar.wait_for(state="visible", timeout=10000)

                    # El submit de ASP.NET usa WebForm_DoPostBackWithOptions
                    # y puede tardar 3-8s. Click y esperar a que la URL
                    # cambie a Respuesta.aspx O aparezca el botón
                    # "Generar Certificado" (lo más fiable).
                    try:
                        btn_continuar.click(no_wait_after=True)
                    except Exception:
                        # Fallback: submit del form
                        try:
                            page.evaluate(
                                "() => { const f = document.getElementById("
                                "'form1'); if (f) f.submit(); }"
                            )
                        except Exception:
                            pass
                    # Esperar a que la URL cambie a Respuesta.aspx
                    # (esto puede tomar 5-15s; máximo 30s)
                    try:
                        page.wait_for_url(
                            "**/Respuesta.aspx*", timeout=30000)
                    except Exception:
                        # Si no navegó, puede que siga en Datos.aspx
                        # mostrando un error de validación. Esperar al
                        # botón "Generar Certificado" como fallback.
                        pass
                    # Asegurar que cargó el botón "Generar Certificado"
                    try:
                        page.wait_for_selector(
                            "input[value='Generar Certificado'], "
                            "a:has-text('Generar Certificado'), "
                            "button:has-text('Generar Certificado')",
                            timeout=20000,
                        )
                        submit_ok = True
                        break
                    except Exception:
                        # No apareció el botón. Distinguir un error de
                        # validación (captcha errado / campo obligatorio) de
                        # un fallo genérico; ambos son reintentables.
                        err_text = page.evaluate(
                            "() => document.body.innerText"
                        )
                        if err_text and (
                            "obligatorio" in err_text.lower() or
                            "requerido" in err_text.lower() or
                            "captcha" in err_text.lower()
                        ):
                            last_fail = (
                                f"Validación no superada en Datos.aspx: "
                                f"{err_text[:160]}"
                            )
                        else:
                            last_fail = (
                                "No se llegó a Respuesta.aspx después "
                                f"de Continuar. URL={page.url}"
                            )
                        if attempt < MAX_CAPTCHA_ATTEMPTS:
                            _reload_datos()
                            continue

                if not submit_ok:
                    results["error"] = last_fail or (
                        f"No se superó el captcha tras "
                        f"{MAX_CAPTCHA_ATTEMPTS} intentos."
                    )
                    return
                page.wait_for_timeout(1500)

                # 8) Screenshot de Respuesta.aspx
                shot_resp = _shot_save(page, "registraduria_respuesta", cedula)
                html_resp = page.content()
                text_upper = html_resp.upper()

                # 9) Detectar estado en HTML
                estado = None
                if "VIGENTE" in text_upper and (
                        "VALIDA" in text_upper or "VÁLIDA" in text_upper
                        or "EN VIGOR" in text_upper):
                    estado = "VIGENTE (Válida)"
                elif ("CANCELADA" in text_upper or "MUERTE" in text_upper
                      or "FALLECIDO" in text_upper):
                    estado = "CANCELADA (por muerte)"
                elif ("NO EXISTE" in text_upper or "INVÁLIDA" in text_upper
                      or "INVALIDA" in text_upper):
                    estado = "NO EXISTE / Inválida"
                elif "EXPEDIRA" in text_upper or "EXPEDIRÁ" in text_upper:
                    # El sistema ofrece generar el certificado. Esto
                    # ocurre cuando la cédula es VIGENTE — los datos
                    # detallados vienen en el PDF, no en esta página.
                    estado = "VIGENTE (confirmado; ver PDF para detalle)"
                results["estado"] = estado
                results["shot"] = shot_resp
                results["html_respuesta"] = html_resp

                # 10) Click "Generar Certificado" — captura el download
                #     o la nueva página (PDF).
                pdf_bytes = None
                pdf_path = None
                try:
                    btn_generar = page.locator(
                        "input[value='Generar Certificado'], "
                        "a:has-text('Generar Certificado'), "
                        "button:has-text('Generar Certificado')"
                    ).first
                    btn_generar.wait_for(state="visible", timeout=10000)

                    # El sitio abre el PDF en una nueva ventana (popup)
                    # o como Content-Disposition: attachment. Manejar
                    # ambos: escuchar downloads y popups.
                    download_ok = False
                    try:
                        with page.expect_download(timeout=20000) as dl_info:
                            try:
                                btn_generar.click()
                            except Exception:
                                # Si el botón hace target=_blank, abrir
                                # nueva ventana y leer el PDF directamente
                                raise
                        download = dl_info.value
                        # Guardar el PDF
                        (DATA / "certs").mkdir(parents=True, exist_ok=True)
                        ts1 = _dt.now().strftime("%Y%m%d_%H%M%S")
                        pdf_fname = f"registraduria_{cedula}_{ts1}.pdf"
                        pdf_full = DATA / "certs" / pdf_fname
                        download.save_as(str(pdf_full))
                        if pdf_full.exists() and pdf_full.stat().st_size > 5000:
                            pdf_path = f"certs/{pdf_fname}"
                            pdf_bytes = pdf_full.read_bytes()
                            download_ok = True
                    except Exception as dl_exc:
                        # Fallback: nueva ventana/popup con el PDF
                        try:
                            with page.context.expect_page(timeout=10000) as new_p:
                                try:
                                    btn_generar.click()
                                except Exception:
                                    page.evaluate(
                                        "() => {"
                                        "const b = document.querySelector("
                                        "  \"input[value='Generar Certificado']\""
                                        ");"
                                        "if (b) { b.click(); }"
                                        "}")
                            pdf_page = new_p.value
                            try:
                                pdf_page.wait_for_load_state(
                                    "domcontentloaded", timeout=15000)
                            except Exception:
                                pass
                            # Intentar leer el contenido del PDF
                            try:
                                resp = pdf_page.context.request.get(
                                    pdf_page.url)
                                if resp and resp.ok and (
                                        "pdf" in (resp.headers.get(
                                            "content-type") or "").lower()
                                        or resp.body[:4] == b"%PDF"):
                                    (DATA / "certs").mkdir(
                                        parents=True, exist_ok=True)
                                    ts1 = _dt.now().strftime(
                                        "%Y%m%d_%H%M%S")
                                    pdf_fname = (
                                        f"registraduria_{cedula}_{ts1}.pdf")
                                    pdf_full = DATA / "certs" / pdf_fname
                                    pdf_full.write_bytes(resp.body)
                                    if pdf_full.stat().st_size > 5000:
                                        pdf_path = f"certs/{pdf_fname}"
                                        pdf_bytes = resp.body
                                        download_ok = True
                            except Exception:
                                pass
                            # Otro intento: pedir la URL del popup
                            # directamente con cookies del contexto
                            if not download_ok and pdf_page.url:
                                try:
                                    resp2 = page.context.request.get(
                                        pdf_page.url)
                                    if resp2 and resp2.ok and len(
                                            resp2.body) > 5000:
                                        (DATA / "certs").mkdir(
                                            parents=True, exist_ok=True)
                                        ts1 = _dt.now().strftime(
                                            "%Y%m%d_%H%M%S")
                                        pdf_fname = (
                                            f"registraduria_{cedula}_{ts1}.pdf")
                                        pdf_full = DATA / "certs" / pdf_fname
                                        pdf_full.write_bytes(resp2.body)
                                        if pdf_full.stat().st_size > 5000:
                                            pdf_path = (
                                                f"certs/{pdf_fname}")
                                            pdf_bytes = resp2.body
                                            download_ok = True
                                except Exception:
                                    pass
                            try:
                                pdf_page.close()
                            except Exception:
                                pass
                        except Exception as pop_exc:
                            # Último recurso: el botón podría haber
                            # abierto el PDF en la misma página
                            try:
                                page.wait_for_load_state(
                                    "domcontentloaded", timeout=10000)
                                body = page.evaluate(
                                    "() => document.body.innerText")
                                if body and "expedira" in body.lower():
                                    # Re-click por si la página anterior
                                    # tenía el form
                                    pass
                            except Exception:
                                pass
                    results["pdf_path"] = pdf_path
                    results["pdf_bytes_size"] = len(pdf_bytes) if pdf_bytes else 0
                except Exception as gen_exc:
                    results["generar_error"] = (
                        f"{type(gen_exc).__name__}: {gen_exc}")

                # 11) Extraer detalles desde el HTML de Respuesta.aspx
                #     (nombre, lugar, fecha, código de verificación)
                details: list[dict] = []
                # Cédula y estado
                if estado:
                    details.append({
                        "estado": estado,
                        "cédula": cedula,
                        "fecha_expedición": fecha_exp,
                    })
                # Nombre (buscar el bloque "A nombre de")
                m = re.search(
                    r'(?:A\s+nombr?e\s+de|NOMBRE)\s*[:\-]?\s*'
                    r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,\.]{4,80})',
                    html_resp, re.I)
                if m:
                    details.append({"nombre": m.group(1).strip()})
                # Lugar de expedición
                m = re.search(
                    r'(?:LUGAR\s+DE\s+EXPEDICIÓN|Lugar\s+de\s+Expedición|'
                    r'LUGAR\s+EXPEDICIÓN)\s*[:\-]?\s*'
                    r'([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,\.\-]{3,60})',
                    html_resp, re.I)
                if m:
                    details.append({"lugar_expedición": m.group(1).strip()})
                # Fecha de expedición (en Respuesta.aspx, en formato largo)
                m = re.search(
                    r'(?:FECHA\s+DE\s+EXPEDICIÓN|Fecha\s+de\s+Expedición)'
                    r'\s*[:\-]?\s*'
                    r'([0-9]{1,2}\s+DE\s+[A-ZÁÉÍÓÚÑ]+'
                    r'(?:\s+DE\s+[A-ZÁÉÍÓÚÑ]+)?\s+DE\s+[0-9]{4})',
                    html_resp, re.I)
                if m:
                    details.append({"fecha_expedición_detalle":
                                    m.group(1).strip()})
                # Código de verificación
                m = re.search(
                    r'(?:CÓDIGO\s+DE\s+VERIFICACIÓN|Código\s+de\s+verificación|'
                    r'C[oó]digo\s+de\s+verificaci[oó]n)\s*[:\-]?\s*'
                    r'([A-Z0-9]{6,40})',
                    html_resp, re.I)
                if m:
                    details.append({"código_verificación":
                                    m.group(1).strip()})
                # Si tenemos PDF, intentar extraer datos del texto del PDF
                if pdf_bytes:
                    pdf_text = _extract_pdf_text(pdf_bytes)
                    if pdf_text:
                        for k, v in _parse_pdf_fields(pdf_text).items():
                            details.append({k: v})
                        results["pdf_text"] = pdf_text[:2000]

                # Guardar path del PDF y screenshot en details
                evidence_entry = {}
                if pdf_path:
                    evidence_entry["pdf_path"] = pdf_path
                if shot_resp:
                    evidence_entry["screenshot"] = shot_resp
                if shot_form:
                    evidence_entry["form_screenshot"] = shot_form
                if evidence_entry:
                    details.append(evidence_entry)
                results["details"] = details

                # 12) Si NO se pudo descargar el PDF, guardar el HTML
                #     de la respuesta como fallback
                if not pdf_path:
                    try:
                        (DATA / "certs").mkdir(parents=True, exist_ok=True)
                        ts1 = _dt.now().strftime("%Y%m%d_%H%M%S")
                        html_fname = (
                            f"registraduria_{cedula}_{ts1}_respuesta.html")
                        (DATA / "certs" / html_fname).write_text(
                            html_resp, encoding="utf-8")
                        results["html_path"] = f"certs/{html_fname}"
                    except Exception:
                        pass

            _run_in_fresh_browser(_do_registraduria)
            elapsed = time.time() - t0
            if "error" in results:
                # Si capturamos screenshot del fallo, incluirlo como
                # download_url para que el operador tenga evidencia
                # visual de la página de error de Chromium.
                fail_shot = results.get("shot")
                return Hit(self.name, False, "Sitio no respondio",
                           notice=results["error"],
                           captcha_required=True,
                           download_url=fail_shot,
                           evidence_urls=[self.source_url],
                           elapsed_s=elapsed)
            estado = results.get("estado")
            details = results.get("details", [])
            pdf_path = results.get("pdf_path")
            shot = results.get("shot")
            matched = bool(estado)
            # Priorizar PDF en download_url; fallback a screenshot
            download_url = pdf_path or shot
            summary = estado or "Búsqueda ejecutada (ver evidencia)."
            if pdf_path:
                summary = f"{estado or 'Resultado'}. PDF descargado."
            return Hit(self.name, matched,
                       summary, details,
                       download_url=download_url,
                       evidence_urls=[self.source_url,
                                      "https://certvigenciacedula.registraduria.gov.co/Respuesta.aspx"],
                       elapsed_s=elapsed)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Registraduría error: {type(e).__name__}: {e}.",
                       captcha_required=True,
                       elapsed_s=time.time() - t0)


# --- helpers de extracción de texto PDF (módulos opcionales) ---
def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Intenta extraer texto del PDF. Usa pypdf/PyPDF2/pdfplumber si está
    disponible. Si ninguno, retorna string vacío."""
    # 1) pypdf
    try:
        import pypdf
        rdr = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in rdr.pages)
    except Exception:
        pass
    # 2) PyPDF2
    try:
        import PyPDF2
        rdr = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in rdr.pages)
    except Exception:
        pass
    # 3) PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(p.get_text() for p in doc)
    except Exception:
        pass
    # 4) pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        pass
    return ""


def _parse_pdf_fields(pdf_text: str) -> dict:
    """Extrae campos estructurados (Cédula, Nombre, Estado, etc.) del
    texto del PDF. Heurística basada en el layout conocido del
    certificado Registraduría: una sola página, cada campo es
    'Label: valor' en líneas separadas."""
    out: dict = {}
    if not pdf_text:
        return out
    patterns = [
        ("cédula_pdf", r"(?:C[eé]dula|N[uú]mero\s+de\s+[Ii]dentificaci[oó]n|NUIP)\s*[:\-]?\s*([0-9\.\,]{6,20})"),
        ("nombre_pdf", r"(?:A\s+nombr?e\s+de|Nombres?\s+y\s+Apellidos?|NOMBRE)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,]{4,80})"),
        ("estado_pdf", r"(?:Estado|ESTADO)\s*[:\-]?\s*(VIGENTE|CANCELADA|CANCELADO|NO\s+EXISTE|INVÁLIDA|INVALIDA|MUERTE|FALLECIDO[A-Z]*)"),
        ("lugar_exp_pdf", r"(?:Lugar\s+de\s+Expedici[oó]n|Lugar\s+Expedici[oó]n)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,\-\.]{3,60})"),
        ("fecha_exp_pdf", r"(?:Fecha\s+de\s+Expedici[oó]n)\s*[:\-]?\s*([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{4}|[0-9]{1,2}\s+DE\s+[A-ZÁÉÍÓÚÑ]+\s+DE\s+[0-9]{4})"),
        ("codigo_verif_pdf", r"(?:C[oó]digo\s+de\s+verificaci[oó]n|CODIGO\s+DE\s+VERIFICACION)\s*[:\-]?\s*([A-Z0-9]{6,40})"),
    ]
    for key, pat in patterns:
        m = re.search(pat, pdf_text, re.I)
        if m:
            v = m.group(1).strip().rstrip(".,;:")
            if v and len(v) >= 2:
                out[key] = v
    return out


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _shot_save(page, source: str, query: str) -> str:
    """Guarda screenshot y devuelve path relativo."""
    DATA = Path(__file__).parent.parent / "data"
    (DATA / "screenshots").mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    fname = f"screenshots/{safe}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(DATA / fname),
                      full_page=False, timeout=15000)
    except Exception:
        pass
    return fname


# ---------- 5. OFAC SDN ----------
@register
class OfacSdnSource:
    name = "OFAC SDN — Specially Designated Nationals"
    source_url = "https://sanctionssearch.ofac.treas.gov/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            path = download("https://www.treasury.gov/ofac/downloads/sdn.csv",
                            DATA / "ofac_sdn.csv")
            needle = _normalize(nombre)
            details = []
            total_rows = 0
            with path.open(encoding="utf-8", errors="replace") as f:
                for row in csv.reader(f) if False else __import__("csv").reader(f):
                    if not row or len(row) < 4: continue
                    if row[0].startswith("0"): continue
                    total_rows += 1
                    sdn_name = row[1]; sdn_type = row[2]; program = row[3]
                    remarks = row[11] if len(row) > 11 else ""
                    if search_text(needle, _normalize(sdn_name)):
                        details.append({
                            "nombre_lista": sdn_name, "tipo": sdn_type,
                            "programa": program,
                            "detalles": (remarks or "")[:140]})
                        if len(details) >= 30: break
            # Guard: la SDN real tiene >10k filas. Un archivo corto = descarga
            # rota → jamás reportar "0 coincidencias" sobre datos truncos.
            if total_rows < 1000 and not details:
                return Hit(self.name, False,
                           "NO FUE POSIBLE CONSULTAR: dataset SDN local "
                           f"incompleto ({total_rows} filas)",
                           status="dataset_missing",
                           error_type="dataset_missing",
                           dataset_records=total_rows,
                           elapsed_s=time.time()-t0)
            hit = Hit(self.name, len(details)>0,
                      f"{len(details)} coincidencia(s) (OFAC SDN List, "
                      f"{total_rows:,} registros)".replace(",", "."),
                      details, elapsed_s=time.time()-t0)
            hit.dataset_records = total_rows
            hit.status = ("match_probable" if details else "nomatch_verified")
            if details:
                hit.confidence = "fuerte"
                hit.matched_name = details[0].get("nombre_lista", "")
            return hit
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- 6. UN Security Council ----------
@register
class UnConsolidatedSource:
    name = "ONU — UN Security Council Consolidated List"
    source_url = "https://www.un.org/securitycouncil/content/un-sc-consolidated-list"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            path = download("https://scsanctions.un.org/resources/xml/en/consolidated.xml",
                            DATA / "un_consolidated.xml")
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            needle = _normalize(nombre)
            details = []
            individuals = tree.getroot().findall(".//INDIVIDUAL")
            # Guard: la lista ONU real tiene cientos de individuos.
            if len(individuals) < 100:
                return Hit(self.name, False,
                           "NO FUE POSIBLE CONSULTAR: dataset ONU local "
                           f"incompleto ({len(individuals)} individuos)",
                           status="dataset_missing",
                           error_type="dataset_missing",
                           dataset_records=len(individuals),
                           elapsed_s=time.time()-t0)
            for ind in individuals:
                d = {c.tag: (c.text or "").strip() for c in ind}
                blob = _normalize(" ".join(d.values()))
                if not search_text(needle, blob): continue
                full = " ".join(filter(None, [
                    d.get("FIRST_NAME",""), d.get("SECOND_NAME",""),
                    d.get("THIRD_NAME",""), d.get("FOURTH_NAME","")])).strip()
                details.append({
                    "nombre": full, "alias": d.get("ALIAS_NAME",""),
                    "fecha_nacimiento": d.get("DATE_OF_BIRTH",""),
                    "lugar_nacimiento": d.get("PLACE_OF_BIRTH",""),
                    "nacionalidad": d.get("NATIONALITY",""),
                    "designacion": d.get("UN_LIST_TYPE",""),
                    "motivo": (d.get("COMMENTS1","") or "")[:160]})
                if len(details) >= 30: break
            hit = Hit(self.name, len(details)>0,
                      f"{len(details)} coincidencia(s) (INDIVIDUAL, "
                      f"{len(individuals):,} registros)".replace(",", "."),
                      details, elapsed_s=time.time()-t0)
            hit.dataset_records = len(individuals)
            hit.status = ("match_probable" if details else "nomatch_verified")
            if details:
                hit.confidence = "fuerte"
                hit.matched_name = details[0].get("nombre", "")
            return hit
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)


# ---------- 7. UK HM Treasury ----------
@register
class UkTreasurySource:
    name = "UK HM Treasury — Consolidated Sanctions"
    source_url = "https://search-uk-sanctions-list.service.gov.uk/"
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        try:
            needle = _normalize(nombre)
            first = (nombre.split()[0] if nombre else "").upper()
            last  = (nombre.split()[-1] if nombre else "").upper()
            query = f"{first} {last}".strip() or "test"
            r = S.get("https://search-uk-sanctions-list.service.gov.uk/search",
                      params={"q": query, "type": "individual"}, timeout=TIMEOUT)
            r.raise_for_status()
            details = []
            for nm in re.findall(r'class="govuk-link"[^>]*>([^<]{4,100})</a>', r.text):
                if search_text(needle, _normalize(nm)):
                    details.append({"nombre_listado": nm.strip(),
                                    "url_búsqueda": f"{self.source_url}search?q={query}"})
                    if len(details) >= 20: break
            return Hit(self.name, len(details)>0,
                       f"{len(details)} coincidencia(s) en UK Sanctions List",
                       details, elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "", error=f"{type(e).__name__}: {e}",
                       elapsed_s=time.time()-t0)
