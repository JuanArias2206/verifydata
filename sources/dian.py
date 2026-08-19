"""
sources/dian.py — DIAN (RUT, Proveedores ficticios).

Reescrito con Playwright para:
  - RUT: navega a la página oficial y toma screenshot del mensaje de
    mantenimiento ("Consulta estado RUT...fuera de operación") como
    evidencia de que el servicio está caído. URL directa al JPG:
    https://www.dian.gov.co/impuestos/RUT/PublishingImages/Mantenimiento-Consulta-RUT.jpg
  - DIAN Proveedores Ficticios: navega a la página de prensa, extrae
    el link al PDF, lo descarga, lo parsea con pdfplumber, busca el
    nombre/cédula en el PDF y muestra evidencia.
"""
from __future__ import annotations
import re
import time
import requests
from pathlib import Path
from urllib.parse import urljoin
from .base import Hit
from .registry import register

DATA = Path(__file__).parent.parent / "data"
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)
(DATA / "dian_boletines").mkdir(parents=True, exist_ok=True)


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _run_in_fresh_browser(fn):
    """Ejecuta fn(page) en un browser Playwright NUEVO.
    Cada llamada crea y destruye su propio sync_playwright + browser
    para evitar el bug 'Cannot switch to a different thread' cuando
    se ejecuta desde un ThreadPoolExecutor."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerifyData-Demo/1.0 (Mozilla/5.0 compatible)")
            try:
                page = ctx.new_page()
                return fn(page)
            finally:
                try: ctx.close()
                except Exception: pass
        finally:
            try: browser.close()
            except Exception: pass
    finally:
        try: pw.stop()
        except Exception: pass


def _shot_path(source: str, query: str) -> str:
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    return f"screenshots/{safe}_{int(time.time())}.png"


# URL directa al banner oficial de mantenimiento del RUT
RUT_MAINTENANCE_IMG = (
    "https://www.dian.gov.co/impuestos/RUT/PublishingImages/"
    "Mantenimiento-Consulta-RUT.jpg"
)


# ---------- RUT — DIAN ----------
@register
class DianRutSource:
    name = "RUT — DIAN (Registro Único Tributario)"
    source_url = ("https://muisca.dian.gov.co/WebRutMuisca/"
                  "DefConsultaEstadoRUT.faces")
    category = "Identidad y registros básicos"
    requires_captcha = True
    captcha_type = "image"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not cedula and not nombre:
            return Hit(self.name, False, "",
                       notice="Requiere cédula/NIT.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       captcha_required=True,
                       elapsed_s=time.time()-t0)

        query = cedula or nombre
        try:
            def _do_rut(page):
                page.set_default_timeout(20000)
                # 1) Intentar cargar la página oficial del RUT
                try:
                    page.goto(self.source_url, wait_until="domcontentloaded",
                             timeout=20000)
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                # 2) Cargar la imagen oficial de mantenimiento
                try:
                    page.goto(RUT_MAINTENANCE_IMG, wait_until="load",
                             timeout=15000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                # 3) Screenshot del estado actual del RUT
                shot_rel = _shot_path("rut", query or "estado")
                page.screenshot(path=str(DATA / shot_rel),
                              full_page=False, timeout=15000)
                return shot_rel
            shot_rel = _run_in_fresh_browser(_do_rut)
            return Hit(self.name, False, "",
                       notice="Portal RUT — DIAN fuera de operación. "
                              "Banner oficial 'Consulta estado RUT' "
                              "capturado como evidencia. Servicio "
                              "temporalmente deshabilitado por la DIAN.",
                       captcha_required=True,
                       evidence_urls=[self.source_url, RUT_MAINTENANCE_IMG],
                       download_url=shot_rel,
                       elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"RUT error: {type(e).__name__}: {e}.",
                       captcha_required=True,
                       evidence_urls=[self.source_url, RUT_MAINTENANCE_IMG],
                       elapsed_s=time.time()-t0)


# ---------- DIAN Proveedores Ficticios ----------
@register
class DianProveedoresFicticiosSource:
    name = "DIAN — Proveedores Ficticios (Boletín)"
    source_url = ("https://www.dian.gov.co/Prensa/Paginas/"
                  "NG-Comunicado-de-Prensa-091-2025.aspx")
    category = "Contratación pública"
    requires_captcha = False
    captcha_type = None

    # URL canónica del PDF (la que la DIAN publica en su comunicado)
    PDF_URL = ("https://www.dian.gov.co/Proveedores_Ficticios/"
               "Proveedores-Ficticios-09102025.pdf")

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        if not nombre and not cedula:
            return Hit(self.name, False, "",
                       notice="Requiere nombre o cédula/NIT para buscar "
                              "en el boletín de proveedores ficticios.",
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado.",
                       elapsed_s=time.time()-t0)

        query = cedula or nombre
        try:
            results = {}

            def _do_dian(page):
                page.set_default_timeout(30000)
                # 1) Ir a la página de prensa de la DIAN
                page.goto(self.source_url, wait_until="domcontentloaded",
                         timeout=30000)
                page.wait_for_timeout(3000)
                # 2) Buscar el link al PDF
                pdf_url = None
                try:
                    href = page.locator(
                        f"a[href*='{Path(self.PDF_URL).name}']"
                    ).first.get_attribute("href", timeout=5000)
                    if href:
                        pdf_url = urljoin(self.source_url, href)
                except Exception:
                    pass
                if not pdf_url:
                    pdf_url = self.PDF_URL
                results["pdf_url"] = pdf_url
                # 3) Screenshot de la página de prensa
                shot_press_rel = _shot_path("dian_prensa", query)
                page.screenshot(path=str(DATA / shot_press_rel),
                              full_page=False, timeout=15000)
                results["shot_press"] = shot_press_rel
                # 4) Descargar el PDF (vía page.request)
                pdf_name = Path(pdf_url).name
                pdf_path = DATA / "dian_boletines" / pdf_name
                if not pdf_path.exists():
                    try:
                        pdf_resp = page.request.get(pdf_url, timeout=60000)
                        pdf_path.parent.mkdir(parents=True, exist_ok=True)
                        pdf_path.write_bytes(pdf_resp.body())
                    except Exception as e:
                        results["dl_error"] = str(e)
                        return
                results["pdf_path"] = str(pdf_path)
                # 5) Parsear PDF
                text = ""
                try:
                    import pdfplumber
                    with pdfplumber.open(pdf_path) as pdf:
                        text = "\n".join(
                            (p.extract_text() or "")
                            for p in pdf.pages
                        )
                except Exception:
                    text = pdf_path.read_text(errors="replace")
                results["text"] = text
                # 6) Screenshot del PDF página 1
                shot_pdf_rel = _shot_path("dian_pdf", query)
                try:
                    page.goto(pdf_url, timeout=30000)
                    page.wait_for_timeout(2000)
                    page.screenshot(path=str(DATA / shot_pdf_rel),
                                  full_page=False, timeout=15000)
                    results["shot_pdf"] = shot_pdf_rel
                except Exception:
                    pass
            _run_in_fresh_browser(_do_dian)
            if "dl_error" in results:
                return Hit(self.name, False, "",
                           notice=f"No se pudo descargar PDF: "
                                  f"{results['dl_error']}",
                           evidence_urls=[self.source_url,
                                          results.get("pdf_url", self.PDF_URL)],
                           download_url=results.get("shot_press"),
                           elapsed_s=time.time()-t0)
            pdf_url = results.get("pdf_url", self.PDF_URL)
            pdf_path = results.get("pdf_path", "")
            text = results.get("text", "")
            shot_press = results.get("shot_press")
            shot_pdf = results.get("shot_pdf")
            if not pdf_path or not Path(pdf_path).exists():
                return Hit(self.name, False, "",
                           notice="PDF no descargado.",
                           evidence_urls=[self.source_url, pdf_url],
                           download_url=shot_press,
                           elapsed_s=time.time()-t0)
            # 7) Buscar coincidencias
            tokens = []
            if cedula:
                tokens.append(re.escape(cedula))
            if nombre:
                for t in re.split(r"\s+", nombre.upper()):
                    if len(t) >= 3:
                        tokens.append(re.escape(t))
            needles = [t.upper() for t in tokens]
            matches = []
            for line in text.splitlines():
                lu = line.upper()
                if all(n in lu for n in needles):
                    matches.append(line.strip())
                    if len(matches) >= 30:
                        break
            if matches:
                return Hit(self.name, True,
                           f"{len(matches)} coincidencia(s) en "
                           f"Listado Público de Proveedores Ficticios "
                           f"({Path(pdf_path).name})",
                           [{"línea": m[:200],
                             "tokens_buscados": needles} for m in matches],
                           evidence_urls=[self.source_url, pdf_url],
                           download_url=shot_pdf or shot_press,
                           elapsed_s=time.time()-t0)
            return Hit(self.name, False,
                       f"0 coincidencias en Listado de Proveedores "
                       f"Ficticios ({Path(pdf_path).name}, "
                       f"{len(text)} chars parseados). PDF descargado.",
                       evidence_urls=[self.source_url, pdf_url],
                       download_url=shot_pdf or shot_press,
                       elapsed_s=time.time()-t0)
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"DIAN error: {type(e).__name__}: {e}.",
                       evidence_urls=[self.source_url, self.PDF_URL],
                       elapsed_s=time.time()-t0)
