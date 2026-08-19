"""
sources/worldbank_debarred.py — World Bank Debarred Firms & Individuals.

Fuente para la lista oficial del Banco Mundial de firmas e individuos
inhabilitados (debarred / cross-debarred) para participar en contratos
financiados por el Banco Mundial.

URL oficial:
    https://www.worldbank.org/en/projects-operations/procurement/debarred-firms

La página oficial carga la tabla completa vía JavaScript desde un
endpoint de Azure APIM:

    https://apigwext.worldbank.org/dvsvc/v1.0/json/APPLICATION/
        ADOBE_EXPRNCE_MGR/FIRM/SANCTIONED_FIRM

Este endpoint requiere un header `Ocp-Apim-Subscription-Key` que el
sitio publica en su JS y que está ligado a suscripciones con IP
aprobada — no es accesible de forma estable desde pipelines de
automatización fuera del navegador.

Por eso la fuente descarga el mirror público de **OpenSanctions**
(https://www.opensanctions.org/datasets/worldbank_debarred/), que
refresca la lista del Banco Mundial diariamente. El endpoint público:

    https://data.opensanctions.org/datasets/latest/worldbank_debarred/targets.simple.csv

devuelve un CSV simplificado (~450 KB, ~1346 entidades) con columnas
útiles para matching: `name`, `aliases`, `countries`, `addresses`,
`sanctions`, `identifiers`.

Estrategia:
  1. Cache local en `data/worldbank_debarred_cache.json` con TTL=24h
     (la lista oficial del BM actualiza cada 3 horas; el mirror de
     OpenSanctions refresca 1 vez al día — TTL 24h es el óptimo).
  2. Si el cache está fresco (<24h), se usa directamente.
  3. Si está vencido o no existe, se descarga y se persiste.
  4. Búsqueda: tokenización (≥3 chars) case-insensitive contra las
     columnas `name` y `addresses` (siguiendo la sugerencia oficial
     del BM de buscar por porción del nombre y/o dirección).

Sin captcha, sin login, sin JavaScript dinámico, sin browser.
"""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .base import Hit
from .registry import register
from .local_lists import normalize, tokenize


# ---------- Configuración ----------

SOURCE_URL = (
    "https://www.worldbank.org/en/projects-operations/"
    "procurement/debarred-firms"
)
# Mirror público (OpenSanctions) — refresca 1 vez al día
DATA_URL = (
    "https://data.opensanctions.org/datasets/latest/"
    "worldbank_debarred/targets.simple.csv"
)
UA = "VerifyData-Demo/1.0 (contacto: verifydata.local)"
CACHE_TTL = timedelta(hours=24)  # cache local 24h
HTTP_TIMEOUT = 60  # segundos para descarga inicial


def _cache_path() -> Path:
    """Ruta del cache local: data/worldbank_debarred_cache.json."""
    return Path(__file__).parent.parent / "data" / "worldbank_debarred_cache.json"


# ---------- Descarga + parseo ----------

def _decode_smart(text: str) -> str:
    """Arregla mojibake UTF-8↔Latin-1 en un string que parece estar en
    Latin-1 cuando en realidad era UTF-8.

    Caso típico visto en el CSV de OpenSanctions: 'NÉSTOR' llega como
    'NÃ‰STOR' porque `requests` asume ISO-8859-1 cuando el Content-Type
    es solo 'text/csv' (sin charset). Si vemos los patrones típicos de
    mojibake UTF-8→Latin-1 (Ã seguido de ©, ©, ±, etc.), re-decodificamos.

    También maneja DOBLE mojibake: el caso 'NÃ‰STOR' donde el carácter
    `‰` (U+2030) está presente indica que el string ya fue decodificado
    mal DOS veces. En ese caso, primero reemplazamos los pares típicos
    de doble mojibake por sus equivalentes Latin-1 de una capa, y luego
    aplicamos la decodificación UTF-8 normal.

    Es seguro: si el string ya está bien en UTF-8, no contiene esos
    patrones y devuelve el original.
    """
    if not text:
        return text

    # Paso 1: detectar y arreglar DOBLE mojibake.
    # El carácter `‰` (U+2030) es típico de doble encoding. Aparece
    # cuando un string UTF-8 fue decodificado mal DOS veces: la
    # primera vez 0xE2 0x80 0xB0 (UTF-8 de ‰) → interpretado como
    # Latin-1, los 3 bytes se vuelven chars. Luego alguien vió `‰`
    # en el resultado y lo guardó como UN solo codepoint (U+2030).
    # Cuando el string original era 'É' (U+00C9), los bytes UTF-8 son
    # 0xC3 0x89. Si eso se decodifica como Latin-1 → 0xC3 (= Ã) + 0x89
    # (= control char). PERO si en algún punto el control char 0x89
    # se reasignó al glifo ‰ (CP1252 style), el string queda como
    # 'Ã‰' (con ‰ en lugar de control char), y NO se puede re-codificar
    # como Latin-1 directamente.
    #
    # ESTRATEGIA: si el string contiene ‰ (U+2030), es un caso de doble
    # mojibake. Reemplazo TODOS los ‰ por el control char 0x89
    # (Latin-1 puro), y luego aplico latin-1 → utf-8 decoding. Esto
    # "deshace" la segunda capa de mojibake. El resultado es un string
    # que SI está en UTF-8 (o UTF-8 con mojibake simple de una capa).
    if "\u2030" in text:
        # Reemplazar todos los ‰ por 0x89 (control char Latin-1)
        text = text.replace("\u2030", "\x89")
        try:
            b = text.encode("latin-1", errors="strict")
            text = b.decode("utf-8", errors="strict")
            return text
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Si falla, devolver el texto con ‰ → 0x89 (mejor que
            # dejarlo con ‰ que no es latin-1).
            return text

    # Paso 2: arreglar mojibake SIMPLE (UTF-8→Latin-1, una sola capa).
    # Caso típico: 'JOSÉ' llega como 'JOSÃ©' (5 chars en vez de 4).
    # Detección: presencia de Ã (U+00C3) seguido de otro char high-bit.
    single_mojibake_signs = (
        "Ã©", "Ã¡", "Ã­", "Ã³", "Ãº", "Ã±",
        "Ã‰", "Ã\x80", "Ã\x81", "Ã\x89",
        "Ã¼", "Ã¤", "Ã¶",
    )
    if any(p in text for p in single_mojibake_signs):
        try:
            b = text.encode("latin-1", errors="strict")
            text = b.decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return text


def _fix_row_mojibake(row: dict) -> dict:
    """Aplica _decode_smart a todas las columnas string del row."""
    out: dict = {}
    for k, v in row.items():
        s = v or ""
        # Re-decodificar si tiene mojibake
        out[k] = _decode_smart(s) if isinstance(s, str) else s
    return out


def _download_csv() -> list[dict[str, str]]:
    """Descarga el CSV desde OpenSanctions. Devuelve lista de dicts.

    2026-07-02: fuerza UTF-8 al decodificar. El Content-Type del servidor
    es 'text/csv' sin charset, por lo que `requests` asumía ISO-8859-1 y
    el resultado llegaba con mojibake (NÃ‰STOR en vez de NÉSTOR). Esto
    rompía el matching para nombres con acentos/ñ.
    """
    headers = {"User-Agent": UA, "Accept": "text/csv,*/*;q=0.9"}
    r = requests.get(DATA_URL, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    # Forzar UTF-8: el CSV viene con bytes UTF-8, no Latin-1.
    # Verificado 2026-07-02: r.content[0:3] = b'\\xef\\xbb\\xbf' o sin BOM,
    # pero los bytes de 'É' son 0xC3 0x89 (UTF-8), no 0xC9 (Latin-1).
    r.encoding = "utf-8"
    # Detección adicional: si el resultado todavía parece mojibake
    # (r.encoding ya fue utf-8 pero los bytes no), reaplicar
    # heurística de doble encoding.
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        if not row or not row.get("name"):
            continue
        fixed = _fix_row_mojibake(row)
        rows.append({k: (v or "").strip() for k, v in fixed.items()})
    return rows


def _load_cache() -> tuple[list[dict[str, str]], datetime | None, str | None]:
    """Carga cache desde disco. Devuelve (rows, fetched_at, error).

    2026-07-02: aplica smart-decode a cada row para arreglar caches
    viejos que tengan mojibake (generados cuando `_download_csv` no
    forzaba UTF-8).
    """
    p = _cache_path()
    if not p.exists():
        return [], None, "cache_missing"
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], None, f"cache_corrupt:{type(e).__name__}"
    raw_rows = blob.get("rows", [])
    # Aplicar smart-decode a cada row (idempotente — si ya está bien,
    # _decode_smart devuelve el original).
    rows = [_fix_row_mojibake(r) for r in raw_rows]
    fetched_at_str = blob.get("fetched_at")
    fetched_at = None
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
        except ValueError:
            fetched_at = None
    return rows, fetched_at, None


def _save_cache(rows: list[dict[str, str]], source_url: str = DATA_URL) -> None:
    """Persiste cache atómicamente (write to .tmp, rename)."""
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "count": len(rows),
        "rows": rows,
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _get_entries() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Devuelve (rows, info) con la lista más fresca posible.

    info: {fresh: bool, source: str, count, fetched_at, elapsed_s}
    """
    t0 = time.time()
    rows, fetched_at, err = _load_cache()
    info: dict[str, Any] = {
        "fresh": False,
        "source": "cache",
        "count": len(rows),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "cache_error": err,
    }
    fresh = False
    if fetched_at and rows and (datetime.now(timezone.utc) - fetched_at) < CACHE_TTL:
        fresh = True
        info["fresh"] = True
        info["elapsed_s"] = time.time() - t0
        return rows, info
    # Cache miss o vencido: descargar
    try:
        rows = _download_csv()
        _save_cache(rows)
        info.update({
            "fresh": True,
            "source": "download",
            "count": len(rows),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "cache_error": None,
        })
    except Exception as e:
        # Si la descarga falla y hay cache (incluso vencido), lo usamos
        if rows:
            info.update({
                "source": "cache_stale",
                "cache_error": f"download_failed:{type(e).__name__}:{e}",
            })
        else:
            raise
    info["elapsed_s"] = time.time() - t0
    return rows, info


# ---------- Matching ----------

def _match_rows(nombre: str, rows: list[dict[str, str]],
                limit: int = 10) -> list[dict[str, str]]:
    """Busca por tokens en name + addresses.

    Misma heurística que las otras fuentes del proyecto: tokenizar
    `nombre` (≥3 chars) y exigir que todos los tokens aparezcan en
    el campo `name` o en `addresses` (normalizado, sin acentos).
    Sigue la sugerencia oficial del BM de buscar por porción de
    nombre o dirección.
    """
    if not nombre:
        return []
    tokens = tokenize(nombre)
    if not tokens:
        # nombre sin tokens utilizables (todos <3 chars): fallback a
        # substring case-insensitive sobre el nombre completo
        norm_nombre = normalize(nombre)
        return [r for r in rows
                if norm_nombre in normalize(r.get("name", ""))
                or norm_nombre in normalize(r.get("addresses", ""))][:limit]
    matches: list[dict[str, str]] = []
    for r in rows:
        haystack = normalize(f"{r.get('name','')} {r.get('addresses','')} "
                             f"{r.get('aliases','')}")
        if all(t in haystack for t in tokens):
            matches.append(r)
            if len(matches) >= limit:
                break
    return matches


# ---------- Source class ----------

@register
class WorldBankDebarredSource:
    """World Bank Listing of Ineligible Firms & Individuals.

    Datos cacheados (24h TTL) desde el mirror público de OpenSanctions.
    La página oficial del BM carga la misma lista cada 3 horas, así
    que 24h es un trade-off aceptable: la lista puede tener <24h de
    retraso respecto a la página oficial, pero seguimos siendo
    diariamente-frescos sin depender del endpoint APIM bloqueado.
    """
    name = "Banco Mundial — Debarred Firms & Individuals"
    source_url = SOURCE_URL
    category = "Sanciones internacionales"
    requires_captcha = False
    captcha_type = None

    def fetch(self, nombre: str, cedula: str | None = None,
              fecha_exp: str | None = None,
              solver: Any = None) -> Hit:
        """Busca `nombre` en la lista de debarred del Banco Mundial.

        Returns:
            Hit con matched=True si hay coincidencia.
        """
        t0 = time.time()
        if not nombre:
            return Hit(
                source=self.name, matched=False, summary="",
                notice="Se requiere nombre para buscar en la lista del "
                       "Banco Mundial.",
                evidence_urls=[self.source_url],
                elapsed_s=time.time() - t0,
            )
        try:
            rows, info = _get_entries()
        except Exception as e:
            return Hit(
                source=self.name, matched=False, summary="",
                error=f"{type(e).__name__}: {e}",
                evidence_urls=[self.source_url, DATA_URL],
                elapsed_s=time.time() - t0,
            )

        matches = _match_rows(nombre, rows)

        # Construir details amigables para PDF/HTML
        details: list[dict[str, Any]] = []
        for m in matches:
            details.append({
                "name": m.get("name", ""),
                "country": m.get("countries", ""),
                "address": m.get("addresses", ""),
                "sanction_period": m.get("sanctions", ""),
                "id": m.get("id", ""),
            })

        if matches:
            # Top match para summary compacto
            top = matches[0]
            country = top.get("countries", "").upper() or "?"
            sanction = top.get("sanctions", "")
            summary = (f"REGISTRA en World Bank Debarred Firms "
                       f"({len(matches)} coincidencia(s), país={country}, "
                       f"sanción={sanction[:60]})")
            hit = Hit(
                source=self.name,
                matched=True,
                summary=summary,
                details=details,
                evidence_urls=[self.source_url, DATA_URL],
                elapsed_s=time.time() - t0,
            )
        else:
            summary = "SIN COINCIDENCIA en World Bank Debarred"
            hit = Hit(
                source=self.name,
                matched=False,
                summary=summary,
                details=[],
                evidence_urls=[self.source_url, DATA_URL],
                elapsed_s=time.time() - t0,
            )

        # Adjuntar metadata de cache al hit (no es parte del schema Hit
        # oficial pero ayuda a depurar / auditar)
        hit.notice = (hit.notice or "")
        cache_note = (f" [worldbank cache: {info.get('source')}, "
                      f"n={info.get('count')}, "
                      f"fetched={info.get('fetched_at')}]")
        if info.get("cache_error"):
            cache_note += f" [warning: {info.get('cache_error')}]"
        hit.notice = (hit.notice + cache_note) if hit.notice else cache_note.lstrip()
        return hit
