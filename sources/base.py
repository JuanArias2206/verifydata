"""
sources/base.py — Clases base para todas las fuentes de datos.

Cada fuente es una clase que hereda de `Source` y se registra con
`@register` para aparecer automáticamente en la lista de fuentes
consultadas.

  @register
  class OfacSdnSource:
      name = "OFAC SDN"
      source_url = "https://sanctionssearch.ofac.treas.gov/"
      category = "Sanciones internacionales"
      requires_captcha = False
      def fetch(self, nombre, cedula, fecha_exp=None, solver=None) -> Hit: ...

El método `fetch` debe:
  - Retornar `Hit(...)` con los datos (matched=True|False, details=[...])
  - NUNCA lanzar excepción. Capturar todo y devolver Hit con `error=...`
  - Si la fuente requiere captcha, llamar `solver.solve_image(...)` y
    capturar `CaptchaUnsolved` para devolver `Hit(..., captcha_required=True)`
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# ── DATA path: /tmp en Vercel, ./data en local ─────────────────────
def get_data_path() -> Path:
    """Devuelve el directorio de datos: /tmp/data en Vercel, ./data en local."""
    if os.environ.get("VERIFYDATA_ENV") == "production":
        p = Path("/tmp/data")
    else:
        p = Path(__file__).parent.parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    (p / "screenshots").mkdir(parents=True, exist_ok=True)
    (p / "certs").mkdir(parents=True, exist_ok=True)
    return p

DATA = get_data_path()


# ---------- Resultado ----------

# Estados finos que una fuente puede declarar explícitamente en Hit.status.
# Si status es None, report._status_kind() lo infiere por heurística
# (compatibilidad con fuentes aún no migradas).
STATUS_KINDS = frozenset({
    "match_exact",        # coincidencia verificada por documento
    "match_probable",     # coincidencia fuerte por nombre completo
    "possible_homonym",   # coincidencia parcial: posible homónimo
    "nomatch_verified",   # la fuente respondió y confirmó que NO registra
    "manual_review",      # se consultó pero el resultado exige revisión humana
    "manual",             # la fuente solo admite consulta manual
    "nodisp",             # fuente caída / bloqueada / WAF
    "error",              # error técnico (red, parseo, excepción)
    "timeout",            # la fuente no respondió a tiempo
    "captcha_blocked",    # captcha/WAF impidió completar la consulta
    "dataset_missing",    # la lista local no está descargada: NO se consultó
    "dataset_stale",      # la lista local está vencida más allá del TTL
    "not_implemented",    # stub sin automatización
    "requires_login",     # exige cuenta/credenciales
    "requires_payment",   # exige pago
    "source_changed",     # la estructura de la fuente cambió y el parser falló
})


@dataclass
class Hit:
    source: str
    matched: bool
    summary: str
    details: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    notice: str | None = None
    download_url: str | None = None
    elapsed_s: float = 0.0
    captcha_required: bool = False
    evidence_urls: list[str] = field(default_factory=list)
    # --- Evidencia estructurada (opcional; None/0 si la fuente no la produce) ---
    status: str | None = None            # estado fino explícito (ver STATUS_KINDS)
    confidence: str | None = None        # "exacta" | "fuerte" | "posible"
    matched_name: str | None = None      # nombre tal como aparece en la fuente
    matched_document: str | None = None  # documento tal como aparece en la fuente
    role: str | None = None              # rol: demandado, demandante, titular…
    case_number: str | None = None       # radicado / expediente / número de caso
    dataset_version: str | None = None   # fecha/versión del dataset local usado
    dataset_records: int = 0             # registros cargados en el dataset local
    error_type: str | None = None        # timeout | network | parsing | blocked…
    requires_manual_review: bool = False # el veredicto NO es automático
    notes: str | None = None             # observaciones metodológicas

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_url": "",  # se completa en registry
            "matched": self.matched,
            "summary": self.summary,
            "details": self.details,
            "error": self.error,
            "notice": self.notice,
            "download_url": self.download_url,
            "elapsed_s": self.elapsed_s,
            "captcha_required": self.captcha_required,
            "evidence_urls": self.evidence_urls,
            "status": self.status,
            "confidence": self.confidence,
            "matched_name": self.matched_name,
            "matched_document": self.matched_document,
            "role": self.role,
            "case_number": self.case_number,
            "dataset_version": self.dataset_version,
            "dataset_records": self.dataset_records,
            "error_type": self.error_type,
            "requires_manual_review": self.requires_manual_review,
            "notes": self.notes,
        }


# ---------- Excepciones ----------

class CaptchaUnsolved(Exception):
    """Lanzada cuando un solver no puede resolver el captcha."""


# ---------- Interfaz ----------

class Source(Protocol):
    name: str
    source_url: str
    category: str
    requires_captcha: bool
    captcha_type: str | None    # "image", "recaptcha_v2", "hcaptcha", "trivia"
    def fetch(self, nombre: str, cedula: str | None,
              fecha_exp: str | None = None,
              solver: Any = None) -> Hit: ...


# ---------- Helper de timing seguro ----------

import time

def safe_fetch(src: "Source", nombre: str, cedula: str | None,
               fecha_exp: str | None = None,
               solver: Any = None) -> Hit:
    """Ejecuta source.fetch() con medición de tiempo y captura de errores."""
    t0 = time.time()
    try:
        hit = src.fetch(nombre, cedula, fecha_exp, solver)
    except CaptchaUnsolved as e:
        return Hit(
            source=src.name, matched=False,
            summary="Requiere captcha",
            notice=f"Captcha no resuelto: {e}. Se requiere servicio de "
                   f"resolución para automatizar esta fuente.",
            captcha_required=True,
            evidence_urls=[src.source_url],
            elapsed_s=time.time() - t0,
        )
    except Exception as e:
        return Hit(
            source=src.name, matched=False, summary="",
            error=f"{type(e).__name__}: {e}",
            evidence_urls=[src.source_url],
            elapsed_s=time.time() - t0,
        )
    # Completar source_url y evidence_urls si el source no los puso
    if hit.evidence_urls is None:
        hit.evidence_urls = [src.source_url]
    elif not hit.evidence_urls:
        hit.evidence_urls = [src.source_url]
    return hit
