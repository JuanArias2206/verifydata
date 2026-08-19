"""
report.py — Generador de reportes PDF profesional (estilo dossier de
cumplimiento) con branding VerifyData.

Estructura del documento (SIN índice; las fuentes principales van primero):

  1. PORTADA           — Banda de marca + sujeto consultado + metadatos.
  2. RESUMEN EJECUTIVO — Panel de identidad + banner de riesgo + tabla de
                         hallazgos por severidad + "Fuentes no disponibles".
  3. FUENTES PRINCIPALES (EXCLUYENTES) — Las fuentes prioritarias de
                         cumplimiento, cada una con su resultado, datos
                         estructurados y EVIDENCIA. Para fuentes que emiten
                         certificado PDF (Registraduría, Contraloría), se
                         embebe la CAPTURA DEL PDF rasterizado, no el
                         screenshot de la página web.
  4. INFORMACIÓN GENERAL — El resto de las fuentes, agrupadas por categoría.
  5. PIE                — En todas las páginas: banda de marca, "Página X de
                         Y", disclaimer legal y timestamp.

Brand: VerifyData (violeta #6941F4 + navy #050A5C).
"""
from __future__ import annotations
import io
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, PageBreak,
                                KeepTogether, Image, KeepInFrame, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas as _canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# Carpeta de datos (screenshots, certificados, etc)
DATA_DIR = Path(__file__).parent / "data"
STATIC_DIR = Path(__file__).parent / "static"
BAND_PATH = STATIC_DIR / "report-band.png"

# === Tipografía Montserrat (VerifyData) ====================================
FONTS_DIR = STATIC_DIR / "fonts"
FONT = "Helvetica"
FONT_MED = "Helvetica"
FONT_SB = "Helvetica-Bold"
FONT_BOLD = "Helvetica-Bold"
FONT_XB = "Helvetica-Bold"
FONT_IT = "Helvetica-Oblique"
try:
    pdfmetrics.registerFont(TTFont("Montserrat", str(FONTS_DIR / "Montserrat-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Montserrat-Med", str(FONTS_DIR / "Montserrat-Medium.ttf")))
    pdfmetrics.registerFont(TTFont("Montserrat-SB", str(FONTS_DIR / "Montserrat-SemiBold.ttf")))
    pdfmetrics.registerFont(TTFont("Montserrat-Bold", str(FONTS_DIR / "Montserrat-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("Montserrat-XB", str(FONTS_DIR / "Montserrat-ExtraBold.ttf")))
    pdfmetrics.registerFont(TTFont("Montserrat-It", str(FONTS_DIR / "Montserrat-Italic.ttf")))
    registerFontFamily("Montserrat", normal="Montserrat", bold="Montserrat-Bold",
                       italic="Montserrat-It", boldItalic="Montserrat-Bold")
    FONT = "Montserrat"
    FONT_MED = "Montserrat-Med"
    FONT_SB = "Montserrat-SB"
    FONT_BOLD = "Montserrat-Bold"
    FONT_XB = "Montserrat-XB"
    FONT_IT = "Montserrat-It"
except Exception:
    pass

# Ancho útil de página (LETTER 21.59cm − 2*2cm de margen ≈ 17.59cm).
PAGE_W = 17.4 * cm

# Alto de la banda de marca superior (dibujada en todas las páginas).
BAND_H = 1.75 * cm

# === Brand VerifyData ===
BRAND_NAVY = colors.HexColor("#050A5C")       # azul profundo (títulos, texto fuerte)
BRAND_DARK = colors.HexColor("#111827")       # texto cuerpo
BRAND_MUTED = colors.HexColor("#6B7280")      # texto secundario/labels
BRAND_SLATE = colors.HexColor("#6B7280")
BRAND_BORDER = colors.HexColor("#E5E7EB")     # bordes/líneas
BRAND_BG = colors.HexColor("#FFFFFF")
BRAND_BG_ALT = colors.HexColor("#F7F8FC")     # fondo cajas
BRAND_NAVY_SOFT = colors.HexColor("#EEF0FB")  # fondo headers de tabla/categoría
BRAND_CYAN = colors.HexColor("#6941F4")       # ACENTO PRINCIPAL = violeta (barras, líneas)
BRAND_CYAN_DARK = colors.HexColor("#6941F4")  # violeta (links)
BRAND_CYAN_SOFT = colors.HexColor("#F1ECFE")  # violeta muy claro (fondos suaves)
BRAND_VIOLET = colors.HexColor("#6941F4")
BRAND_MAGENTA = colors.HexColor("#D00DE3")
BRAND_SUCCESS = colors.HexColor("#15803D")    # verde texto
BRAND_WARN = colors.HexColor("#A16207")       # ámbar texto
BRAND_ERROR = colors.HexColor("#B91C1C")      # rojo texto
BRAND_CAPTCHA = colors.HexColor("#6941F4")

# Fondos "pill" aplanados sobre blanco para badges/chips.
RISK_BG_GREEN = colors.HexColor("#DEF3E4")
RISK_BG_AMBER = colors.HexColor("#FEF2D6")
RISK_BG_RED = colors.HexColor("#FCE1E1")
RISK_BG_VIOLET = colors.HexColor("#EDE9FE")
RISK_BG_BLUE = colors.HexColor("#E7EEFE")
RISK_BG_GRAY = colors.HexColor("#F1F0F6")

# Fondo claro (pill) por color de texto sólido.
_PILL_BG = {
    "#15803d": RISK_BG_GREEN,
    "#a16207": RISK_BG_AMBER,
    "#b91c1c": RISK_BG_RED,
    "#6941f4": RISK_BG_VIOLET,
    "#2563eb": RISK_BG_BLUE,
    "#6b7280": RISK_BG_GRAY,
    "#d00de3": RISK_BG_VIOLET,
}


def _pill_bg(color) -> colors.Color:
    """Fondo pill claro para un color de texto sólido."""
    try:
        return _PILL_BG.get(color.hexval()[-6:].lower(), RISK_BG_GRAY)
    except Exception:
        return RISK_BG_GRAY

# === Estilos tipográficos ===
styles = getSampleStyleSheet()

TITLE = ParagraphStyle(
    "Title", parent=styles["Normal"],
    fontName=FONT_XB, fontSize=24, leading=28,
    textColor=BRAND_NAVY, spaceAfter=4, spaceBefore=0,
)
H2 = ParagraphStyle(
    "H2", parent=styles["Normal"],
    fontName=FONT_BOLD, fontSize=17, leading=21,
    textColor=BRAND_NAVY, spaceAfter=4, spaceBefore=0,
)
H3 = ParagraphStyle(
    "H3", parent=styles["Normal"],
    fontName=FONT_BOLD, fontSize=12.5, leading=16,
    textColor=BRAND_NAVY, spaceAfter=2, spaceBefore=0,
)
H4 = ParagraphStyle(
    "H4", parent=styles["Normal"],
    fontName=FONT_BOLD, fontSize=10, leading=13,
    textColor=BRAND_NAVY, spaceAfter=2, spaceBefore=0,
)
BODY = ParagraphStyle(
    "Body", parent=styles["Normal"],
    fontName=FONT, fontSize=10, leading=13.5,
    textColor=BRAND_DARK,
)
BODY_MUTED = ParagraphStyle(
    "BodyMuted", parent=styles["Normal"],
    fontName=FONT, fontSize=9, leading=12,
    textColor=BRAND_MUTED,
)
CAPTION = ParagraphStyle(
    "Caption", parent=styles["Normal"],
    fontName=FONT_IT, fontSize=8, leading=10,
    textColor=BRAND_MUTED, alignment=TA_CENTER,
)
TAGLINE = ParagraphStyle(
    "Tagline", parent=styles["Normal"],
    fontName=FONT_SB, fontSize=10, leading=12,
    textColor=BRAND_CYAN_DARK, alignment=TA_CENTER,
)
STAT_LABEL = ParagraphStyle(
    "StatLabel", parent=styles["Normal"],
    fontName=FONT, fontSize=8, leading=10,
    textColor=BRAND_MUTED, alignment=TA_CENTER,
)
STAT_NUM = ParagraphStyle(
    "StatNum", parent=styles["Normal"],
    fontName=FONT_XB, fontSize=20, leading=24,
    textColor=BRAND_NAVY, alignment=TA_CENTER,
)
SMALL = ParagraphStyle(
    "Small", parent=styles["Normal"],
    fontName=FONT, fontSize=7.5, leading=9,
    textColor=BRAND_MUTED,
)
LINK = ParagraphStyle(
    "Link", parent=styles["Normal"],
    fontName=FONT, fontSize=8, leading=10,
    textColor=BRAND_CYAN_DARK,
)

# === Estados finos y grupos visuales =====================================
# Cada Hit se clasifica en un estado FINO (los de sources.base.STATUS_KINDS
# más los legacy match/nomatch/manual/nodisp/error). Para conteos y layout
# los estados se agrupan en 6 grupos: match|nomatch|review|manual|nodisp|error.
STATUS_GROUP = {
    "match": "match", "match_exact": "match", "match_probable": "match",
    "possible_homonym": "match",
    "nomatch": "nomatch", "nomatch_verified": "nomatch",
    "manual_review": "review",
    "manual": "manual", "not_implemented": "manual",
    "requires_login": "manual", "requires_payment": "manual",
    "nodisp": "nodisp", "captcha_blocked": "nodisp",
    "dataset_missing": "nodisp", "dataset_stale": "nodisp",
    "source_changed": "nodisp",
    "error": "error", "timeout": "error",
    "captcha": "nodisp",
}

# Badge colors por GRUPO (con overrides finos donde aporta señal).
GROUP_COLORS = {
    "match":   BRAND_SUCCESS,
    "nomatch": BRAND_SLATE,
    "review":  BRAND_WARN,
    "manual":  BRAND_WARN,
    "nodisp":  BRAND_SLATE,
    "error":   BRAND_ERROR,
}
COLORS = {
    "match":   BRAND_SUCCESS,
    "nomatch": BRAND_SLATE,
    "error":   BRAND_ERROR,
    "nodisp":  BRAND_SLATE,
    "manual":  BRAND_WARN,
    "captcha": BRAND_CAPTCHA,
    # estados finos
    "match_exact":      BRAND_SUCCESS,
    "match_probable":   colors.HexColor("#0E9F6E"),
    "possible_homonym": colors.HexColor("#D97706"),
    "nomatch_verified": BRAND_SLATE,
    "manual_review":    BRAND_WARN,
    "not_implemented":  BRAND_WARN,
    "requires_login":   BRAND_WARN,
    "requires_payment": BRAND_WARN,
    "captcha_blocked":  BRAND_CAPTCHA,
    "dataset_missing":  BRAND_ERROR,
    "dataset_stale":    BRAND_WARN,
    "source_changed":   BRAND_ERROR,
    "timeout":          BRAND_ERROR,
}
LABELS = {
    "match":   "CON REGISTRO",
    "nomatch": "SIN REGISTRO",
    "error":   "ERROR",
    "nodisp":  "NO DISPONIBLE",
    "manual":  "CONSULTA MANUAL",
    "captcha": "REQUIERE CAPTCHA",
    # estados finos
    "match_exact":      "COINCIDENCIA EXACTA",
    "match_probable":   "COINCIDENCIA PROBABLE",
    "possible_homonym": "POSIBLE HOMÓNIMO",
    "nomatch_verified": "NO REGISTRA (VERIFICADO)",
    "manual_review":    "REQUIERE REVISIÓN",
    "not_implemented":  "CONSULTA MANUAL",
    "requires_login":   "REQUIERE CUENTA",
    "requires_payment": "REQUIERE PAGO",
    "captcha_blocked":  "BLOQUEO CAPTCHA/WAF",
    "dataset_missing":  "DATASET NO DISPONIBLE",
    "dataset_stale":    "DATASET DESACTUALIZADO",
    "source_changed":   "FUENTE CAMBIÓ",
    "timeout":          "TIMEOUT",
}

# === Fuentes PRINCIPALES / EXCLUYENTES ===================================
# Son las fuentes prioritarias de cumplimiento: van PRIMERO en el reporte,
# cada una con presentación destacada. El orden de esta lista es el orden
# canónico en que aparecen. El resto de fuentes va en "Información general".
#
# Confirmado con el cliente (2026-07): Registraduría, RUES, ONU,
# OFAC, PEP Colombia, PEP Internacional, Procuraduría, Contraloría, Policía
# (antecedentes judiciales), Delitos sexuales contra menores, DIAN
# Proveedores Ficticios, SECOP.
PRINCIPAL_SOURCES = [
    "Registraduría — Estado de cédula",
    "RUES — Registro Único Empresarial y Social",
    "ONU — UN Security Council Consolidated List",
    "OFAC SDN — Specially Designated Nationals",
    "PEP Colombia — Consulta agregada",
    "PEP Internacionales — Consulta agregada",
    "Procuraduría — Antecedentes Disciplinarios",
    "Contraloría General — Responsabilidad Fiscal",
    "Policía Nacional — Antecedentes Judiciales",
    "Rama Judicial — Procesos (demandante/demandado)",
    "Policía — Delitos Sexuales contra Menores",
    "DIAN — Proveedores Ficticios (Boletín)",
    "SECOP II — Multas y Sanciones",
    "SECOP I — Multas y Sanciones",
]
PRINCIPAL_SET = set(PRINCIPAL_SOURCES)
_PRINCIPAL_INDEX = {n: i for i, n in enumerate(PRINCIPAL_SOURCES)}


def _is_principal(h) -> bool:
    return (getattr(h, "source", "") or "") in PRINCIPAL_SET


def _principal_order(h) -> int:
    return _PRINCIPAL_INDEX.get(getattr(h, "source", "") or "", 999)


# === Helpers de texto ====================================================

def _esc(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    return (s.replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;"))


def _clean_val(v: Any) -> str:
    """Limpia un valor de detalle: primera línea, sin colas de parseo de PDF."""
    if v is None:
        return ""
    lines = str(v).splitlines()
    v = lines[0].strip() if lines else ""
    for tail in (" A nombre de", " Estado", " Nombre",
                 " Lugar de Expedición", " Lugar de Expedicion"):
        if v.endswith(tail):
            v = v[:-len(tail)].strip()
    return v


# Prettificado de claves crudas → etiquetas legibles.
_KEY_LABELS = {
    "cedula": "Cédula", "cédula": "Cédula",
    "estado": "Estado", "estado_pdf": "Estado",
    "nombre": "Nombre", "nombre_pdf": "Nombre",
    "fecha_expedicion": "Fecha de expedición",
    "fecha_expedición": "Fecha de expedición",
    "fecha_exp_pdf": "Fecha de expedición",
    "fecha_expedición_detalle": "Fecha de expedición",
    "lugar_expedicion": "Lugar de expedición",
    "lugar_expedición": "Lugar de expedición",
    "lugar_exp_pdf": "Lugar de expedición",
    "codigo_verificacion": "Código de verificación",
    "código_verificación": "Código de verificación",
    "codigo_verif_pdf": "Código de verificación",
    "nit": "NIT", "rut": "RUT",
    "programa": "Programa", "nombre_lista": "Nombre en lista",
    "fila": "Registro", "estado_rut": "Estado RUT",
}


def _pretty_key(k: str) -> str:
    kl = str(k).lower().strip()
    if kl in _KEY_LABELS:
        return _KEY_LABELS[kl]
    return str(k).replace("_", " ").strip().capitalize()


# === Clasificación de estado =============================================

_NODISP_KW = ("no disponible", "403", "bloque", "fuera de operación",
              "fuera de operacion", "redirige", "no accesible", "waf",
              "cloudfront", "re-render", "re-renderiz",
              "no se obtuvo respuesta", "ip binding", "ip-binding",
              "no retornó token", "no retorno token", "sin proxy",
              "protegido por", "timeout", "silenciosamente fallido")
_MANUAL_KW = ("login", "registrar", "cuenta", "pago", "manualmente",
              "captcha visual", "requiere cuenta", "no instalado",
              "abrir fuente")
# Señales de resultado CONCLUYENTE limpio: la fuente sí respondió y no hubo
# coincidencias. Evita que un summary limpio se marque "no disponible".
_CLEAN_KW = ("no registra", "sin coincidencia", "0 coincidencia",
             "no aparece", "sin antecedente", "no presenta", "sin registro")


def _status_kind(h) -> str:
    """Clasifica un Hit en un estado FINO.

    Prioridad:
      1. `h.status` explícito declarado por la fuente (estados finos).
      2. Heurística legacy sobre error/matched/notice/summary para fuentes
         que aún no declaran status.

    La regla de oro: un error técnico o un dataset ausente NUNCA debe
    terminar clasificado como 'nomatch'."""
    explicit = getattr(h, "status", None)
    if explicit and explicit in STATUS_GROUP:
        return explicit
    if getattr(h, "error", None):
        err = str(getattr(h, "error_type", None) or h.error).lower()
        if "timeout" in err or "timed out" in err:
            return "timeout"
        return "error"
    if getattr(h, "requires_manual_review", False):
        return "manual_review"
    if getattr(h, "matched", False):
        conf = (getattr(h, "confidence", None) or "").lower()
        if conf == "exacta":
            return "match_exact"
        if conf == "fuerte":
            return "match_probable"
        if conf == "posible":
            return "possible_homonym"
        return "match"
    notice = (getattr(h, "notice", None) or "").lower()
    summary = (getattr(h, "summary", None) or "").lower()
    captcha = getattr(h, "captcha_required", False)
    blob = f"{notice} {summary}"
    if not notice and not summary and not captcha:
        return "nomatch"
    if ("cache:" in notice or "n=" in notice) and not captcha:
        return "nomatch"
    if any(k in blob for k in _MANUAL_KW):
        return "manual"
    # Un resultado limpio explícito gana sobre keywords de indisponibilidad.
    if any(k in summary for k in _CLEAN_KW) and not captcha:
        return "nomatch_verified"
    if captcha or any(k in blob for k in _NODISP_KW):
        return "captcha_blocked" if captcha else "nodisp"
    return "nomatch"


def _status_group(h) -> str:
    """Grupo visual del Hit: match | nomatch | review | manual | nodisp | error."""
    return STATUS_GROUP.get(_status_kind(h), "nomatch")


def _badge_for(h) -> tuple[str, colors.Color]:
    kind = _status_kind(h)
    return kind, COLORS.get(kind, BRAND_SLATE)


# === Rasterización de certificados PDF ===================================

def _pdf_evidence(h) -> str | None:
    """Ruta relativa (dentro de data/) al PDF de evidencia del Hit."""
    du = getattr(h, "download_url", None)
    if du and isinstance(du, str) and du.lower().endswith(".pdf"):
        return du
    for d in (getattr(h, "details", None) or []):
        if isinstance(d, dict):
            for k, v in d.items():
                if ("pdf" in str(k).lower() and "text" not in str(k).lower()
                        and isinstance(v, str) and v.lower().endswith(".pdf")):
                    return v
    return None


def _render_pdf_first_page(pdf_rel: str, dpi: int = 150) -> Path | None:
    """Rasteriza la PRIMERA página de un certificado PDF a PNG usando poppler
    (pdftoppm / pdftocairo). Cachea el PNG junto al PDF. Devuelve la ruta al
    PNG o None si no se pudo (poppler ausente o PDF inválido).

    Esta es la "captura del PDF como tal" que el reporte embebe como evidencia
    para fuentes que emiten certificado (Registraduría, Contraloría), en lugar
    del screenshot de la página web."""
    if not pdf_rel or not isinstance(pdf_rel, str):
        return None
    pdf_full = DATA_DIR / pdf_rel
    if not pdf_full.exists() or pdf_full.suffix.lower() != ".pdf":
        return None
    cache_png = pdf_full.with_name(pdf_full.stem + "_p1.png")
    try:
        if (cache_png.exists()
                and cache_png.stat().st_mtime >= pdf_full.stat().st_mtime
                and cache_png.stat().st_size > 1000):
            return cache_png
    except Exception:
        pass
    prefix = str(cache_png.with_suffix(""))  # pdftoppm -singlefile añade .png
    for tool in ("pdftoppm", "pdftocairo"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            subprocess.run(
                [exe, "-png", "-r", str(dpi), "-f", "1", "-l", "1",
                 "-singlefile", str(pdf_full), prefix],
                check=True, capture_output=True, timeout=40)
            if cache_png.exists() and cache_png.stat().st_size > 1000:
                return cache_png
        except Exception:
            continue
    return None


# === Evidencia visual (screenshots) ======================================
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif")


def _resolve_shot(rel) -> Path | None:
    if not rel or not isinstance(rel, str):
        return None
    full = DATA_DIR / rel
    if full.exists() and full.suffix.lower() in _IMG_EXT:
        return full
    return None


def _evidence_shot(h) -> Path | None:
    """Mejor screenshot de evidencia (prioriza el de resultado, no el form)."""
    shot = _resolve_shot(getattr(h, "download_url", None))
    if shot:
        return shot
    hint = ("screenshot", "captura", "shot", "evidencia", "imagen")
    for d in (getattr(h, "details", None) or []):
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            kl = str(k).lower()
            if any(w in kl for w in hint) and "form" not in kl:
                p = _resolve_shot(v)
                if p:
                    return p
        for k, v in d.items():
            if any(w in str(k).lower() for w in hint):
                p = _resolve_shot(v)
                if p:
                    return p
    return None


def _evidence_for(h) -> tuple[Path | None, str, str | None]:
    """Resuelve la mejor evidencia visual de un Hit.

    Devuelve (image_path, kind, pdf_rel) donde:
      - kind == 'pdf'        → image_path es la PRIMERA página del certificado
                               PDF rasterizada (la "captura del PDF").
      - kind == 'screenshot' → image_path es un screenshot de la página.
      - kind == ''           → sin evidencia visual.
    pdf_rel es la ruta al PDF fuente (si existe), para citarla en el pie."""
    pdf_rel = _pdf_evidence(h)
    if pdf_rel:
        img = _render_pdf_first_page(pdf_rel)
        if img:
            return img, "pdf", pdf_rel
    shot = _evidence_shot(h)
    if shot:
        return shot, "screenshot", pdf_rel
    return None, ("pdf" if pdf_rel else ""), pdf_rel


def _fit_image(path: Path, max_w: float, max_h: float) -> Image | None:
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            iw, ih = im.size
        if iw == 0 or ih == 0:
            return None
        ratio = min(max_w / iw, max_h / ih)
        return Image(str(path), width=iw * ratio, height=ih * ratio)
    except Exception:
        return None


# === Detalles key/value ==================================================

_SKIP_KEYS = ("screenshot", "shot", "pdf_path", "form_screenshot",
              "html_path", "captura", "evidencia", "imagen", "pdf_text",
              "pdf_bytes", "proxies", "token", "inject_info", "submit_info",
              "post_seen", "resp_seen", "nav_changed", "sitekey",
              "root_cause", "soft_timeout")


def _detail_rows_for(h, limit: int = 14) -> list[tuple[str, str]]:
    """Filas (etiqueta, valor) limpias y deduplicadas para el Hit.
    Excluye claves de evidencia/diagnóstico. Prefiere el valor más limpio
    cuando dos claves mapean a la misma etiqueta (ej. estado vs estado_pdf)."""
    acc: dict[str, str] = {}
    order: list[str] = []
    for d in (getattr(h, "details", None) or []):
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            kl = str(k).lower()
            if any(w in kl for w in _SKIP_KEYS):
                continue
            val = _clean_val(v)
            if val in ("", "N/A", "None"):
                continue
            label = _pretty_key(k)
            if label not in acc:
                acc[label] = val
                order.append(label)
            else:
                # Preferir el valor más limpio (sin paréntesis / más corto)
                cur = acc[label]
                if ("(" in cur and "(" not in val) or (len(val) < len(cur)):
                    acc[label] = val
    return [(lbl, acc[lbl]) for lbl in order][:limit]


# === Cajas de color (sin overlap) ========================================

def _box(text: str, *, bg: colors.Color, border: colors.Color,
         width: float, style: ParagraphStyle = BODY, pad: int = 9,
         accent: colors.Color | None = None) -> Table:
    p = Paragraph(text, style)
    t = Table([[p]], colWidths=[width])
    ts = [
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.75, border),
        ("LEFTPADDING", (0, 0), (-1, -1), pad + (4 if accent else 0)),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if accent:
        ts.append(("LINEBEFORE", (0, 0), (0, -1), 4, accent))
    t.setStyle(TableStyle(ts))
    return t


def _pill(label: str, color: colors.Color, *, size: float = 8,
          font: str = None) -> Table:
    """Badge/pill redondeado: fondo claro, texto y punto en color sólido."""
    font = font or FONT_SB
    txt = f"● {label}"
    try:
        tw = pdfmetrics.stringWidth(txt, font, size)
    except Exception:
        tw = pdfmetrics.stringWidth(txt, "Helvetica-Bold", size)
    p = Paragraph(
        f'<font color="{color.hexval()}" size="{size}" face="{font}">'
        f'&#9679; {_esc(label)}</font>',
        ParagraphStyle("pill", fontName=font, fontSize=size,
                       leading=size + 2, textColor=color, alignment=TA_CENTER))
    t = Table([[p]], colWidths=[tw + 20])
    ts = [
        ("BACKGROUND", (0, 0), (-1, -1), _pill_bg(color)),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    try:
        ts.append(("ROUNDEDCORNERS", [7, 7, 7, 7]))
    except Exception:
        pass
    t.setStyle(TableStyle(ts))
    t.hAlign = "LEFT"
    return t


def _chip(label: str, color: colors.Color) -> Table:
    """Chip de estado como pill redondeado."""
    return _pill(label, color)


# === Header & Footer =====================================================

class _NumberedCanvas(_canvas.Canvas):
    """Canvas con banda de marca superior + pie con 'Página X de Y'."""
    report_meta: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages = []

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_chrome(total)
            super().showPage()
        super().save()

    def _draw_chrome(self, total: int):
        self.saveState()
        width, height = LETTER
        meta = _NumberedCanvas.report_meta or {}

        # --- Banda de marca superior (todas las páginas, incl. portada) ---
        try:
            if BAND_PATH.exists():
                self.drawImage(str(BAND_PATH), 0, height - BAND_H,
                               width=width, height=BAND_H, mask='auto')
            else:
                self.setFillColor(BRAND_NAVY)
                self.rect(0, height - BAND_H, width, BAND_H, stroke=0, fill=1)
        except Exception:
            self.setFillColor(BRAND_NAVY)
            self.rect(0, height - BAND_H, width, BAND_H, stroke=0, fill=1)

        # Wordmark de texto a la izquierda, centrado vertical en la banda.
        band_mid = height - BAND_H / 2.0
        try:
            self.setFont(FONT_XB, 15)
            self.setFillColor(colors.white)
            x0 = 2 * cm
            self.drawString(x0, band_mid - 5, "Verify")
            w = self.stringWidth("Verify", FONT_XB, 15)
            self.setFillColor(colors.HexColor("#1de5e9"))
            self.drawString(x0 + w, band_mid - 5, "Data")
        except Exception:
            pass

        # Meta a la derecha, dos líneas.
        codigo = meta.get("codigo", "")
        riesgo = meta.get("riesgo", "")
        line1 = codigo
        if riesgo:
            line1 = f"{codigo} · {riesgo}" if codigo else riesgo
        gen = meta.get("generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        parts = gen.split(" ")
        fecha = parts[0] if parts else gen
        hora = parts[1] if len(parts) > 1 else ""
        line2 = f"{fecha} · {hora}".strip(" ·") if hora else fecha
        self.setFillColor(colors.HexColor("#c7cdf5"))
        if line1:
            self.setFont(FONT_BOLD, 9)
            self.drawRightString(width - 2 * cm, band_mid + 1, line1[:70])
        self.setFont(FONT, 8)
        self.drawRightString(width - 2 * cm, band_mid - 10, line2[:70])

        # --- Footer (todas las páginas) ---
        self.setStrokeColor(BRAND_BORDER)
        self.setLineWidth(0.5)
        self.line(2 * cm, 1.3 * cm, width - 2 * cm, 1.3 * cm)
        self.setFont(FONT, 7)
        self.setFillColor(BRAND_MUTED)
        self.drawString(
            2 * cm, 0.75 * cm,
            "VerifyData · Documento de uso restringido al destinatario")
        self.drawRightString(width - 2 * cm, 0.75 * cm,
                             f"Página {self._pageNumber} de {total}")
        self.restoreState()


# === Portada =============================================================

def _build_cover(query: dict, hits: list, meta: dict) -> list:
    els = []
    els.append(Spacer(1, 0.9 * cm))
    empresa = bool(query.get("__empresa_mode"))

    # Eyebrow
    eyebrow_color = BRAND_MAGENTA if empresa else BRAND_VIOLET
    eyebrow_txt = ("REPORTE DE VERIFICACIÓN · EMPRESA" if empresa
                   else "REPORTE DE VERIFICACIÓN · PERSONA NATURAL")
    els.append(Paragraph(
        f'<font color="{eyebrow_color.hexval()}" size="10" face="{FONT_SB}">'
        f'{_esc(eyebrow_txt)}</font>',
        ParagraphStyle("EB", fontName=FONT_SB, fontSize=10, leading=14,
                       textColor=eyebrow_color, spaceAfter=6)))

    # Título grande: nombre / razón social
    if empresa:
        titulo = query.get("razon_social") or query.get("nombre", "—")
    else:
        titulo = query.get("nombre", "—")
    els.append(Paragraph(
        f'<font color="{BRAND_NAVY.hexval()}" size="24" face="{FONT_XB}">'
        f'{_esc(titulo)}</font>',
        ParagraphStyle("CT", fontName=FONT_XB, fontSize=24, leading=28,
                       textColor=BRAND_NAVY, spaceAfter=6)))

    # Subtítulo
    els.append(Paragraph(
        f'<font color="{BRAND_MUTED.hexval()}" size="11">Evidencia de consulta, '
        'validación de identidad y análisis de riesgo</font>',
        ParagraphStyle("CST", fontName=FONT, fontSize=11, leading=15,
                       textColor=BRAND_MUTED, spaceAfter=0)))
    els.append(Spacer(1, 0.8 * cm))

    # Caja de identificación
    els.append(_query_box(query, meta))
    els.append(Spacer(1, 0.55 * cm))

    # Chip de riesgo general
    from findings import derive_findings
    try:
        res = derive_findings(hits, query)
    except Exception:
        res = {"riesgo": "SIN RIESGO", "findings": []}
    riesgo = res.get("riesgo", "SIN RIESGO")
    rc = _RIESGO_COLOR.get(riesgo, BRAND_MUTED)
    els.append(_pill(f"Riesgo general: {riesgo.title()}", rc, size=9))
    els.append(Spacer(1, 0.55 * cm))

    # KPI cards
    els.append(_stats_table(hits))
    els.append(Spacer(1, 0.7 * cm))

    # Recomendación del sistema
    els.append(_section_header("Recomendación del sistema"))
    els.append(Spacer(1, 0.25 * cm))
    findings = res.get("findings", [])
    if riesgo == "ALTO":
        rec = ("Se detectaron hallazgos de severidad ALTA. Se recomienda "
               "escalar la verificación a revisión humana y suspender la "
               "vinculación hasta validar la evidencia de las fuentes "
               "señaladas en este reporte.")
    elif riesgo == "MEDIO":
        rec = ("Se detectaron hallazgos de severidad media. Se recomienda "
               "revisar la evidencia de las fuentes con coincidencia antes "
               "de continuar con la vinculación.")
    elif riesgo == "BAJO":
        rec = ("Se detectaron hallazgos de severidad baja / informativa. "
               "Verifique la identidad frente a posibles homónimos y revise "
               "la evidencia adjunta.")
    else:
        rec = ("No se detectaron hallazgos adversos en las fuentes "
               "consultadas con éxito. Revise las fuentes no disponibles o "
               "pendientes de revisión detalladas en el resumen ejecutivo "
               "antes de concluir.")
    if findings:
        rec += (f" Total de novedades detectadas: {len(findings)}.")
    els.append(Paragraph(
        f'<font color="{BRAND_DARK.hexval()}" size="10">{_esc(rec)}</font>',
        BODY))
    return els


def _query_box(query: dict, meta: dict) -> Table:

    def cell(k, v):
        return [
            Paragraph(
                f'<font color="{BRAND_MUTED.hexval()}" size="8">'
                f'{_esc(k.upper())}</font>',
                ParagraphStyle("qk", fontName=FONT, fontSize=8, leading=11,
                               textColor=BRAND_MUTED, spaceAfter=2)),
            Paragraph(
                f'<font color="{BRAND_NAVY.hexval()}" size="11" face="{FONT_SB}">'
                f'{_esc(v)}</font>',
                ParagraphStyle("qv", fontName=FONT_SB, fontSize=11, leading=14,
                               textColor=BRAND_NAVY))]

    # Reporte de EMPRESA: NIT / TIPO / PAÍS.
    if query.get("__empresa_mode"):
        nit = query.get("cedula", "—")
        data = [[cell("NIT", nit),
                 cell("TIPO DE ENTIDAD", "Persona jurídica"),
                 cell("PAÍS", "Colombia")]]
    else:
        c = query.get("cedula", "—")
        f = query.get("fecha_exp", "—")
        data = [[cell("TIPO Y NÚMERO DE DOCUMENTO", f"CC {c}"),
                 cell("FECHA DE EXPEDICIÓN", f),
                 cell("PAÍS", "Colombia")]]
    t = Table(data, colWidths=[PAGE_W / 3] * 3)
    ts = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 1, BRAND_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 13),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]
    try:
        ts.append(("ROUNDEDCORNERS", [8, 8, 8, 8]))
    except Exception:
        pass
    t.setStyle(TableStyle(ts))
    return t


def _stats_table(hits: list) -> Table:
    n_total = len(hits)
    n_match = sum(1 for h in hits if _status_group(h) == "match")
    n_clean = sum(1 for h in hits if _status_group(h) == "nomatch")
    n_review = sum(1 for h in hits if _status_group(h) == "review")
    n_nodisp = sum(1 for h in hits if _status_group(h) == "nodisp")
    n_manual = sum(1 for h in hits if _status_group(h) == "manual")
    n_error = sum(1 for h in hits if _status_group(h) == "error")

    cards = [
        ("Fuentes", n_total, BRAND_NAVY),
        ("Con registro", n_match, BRAND_SUCCESS),
        ("Sin registro", n_clean, BRAND_SLATE),
        ("Por revisar", n_review, BRAND_WARN),
        ("No disponible", n_nodisp, BRAND_SLATE),
        ("Manual", n_manual, BRAND_WARN),
        ("Error", n_error, BRAND_ERROR),
    ]
    n = len(cards)
    gap = 0.14 * cm
    card_w = (PAGE_W - gap * (n - 1)) / n

    def _card(lbl, val, color):
        inner = Table([
            [Paragraph(str(val),
                       ParagraphStyle("SN", parent=STAT_NUM, textColor=color))],
            [Paragraph(f'<font color="{BRAND_MUTED.hexval()}" size="7.5">'
                       f'{_esc(lbl.upper())}</font>',
                       ParagraphStyle("SL", parent=STAT_LABEL, fontSize=7.5))],
        ], colWidths=[card_w])
        ts = [
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 1, BRAND_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (0, 0), 11),
            ("BOTTOMPADDING", (0, 0), (0, 0), 1),
            ("TOPPADDING", (0, 1), (0, 1), 1),
            ("BOTTOMPADDING", (0, 1), (0, 1), 11),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]
        try:
            ts.append(("ROUNDEDCORNERS", [6, 6, 6, 6]))
        except Exception:
            pass
        inner.setStyle(TableStyle(ts))
        return inner

    row = []
    widths = []
    for i, (lbl, val, color) in enumerate(cards):
        row.append(_card(lbl, val, color))
        widths.append(card_w)
        if i < n - 1:
            row.append("")
            widths.append(gap)
    t = Table([row], colWidths=widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


# === Encabezado de sección ===============================================

def _section_header(title: str, subtitle: str = "") -> Table:
    inner = [Paragraph(
        f'<font color="{BRAND_NAVY.hexval()}" size="14" face="{FONT_BOLD}">'
        f'{_esc(title)}</font>',
        ParagraphStyle("SH", fontName=FONT_BOLD, fontSize=14, leading=17,
                       textColor=BRAND_NAVY, spaceAfter=0))]
    if subtitle:
        inner.append(Paragraph(
            f'<font color="{BRAND_MUTED.hexval()}" size="9">{_esc(subtitle)}</font>',
            BODY_MUTED))
    t = Table([[inner]], colWidths=[PAGE_W])
    t.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, 0), 3, BRAND_VIOLET),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


# === Resumen ejecutivo (hallazgos estilo TusDatos) =======================

_RIESGO_COLOR = {
    "SIN RIESGO": BRAND_SUCCESS, "BAJO": colors.HexColor("#2563EB"),
    "MEDIO": BRAND_WARN, "ALTO": BRAND_ERROR,
}


def _build_executive(query: dict, hits: list) -> list:
    from findings import derive_findings
    res = derive_findings(hits, query)
    riesgo = res["riesgo"]
    rc = _RIESGO_COLOR.get(riesgo, BRAND_MUTED)
    c = res["counts"]
    els = []
    els.append(_section_header(
        "Resumen ejecutivo",
        "Perfil de riesgo del sujeto, consolidado de todas las fuentes."))
    els.append(Spacer(1, 0.35 * cm))

    # Banner de riesgo
    left = [
        Paragraph('<font color="#64748B" size="8"><b>NIVEL DE RIESGO</b></font>',
                  BODY_MUTED),
        Paragraph(f'<font color="{rc.hexval()}" size="22"><b>{_esc(riesgo)}</b></font>',
                  ParagraphStyle("RB", fontName="Helvetica-Bold", fontSize=22,
                                 leading=26, textColor=rc)),
    ]

    def _cnt(lbl, val, col):
        return [Paragraph(f'<font color="{col}" size="17"><b>{val}</b></font>',
                          ParagraphStyle("cn", alignment=TA_CENTER, fontSize=17,
                                         leading=19, fontName="Helvetica-Bold")),
                Paragraph(f'<font color="#64748B" size="7">{lbl}</font>',
                          ParagraphStyle("cl", alignment=TA_CENTER, fontSize=7,
                                         leading=9))]
    cnt_tbl = Table([[_cnt("ALTO", c["alto"], "#DC2626"),
                      _cnt("MEDIO", c["medio"], "#D97706"),
                      _cnt("BAJO", c["bajo"], "#2563EB"),
                      _cnt("INFORM.", c["informativo"], "#0E9F6E")]],
                    colWidths=[2.0 * cm] * 4)
    cnt_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    banner = Table([[left, cnt_tbl]], colWidths=[PAGE_W - 8.0 * cm, 8.0 * cm])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_BG_ALT),
        ("LINEBEFORE", (0, 0), (0, -1), 5, rc),
        ("BOX", (0, 0), (-1, -1), 0.8, BRAND_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    els.append(banner)
    els.append(Spacer(1, 0.4 * cm))

    # Panel de identidad
    els.append(Paragraph(
        '<font color="#1A2B4A" size="11"><b>Datos de identidad</b></font>', H4))
    els.append(Spacer(1, 0.15 * cm))
    items = list(res["panel"].items())
    rows = []
    for i in range(0, len(items), 2):
        row = []
        for k, v in items[i:i + 2]:
            row.append(Paragraph(
                f'<font color="#64748B" size="7.5">{_esc(k.upper())}</font><br/>'
                f'<font color="#0A1929" size="10.5"><b>{_esc(v)}</b></font>', BODY))
        while len(row) < 2:
            row.append(Paragraph("", BODY))
        rows.append(row)
    panel = Table(rows, colWidths=[PAGE_W / 2] * 2)
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.8, BRAND_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    els.append(panel)
    els.append(Spacer(1, 0.4 * cm))

    # Tabla de hallazgos
    els.append(Paragraph(
        '<font color="#1A2B4A" size="11"><b>Hallazgos detectados</b></font>', H4))
    els.append(Spacer(1, 0.15 * cm))
    findings = res["findings"]
    _th = ParagraphStyle("th", fontName=FONT_SB, textColor=BRAND_MUTED, fontSize=8)
    data = [[Paragraph(f'<font color="{BRAND_MUTED.hexval()}" size="8" '
                       f'face="{FONT_SB}">SEVERIDAD</font>', _th),
             Paragraph(f'<font color="{BRAND_MUTED.hexval()}" size="8" '
                       f'face="{FONT_SB}">NOVEDAD</font>', _th)]]
    if findings:
        for f in findings:
            data.append([
                Paragraph(f'<font color="{f["color"]}" size="8.5"><b>&#9679; '
                          f'{_esc(f["label"])}</b></font>', BODY),
                Paragraph(f'<font color="#0A1929" size="9.5">{_esc(f["novedad"])}</font>',
                          BODY)])
    else:
        data.append([
            Paragraph('<font color="#0E9F6E" size="8.5"><b>&#9679; SIN RIESGO</b></font>',
                      BODY),
            Paragraph('<font color="#0A1929" size="9.5">No se detectaron hallazgos '
                      'adversos en las fuentes consultadas.</font>', BODY)])
    ftbl = Table(data, colWidths=[3.6 * cm, PAGE_W - 3.6 * cm])
    ftbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY_SOFT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, BRAND_BG_ALT]),
        ("BOX", (0, 0), (-1, -1), 0.8, BRAND_BORDER),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BRAND_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    els.append(ftbl)

    # Fuentes no disponibles / con error / por revisar — NUNCA ocultas.
    nodisp = sorted([h.source for h in hits if _status_group(h) == "nodisp"])
    manual = sorted([h.source for h in hits if _status_group(h) == "manual"])
    errors = sorted([h.source for h in hits if _status_group(h) == "error"])
    review = sorted([h.source for h in hits if _status_group(h) == "review"])
    if nodisp or manual or errors or review:
        els.append(Spacer(1, 0.4 * cm))
        els.append(Paragraph(
            '<font color="#1A2B4A" size="11"><b>Fuentes no disponibles, con '
            'error o pendientes de revisión</b></font>', H4))
        els.append(Spacer(1, 0.12 * cm))
        parts = []
        if errors:
            parts.append("<font color='#DC2626' size='9'><b>Con error técnico "
                         "(la fuente NO fue consultada con éxito):</b> </font>"
                         "<font color='#0A1929' size='9'>"
                         + _esc(", ".join(errors)) + ".</font>")
        if nodisp:
            parts.append("<font color='#0A1929' size='9'>Presentaron "
                         "indisponibilidad (bloqueo WAF, portal caído, captcha "
                         "rechazado o dataset no disponible): "
                         "</font><font color='#64748B' size='9'>"
                         + _esc(", ".join(nodisp)) + ".</font>")
        if review:
            parts.append("<font color='#D97706' size='9'><b>Consultadas pero "
                         "requieren revisión visual/humana del resultado:</b> "
                         "</font><font color='#0A1929' size='9'>"
                         + _esc(", ".join(review)) + ".</font>")
        if manual:
            parts.append("<font color='#0A1929' size='9'>Requieren consulta "
                         "manual (login/cuenta/pago): </font>"
                         "<font color='#64748B' size='9'>"
                         + _esc(", ".join(manual)) + ".</font>")
        txt = "<br/>".join(parts)
        els.append(_box(txt, bg=colors.HexColor("#FEF3C7"), border=BRAND_WARN,
                        width=PAGE_W, style=BODY, pad=9, accent=BRAND_WARN))
        els.append(Spacer(1, 0.12 * cm))
        els.append(Paragraph(
            '<font color="#64748B" size="8">Ninguna de estas fuentes debe '
            'interpretarse como "no registra": el sistema no obtuvo un '
            'resultado verificado de ellas.</font>', SMALL))

    # Leyenda de estados — cómo leer los badges del reporte.
    els.append(Spacer(1, 0.45 * cm))
    els.append(Paragraph(
        '<font color="#1A2B4A" size="11"><b>Cómo leer los estados de este '
        'reporte</b></font>', H4))
    els.append(Spacer(1, 0.12 * cm))
    legend = [
        ("COINCIDENCIA EXACTA", BRAND_SUCCESS,
         "Verificada por número de documento."),
        ("COINCIDENCIA PROBABLE", BRAND_SUCCESS,
         "El nombre completo coincide; verificar identidad."),
        ("POSIBLE HOMÓNIMO", colors.HexColor("#D97706"),
         "Coincidencia parcial por nombre; fines informativos."),
        ("NO REGISTRA (VERIFICADO)", BRAND_SLATE,
         "La fuente respondió y confirmó que no hay registro."),
        ("REQUIERE REVISIÓN", BRAND_WARN,
         "Se consultó, pero el veredicto exige revisión humana de la evidencia."),
        ("NO DISPONIBLE / DATASET NO DISPONIBLE", BRAND_SLATE,
         "La fuente no pudo consultarse: NO significa 'sin registro'."),
        ("ERROR / TIMEOUT", BRAND_ERROR,
         "Fallo técnico durante la consulta: NO significa 'sin registro'."),
        ("CONSULTA MANUAL", BRAND_WARN,
         "La fuente exige login, pago o interacción humana."),
    ]
    lrows = []
    for lbl, col, desc in legend:
        lrows.append([
            Paragraph(f'<font color="{col.hexval()}" size="7.5"><b>&#9679; '
                      f'{_esc(lbl)}</b></font>', SMALL),
            Paragraph(f'<font color="#0A1929" size="8">{_esc(desc)}</font>',
                      SMALL)])
    lt = Table(lrows, colWidths=[6.2 * cm, PAGE_W - 6.2 * cm])
    lt.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [colors.white, colors.HexColor("#F7FAFC")]),
        ("BOX", (0, 0), (-1, -1), 0.6, BRAND_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    els.append(lt)
    return els


# === Bloque de fuente (presentación destacada) ===========================

def _source_block(h, category: str, principal: bool) -> list:
    """Bloque profesional para una fuente: cabecera con estado, datos
    estructurados, aviso/error, y evidencia (captura del PDF o screenshot)."""
    els = []
    kind, color = _badge_for(h)
    url = (h.evidence_urls[0] if getattr(h, "evidence_urls", None)
           else getattr(h, "source_url", "") or "")

    # Cabecera: nombre de la fuente + badge (pill)
    _badge_pill = _pill(LABELS[kind], color, size=8)
    _badge_pill.hAlign = "RIGHT"
    header = Table([[
        Paragraph(f'<b>{_esc(h.source)}</b>', H3),
        _badge_pill,
    ]], colWidths=[PAGE_W - 4.6 * cm, 4.6 * cm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY_SOFT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    head_bits = [header, Spacer(1, 0.12 * cm)]
    meta_line = []
    if category:
        meta_line.append(f'<font color="#64748B" size="8"><i>{_esc(category)}</i></font>')
    if url:
        meta_line.append(f'<font color="#00A8A8" size="8"><u>{_esc(url[:90])}</u></font>')
    if meta_line:
        head_bits.append(Paragraph("  ·  ".join(meta_line), LINK))

    # Trazabilidad: evidencia estructurada + metadatos de dataset/tiempos.
    trace_bits = []
    conf = getattr(h, "confidence", None)
    if conf:
        trace_bits.append(f"Confianza: <b>{_esc(conf)}</b>")
    mn = getattr(h, "matched_name", None)
    if mn:
        trace_bits.append(f"Nombre en fuente: <b>{_esc(mn)}</b>")
    md = getattr(h, "matched_document", None)
    if md:
        trace_bits.append(f"Documento en fuente: <b>{_esc(md)}</b>")
    role = getattr(h, "role", None)
    if role:
        trace_bits.append(f"Rol: <b>{_esc(role)}</b>")
    cn = getattr(h, "case_number", None)
    if cn:
        trace_bits.append(f"Radicado: <b>{_esc(cn)}</b>")
    dv = getattr(h, "dataset_version", None)
    dr = getattr(h, "dataset_records", 0) or 0
    if dv or dr:
        ds = f"Dataset: {_esc(dv or 's/f')}"
        if dr:
            ds += f" ({dr:,} registros)".replace(",", ".")
        trace_bits.append(ds)
    el = getattr(h, "elapsed_s", 0) or 0
    if el:
        trace_bits.append(f"Consulta: {el:.1f}s")
    if trace_bits:
        head_bits.append(Spacer(1, 0.08 * cm))
        head_bits.append(Paragraph(
            '<font color="#64748B" size="7.5">' + "  ·  ".join(trace_bits)
            + "</font>", SMALL))
    head_bits.append(Spacer(1, 0.25 * cm))

    # Resumen — ámbar si el resultado exige revisión humana, cyan si es
    # un veredicto automático verificado.
    if h.summary and not h.error:
        if _status_group(h) == "review":
            head_bits.append(_box(
                f'<font color="#0A1929" size="10">{_esc(h.summary)}</font><br/>'
                '<font color="#92400E" size="8"><b>Este resultado NO fue '
                'verificado automáticamente: revisar la evidencia visual '
                'antes de concluir.</b></font>',
                bg=colors.HexColor("#FEF3C7"), border=BRAND_WARN,
                width=PAGE_W, pad=9, accent=BRAND_WARN))
        else:
            head_bits.append(_box(
                f'<font color="#0A1929" size="10">{_esc(h.summary)}</font>',
                bg=BRAND_CYAN_SOFT, border=BRAND_CYAN, width=PAGE_W, pad=9,
                accent=BRAND_CYAN))
        head_bits.append(Spacer(1, 0.28 * cm))
    els.append(KeepTogether(head_bits))

    # Datos estructurados (tabla key/value 2-col)
    rows = _detail_rows_for(h, limit=14)
    if rows:
        kv = []
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            cells = []
            for k, v in pair:
                cells.append(Paragraph(
                    f'<font color="#64748B" size="8">{_esc(k)}</font><br/>'
                    f'<font color="#0A1929" size="10"><b>{_esc(v[:120])}</b></font>',
                    BODY))
            while len(cells) < 2:
                cells.append(Paragraph("", BODY))
            kv.append(cells)
        kvt = Table(kv, colWidths=[PAGE_W / 2] * 2)
        kvt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.6, BRAND_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, BRAND_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        els.append(kvt)
        els.append(Spacer(1, 0.28 * cm))
    elif not (h.summary and not h.error):
        els.append(Paragraph(
            '<font color="#64748B" size="9">Sin datos estructurados adicionales.</font>',
            BODY_MUTED))
        els.append(Spacer(1, 0.2 * cm))

    # Aviso / Error
    if h.notice and not h.error:
        els.append(_box(
            f'<font color="#0A1929" size="9"><b>Aviso:</b> {_esc(h.notice)}</font>',
            bg=colors.HexColor("#FEF3C7"), border=BRAND_WARN, width=PAGE_W,
            pad=8, accent=BRAND_WARN))
        els.append(Spacer(1, 0.25 * cm))
    notes = getattr(h, "notes", None)
    if notes:
        els.append(Paragraph(
            f'<font color="#64748B" size="8"><b>Observación metodológica:</b> '
            f'{_esc(notes)}</font>', SMALL))
        els.append(Spacer(1, 0.2 * cm))
    if h.error:
        els.append(_box(
            f'<font color="#0A1929" size="9"><b>Error:</b> {_esc(h.error)}</font>',
            bg=colors.HexColor("#FEE2E2"), border=BRAND_ERROR, width=PAGE_W,
            pad=8, accent=BRAND_ERROR))
        els.append(Spacer(1, 0.25 * cm))

    # Evidencia: captura del PDF (preferida) o screenshot
    img_path, kind_ev, pdf_rel = _evidence_for(h)
    if img_path or pdf_rel:
        evi = []
        if kind_ev == "pdf":
            title = "Evidencia — Certificado PDF capturado"
        else:
            title = "Evidencia — Captura de pantalla"
        evi.append(Paragraph(
            f'<font color="#1A2B4A" size="10"><b>{title}</b></font>', H4))
        evi.append(Spacer(1, 0.12 * cm))
        if pdf_rel:
            evi.append(Paragraph(
                f'<font color="#00A8A8" size="8">&#128196; Documento fuente: '
                f'<u>{_esc(pdf_rel)}</u></font>', LINK))
            evi.append(Spacer(1, 0.12 * cm))
        if img_path:
            img = _fit_image(img_path, max_w=PAGE_W - 1.0 * cm, max_h=13.0 * cm)
            if img:
                img.hAlign = "CENTER"
                frame = Table([[img]], colWidths=[img.drawWidth + 10])
                frame.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 0.6, BRAND_BORDER),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]))
                frame.hAlign = "CENTER"
                evi.append(frame)
                evi.append(Spacer(1, 0.08 * cm))
                evi.append(Paragraph(_esc(img_path.name), CAPTION))
        els.append(KeepTogether(evi))

    els.append(Spacer(1, 0.5 * cm))
    return els


def _build_principal(hits: list, cat_map: dict) -> list:
    principals = sorted([h for h in hits if _is_principal(h)],
                        key=_principal_order)
    if not principals:
        return []
    els = [_section_header(
        "Fuentes principales — Excluyentes y prioritarias",
        "Fuentes de mayor peso para la decisión de cumplimiento. "
        "Cada una con su resultado, datos y evidencia verificable.")]
    els.append(Spacer(1, 0.4 * cm))
    for i, h in enumerate(principals):
        els.extend(_source_block(h, cat_map.get(h.source, ""), principal=True))
        if i < len(principals) - 1:
            els.append(HRFlowable(width=PAGE_W, thickness=0.5,
                                  color=BRAND_BORDER, spaceBefore=0,
                                  spaceAfter=8))
    return els


# === Información general (resto de fuentes) ==============================

def _general_card(h, category: str) -> list:
    els = []
    kind, color = _badge_for(h)
    els.append(_pill(LABELS[kind], color, size=7))
    els.append(Spacer(1, 0.1 * cm))
    els.append(Paragraph(
        f'<font size="9.5" face="{FONT_SB}">{_esc(h.source)}</font>',
        ParagraphStyle("gcn", fontName=FONT_SB, fontSize=9.5, leading=12,
                       textColor=BRAND_NAVY)))
    els.append(Spacer(1, 0.06 * cm))
    note = ""
    if kind == "match":
        note = h.summary or ""
    elif kind == "error":
        note = h.error or ""
    elif kind in ("nodisp", "manual"):
        note = h.notice or h.summary or ""
    else:
        note = h.summary or "Sin coincidencias."
    if note:
        els.append(Paragraph(
            f'<font color="#475569" size="8">{_esc(note[:150])}</font>', BODY))
    rows = _detail_rows_for(h, limit=3)
    for k, v in rows:
        els.append(Paragraph(
            f'<font color="#64748B" size="7.5">{_esc(k)}:</font> '
            f'<font color="#0A1929" size="8">{_esc(v[:70])}</font>', BODY))
    return els


# Categorías que son "listas precargadas" (datasets consultados en bloque),
# al estilo de la sección homónima del reporte TusDatos.
_LISTAS_CATS = ("Sanciones internacionales", "Corrupción internacional",
                "Crimen y fugitivos", "PEP (Personas Expuestas Políticamente)")

# Verdicto compacto por grupo de estado, para la grilla de listas.
_GRID_VERDICT = {
    "match":   ("REGISTRA", BRAND_SUCCESS),
    "nomatch": ("Registro no encontrado", BRAND_MUTED),
    "review":  ("Requiere revisión", BRAND_WARN),
    "manual":  ("Consulta manual", BRAND_WARN),
    "nodisp":  ("No disponible", BRAND_SLATE),
    "error":   ("Error / no consultada", BRAND_ERROR),
}


def _build_listas_precargadas(hits: list, cat_map: dict) -> list:
    """Grilla compacta estilo TusDatos: cada lista precargada (sanciones,
    corrupción, fugitivos, PEP) con su veredicto de un vistazo. Da la
    "vista de tablero" que el cliente pidió replicar."""
    listas = [h for h in hits if cat_map.get(h.source, "") in _LISTAS_CATS]
    if not listas:
        return []
    els = [_section_header(
        "Listas precargadas",
        "Consulta por nombre y documento contra datasets oficiales "
        "cacheados localmente. Verificados con su fecha y número de "
        "registros.")]
    els.append(Spacer(1, 0.2 * cm))
    els.append(Paragraph(
        '<font color="#0A1929" size="8.5">El resultado se produce por '
        'nombre y/o documento. Dada la existencia de homónimos, una '
        'coincidencia puede corresponder a otra persona con el mismo '
        'nombre; verificar identidad.</font>', BODY))
    els.append(Spacer(1, 0.3 * cm))

    by_cat: dict[str, list] = {}
    for h in listas:
        by_cat.setdefault(cat_map.get(h.source, "Otros"), []).append(h)

    for cat in sorted(by_cat.keys()):
        group = sorted(by_cat[cat], key=lambda h: h.source)
        head = Table([[Paragraph(
            f'<font color="#1A2B4A" size="10"><b>{_esc(cat)}</b></font> '
            f'<font color="#64748B" size="8">({len(group)})</font>', H4)]],
            colWidths=[PAGE_W])
        head.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY_SOFT),
            ("LINEBEFORE", (0, 0), (0, -1), 3, BRAND_CYAN),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        rows_data = []

        def _cell(h):
            grp = _status_group(h)
            verdict, col = _GRID_VERDICT.get(grp, ("—", BRAND_MUTED))
            if grp == "match":
                dr = getattr(h, "dataset_records", 0) or 0
                if dr:
                    verdict = "REGISTRA"
            name = _esc(h.source.split(" (")[0])[:60]
            extra = ""
            dv = getattr(h, "dataset_version", None)
            if dv:
                extra = f'<br/><font color="#94A3B8" size="6.5">actualizado {_esc(dv[:10])}</font>'
            return [
                Paragraph(f'<font color="#1A2B4A" size="8"><b>{name}</b></font>{extra}',
                          SMALL),
                Paragraph(f'<font color="{col.hexval()}" size="8">'
                          f'&#9679; {_esc(verdict)}</font>', SMALL)]

        # 2 fuentes por fila (4 columnas: nombre|verdicto|nombre|verdicto)
        for i in range(0, len(group), 2):
            l = _cell(group[i])
            r = _cell(group[i + 1]) if i + 1 < len(group) else [
                Paragraph("", SMALL), Paragraph("", SMALL)]
            rows_data.append(l + r)
        colw = [PAGE_W * 0.30, PAGE_W * 0.20, PAGE_W * 0.30, PAGE_W * 0.20]
        gt = Table(rows_data, colWidths=colw)
        gt.setStyle(TableStyle([
            ("ROWBACKGROUNDS", (0, 0), (-1, -1),
             [colors.white, colors.HexColor("#F7FAFC")]),
            ("BOX", (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, BRAND_BORDER),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        block = [head, Spacer(1, 0.1 * cm), gt, Spacer(1, 0.3 * cm)]
        if len(group) <= 8:
            els.append(KeepTogether(block))
        else:
            els.extend(block)
    return els


def _build_general(hits: list, cat_map: dict) -> list:
    # Las listas precargadas ya se presentan en su propia sección compacta;
    # aquí va el resto (registros especializados, contratación, etc.).
    others = [h for h in hits if not _is_principal(h)
              and cat_map.get(h.source, "") not in _LISTAS_CATS]
    if not others:
        return []
    els = [_section_header(
        "Información general",
        "Resto de fuentes consultadas, agrupadas por categoría.")]
    els.append(Spacer(1, 0.35 * cm))

    by_cat: dict[str, list] = {}
    for h in others:
        by_cat.setdefault(cat_map.get(h.source, "Otros"), []).append(h)

    for cat in sorted(by_cat.keys()):
        group = sorted(by_cat[cat], key=lambda h: (_status_group(h) != "match",
                                                   h.source))
        cat_head = Table([[Paragraph(
            f'<font color="#1A2B4A" size="10.5"><b>{_esc(cat)}</b></font> '
            f'<font color="#64748B" size="8">({len(group)})</font>', H4)]],
            colWidths=[PAGE_W])
        cat_head.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY_SOFT),
            ("LINEBEFORE", (0, 0), (0, -1), 3, BRAND_CYAN),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        cat_rows = [cat_head, Spacer(1, 0.12 * cm)]

        cards = [_general_card(h, cat) for h in group]
        grid = []
        for i in range(0, len(cards), 2):
            left = cards[i]
            right = cards[i + 1] if i + 1 < len(cards) else [Spacer(1, 0.1 * cm)]
            grid.append([left, right])
        gt = Table(grid, colWidths=[PAGE_W / 2] * 2)
        gt.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, BRAND_BORDER),
        ]))
        cat_rows.append(gt)
        cat_rows.append(Spacer(1, 0.35 * cm))
        # Grupos pequeños se mantienen juntos; los grandes pueden partir página.
        if len(group) <= 4:
            els.append(KeepTogether(cat_rows))
        else:
            els.extend(cat_rows)
    return els


# === API principal =======================================================

def generate_pdf(query: dict, hits: list, output_path: str | None = None) -> bytes:
    """Genera el PDF profesional (fuentes principales primero, sin índice)."""
    from sources import registry
    cat_map = {s.name: s.category for s in registry.all_sources()}
    url_map = {s.name: s.source_url for s in registry.all_sources()}
    for h in hits:
        if not getattr(h, "source_url", None):
            h.source_url = url_map.get(h.source, "")

    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Nivel de riesgo (frase legible) + código de reporte para la banda.
    try:
        from findings import derive_findings
        _riesgo = derive_findings(hits, query or {}).get("riesgo", "SIN RIESGO")
    except Exception:
        _riesgo = "SIN RIESGO"
    riesgo_label = ("Sin riesgo" if _riesgo == "SIN RIESGO"
                    else f"Riesgo {_riesgo.lower()}")
    import hashlib
    _seed = f"{(query or {}).get('cedula','')}|{gen}"
    _suffix = hashlib.sha1(_seed.encode("utf-8")).hexdigest()[:6].upper()
    codigo = f"CV-{datetime.now().year}-{_suffix}"

    meta = {
        "nombre": (query or {}).get("nombre", ""),
        "cedula": (query or {}).get("cedula", ""),
        "generated": gen,
        "codigo": codigo,
        "riesgo": riesgo_label,
    }
    _NumberedCanvas.report_meta = meta

    buffer = io.BytesIO() if output_path is None else None
    target = buffer or output_path

    doc = SimpleDocTemplate(
        target, pagesize=LETTER,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2.4 * cm, bottomMargin=1.6 * cm,
        title="VerifyData — Reporte de Verificación",
        author="VerifyData",
        subject="Verificación de identidad y análisis de riesgo",
    )

    story = []
    story.extend(_build_cover(query, hits, meta))
    story.append(PageBreak())
    story.extend(_build_executive(query, hits))
    story.append(PageBreak())
    principal = _build_principal(hits, cat_map)
    if principal:
        story.extend(principal)
        story.append(PageBreak())
    listas = _build_listas_precargadas(hits, cat_map)
    if listas:
        story.extend(listas)
        story.append(PageBreak())
    story.extend(_build_general(hits, cat_map))

    doc.build(story, canvasmaker=_NumberedCanvas)
    if buffer is not None:
        return buffer.getvalue()
    return b""


# === Backward-compat alias ===============================================

def build_report(hits, query=None, out=None):
    q = query or {}
    if out is None:
        return generate_pdf(q, hits, None)
    return generate_pdf(q, hits, out)


if __name__ == "__main__":
    import sys, json
    sys.path.insert(0, ".")
    from sources import Hit
    # Test con el último run real si existe
    run_file = DATA_DIR / "last_run.json"
    if run_file.exists():
        run = json.loads(run_file.read_text(encoding="utf-8"))
        hits = []
        for name, d in run.get("sources", {}).items():
            try:
                hits.append(Hit(**{k: v for k, v in d.items()
                                   if k != "source_url"}))
            except Exception:
                continue
        out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_report.pdf"
        generate_pdf(run.get("query", {}), hits, out)
        print(f"PDF generado desde last_run.json → {out} ({len(hits)} fuentes)")
    else:
        fake = [Hit(source="Registraduría — Estado de cédula", matched=True,
                    summary="VIGENTE", details=[{"estado": "VIGENTE"}],
                    elapsed_s=0.3)]
        generate_pdf({"nombre": "TEST", "cedula": "80793180"}, fake,
                     "/tmp/test_report.pdf")
        print("PDF de prueba → /tmp/test_report.pdf")
