"""
credit_risk.py — Perfil crediticio del cliente y evaluación de riesgo.

Combina datos de:
  1. RSALES API (cartera, compras, frecuencia, gestión)
  2. Excel BITACORA (historial de crédito, datacrédito, mora)
  3. (Futuro) Datacrédito / centrales de riesgo externas

Define:
  - Perfil financiero completo del cliente
  - Score de riesgo crediticio (0-1000)
  - Criterios de aprobación/rechazo de crédito
  - Alertas y banderas de riesgo
  - Recomendación de monto máximo
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("verifydata.credit_risk")


# ============================================================
# Modelos de datos
# ============================================================

@dataclass
class CreditProfile:
    """Perfil crediticio completo de un cliente."""
    # Identificación
    nombre: str = ""
    cedula_nit: str = ""
    tipo_persona: str = "natural"  # natural | juridica

    # ── Fuente RSales ──
    rsales_disponible: bool = False
    rsales_client_code: str = ""
    rsales_cartera_total: float = 0.0
    rsales_cartera_vencida: float = 0.0
    rsales_cartera_corriente: float = 0.0
    rsales_pct_vencida: float = 0.0
    rsales_documentos_total: int = 0
    rsales_documentos_vencidos: int = 0
    rsales_dias_mora_max: int = 0
    rsales_compras_total: float = 0.0
    rsales_num_pedidos: int = 0
    rsales_promedio_pedido: float = 0.0
    rsales_ultima_compra_fecha: str = ""
    rsales_frecuencia_meses: float | None = None
    rsales_gestiones_12m: int = 0
    rsales_ciudad: str = ""
    rsales_estado: str = ""

    # ── Fuente Excel BITACORA ──
    excel_disponible: bool = False
    excel_credito_actual: float | None = None
    excel_monto_solicitado: float | None = None
    excel_cupo_inicial: float | None = None
    excel_credito_aprobado: float | None = None
    excel_promedio_compras: float | None = None
    excel_compra_minima: float | None = None
    excel_compra_maxima: float | None = None
    excel_numero_compras: int | None = None
    excel_ano_dato_compras: int | None = None
    excel_promedio_pago_dias: float | None = None
    excel_calificacion_datacredito: float | None = None
    excel_consultas_6m: str = ""
    excel_presenta_mora: bool | None = None
    excel_cartera_castigada: bool | None = None
    excel_aprobacion: bool | None = None
    excel_tipo_solicitud: str = ""
    excel_ejecutivo: str = ""
    excel_observaciones: str = ""

    # ── Cotejo RSales vs Excel ──
    cotejo_compras_ok: bool | None = None  # ¿Concuerdan las compras?
    cotejo_compras_diff_pct: float | None = None  # % de diferencia
    cotejo_nota: str = ""

    # ── Documentos subidos ──
    docs_cedula_frontal: bool = False
    docs_cedula_posterior: bool = False
    docs_rut: bool = False
    docs_camara_comercio: bool = False
    docs_estados_financieros: bool = False
    docs_declaracion_renta: bool = False

    # ── Score y recomendación ──
    score: int = 0  # 0-1000
    nivel_riesgo: str = "NO_EVALUADO"  # BAJO, MEDIO, ALTO, CRITICO
    recomendacion: str = ""
    monto_maximo_recomendado: float = 0.0
    alertas: list[str] = field(default_factory=list)
    factores_positivos: list[str] = field(default_factory=list)
    factores_negativos: list[str] = field(default_factory=list)

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# Pesos del scoring (suman 100%)
# ============================================================

WEIGHTS = {
    "historial_pago": 30,       # % cartera vencida, días de mora
    "capacidad_pago": 20,       # relación compras vs cartera, promedio pedido
    "comportamiento_compras": 20,  # frecuencia, volumen, consistencia
    "datacredito": 10,          # calificación datacrédito
    "mora_castigo": 15,         # presencia de mora o cartera castigada
    "documentacion": 5,         # completitud de documentos (RUT, Cámara, etc.)
}


# ============================================================
# Umbrales
# ============================================================

# Niveles de riesgo
RISK_LEVELS = {
    "BAJO": (700, 1000),
    "MEDIO": (500, 699),
    "ALTO": (300, 499),
    "CRITICO": (0, 299),
}

# Monto máximo como % de las compras anuales
CAPACIDAD_PAGO_RATIO = 0.30  # Max 30% de compras anuales


# ============================================================
# Evaluación de riesgo
# ============================================================

def build_credit_profile(
    cedula_nit: str,
    nombre: str = "",
    rsales_profile: dict[str, Any] | None = None,
    excel_data: dict[str, Any] | None = None,
    allow_rsales_fetch: bool = False,
    docs: dict[str, bool] | None = None,
) -> CreditProfile:
    """Construye un perfil crediticio completo combinando todas las fuentes.

    Args:
        cedula_nit: Cédula o NIT del cliente.
        nombre: Nombre del cliente (opcional).
        rsales_profile: Datos ya extraídos de RSales (si se tienen).
        excel_data: Datos ya extraídos del Excel BITACORA (si se tienen).
        allow_rsales_fetch: Si es True y no se pasó rsales_profile,
                           intenta obtenerlo de RSales automáticamente.
        docs: Flags de documentos subidos (cedula_frontal, cedula_posterior, etc.)

    Returns:
        CreditProfile con score, recomendación y alertas.
    """
    profile = CreditProfile(
        cedula_nit=cedula_nit,
        nombre=nombre,
    )

    # ── Aplicar flags de documentos ──
    if docs:
        profile.docs_cedula_frontal = docs.get("cedula_frontal", False)
        profile.docs_cedula_posterior = docs.get("cedula_posterior", False)
        profile.docs_rut = docs.get("rut", False)
        profile.docs_camara_comercio = docs.get("camara_comercio", False)
        profile.docs_estados_financieros = docs.get("estados_financieros", False)
        profile.docs_declaracion_renta = docs.get("declaracion_renta", False)

    # ── Cargar datos de RSales ──
    if rsales_profile is None and allow_rsales_fetch:
        try:
            from rsales_client import get_rsales_client
            client = get_rsales_client()
            # Intentar buscar el cliente en RSales por código
            # Primero necesitamos encontrar el client_code
            customers = client.get_all_customers()
            for c in customers:
                c_nit = (c.get("nit") or c.get("identification") or "")
                c_code = (c.get("code") or c.get("client_code") or "")
                if c_nit == cedula_nit or c_code == cedula_nit:
                    rsales_profile = client.get_customer_financial_profile(
                        c_code
                    )
                    break
        except Exception as e:
            log.warning("No se pudo obtener perfil RSales: %s", e)

    if rsales_profile and "error" not in rsales_profile:
        profile.rsales_disponible = True
        profile.rsales_client_code = rsales_profile.get("client_code", "")
        cartera = rsales_profile.get("cartera", {})
        profile.rsales_cartera_total = cartera.get("total", 0)
        profile.rsales_cartera_vencida = cartera.get("vencida", 0)
        profile.rsales_cartera_corriente = cartera.get("corriente", 0)
        profile.rsales_pct_vencida = cartera.get("pct_vencida", 0)
        profile.rsales_documentos_total = cartera.get("documentos_total", 0)
        profile.rsales_documentos_vencidos = cartera.get("documentos_vencidos", 0)
        profile.rsales_dias_mora_max = cartera.get("dias_mora_max", 0)
        compras = rsales_profile.get("compras", {})
        profile.rsales_compras_total = compras.get("total_historico", 0)
        profile.rsales_num_pedidos = compras.get("num_pedidos", 0)
        profile.rsales_promedio_pedido = compras.get("promedio_pedido", 0)
        profile.rsales_ultima_compra_fecha = compras.get(
            "ultimo_pedido_fecha", ""
        )
        profile.rsales_frecuencia_meses = compras.get("frecuencia_meses")
        gestion = rsales_profile.get("gestion", {})
        profile.rsales_gestiones_12m = gestion.get("visitas_12_meses", 0)
        profile.rsales_ciudad = rsales_profile.get("ciudad", "")
        profile.rsales_estado = rsales_profile.get("estado", "")
        profile.tipo_persona = (
            "juridica"
            if rsales_profile.get("es_persona_juridica")
            else "natural"
        )
        if rsales_profile.get("nombre"):
            profile.nombre = rsales_profile["nombre"]

    # ── Cargar datos del Excel ──
    if excel_data:
        profile.excel_disponible = True
        profile.excel_credito_actual = excel_data.get("credito_actual")
        profile.excel_monto_solicitado = excel_data.get("monto_solicitar")
        profile.excel_cupo_inicial = excel_data.get("cupo_inicial")
        profile.excel_credito_aprobado = excel_data.get("credito_aprobado") or \
            excel_data.get("monto_credito_aprobado")
        profile.excel_promedio_compras = excel_data.get("promedio_compras")
        profile.excel_compra_minima = excel_data.get("compra_minima")
        profile.excel_compra_maxima = excel_data.get("compra_maxima")
        profile.excel_numero_compras = excel_data.get("numero_compras")
        profile.excel_ano_dato_compras = excel_data.get("ano_dato_compras")
        profile.excel_promedio_pago_dias = excel_data.get("promedio_pago_dias")
        profile.excel_calificacion_datacredito = excel_data.get(
            "calificacion_datacredito"
        )
        profile.excel_consultas_6m = str(
            excel_data.get("consultas_6m_sector_real") or ""
        )
        profile.excel_presenta_mora = excel_data.get("presenta_mora")
        profile.excel_cartera_castigada = excel_data.get(
            "presenta_cartera_castigada"
        )
        profile.excel_aprobacion = excel_data.get("aprobacion")
        profile.excel_tipo_solicitud = str(
            excel_data.get("tipo_solicitud") or ""
        )
        profile.excel_ejecutivo = str(excel_data.get("ejecutivo") or "")
        profile.excel_observaciones = str(
            excel_data.get("observaciones") or ""
        )
        if excel_data.get("nombre_cliente"):
            profile.nombre = excel_data["nombre_cliente"]

    # ── Cotejo RSales vs Excel ──
    if profile.rsales_disponible and profile.excel_disponible:
        _compute_cross_reference(profile)

    # ── Calcular score ──
    _compute_score(profile)

    # ── Recomendación ──
    _compute_recommendation(profile)

    return profile


def _compute_cross_reference(profile: CreditProfile) -> None:
    """Compara datos de RSales con los del Excel."""
    alerts = []

    # Comparar compras: RSales total vs Excel promedio * número
    if (
        profile.excel_promedio_compras
        and profile.excel_numero_compras
        and profile.rsales_compras_total > 0
    ):
        excel_estimado = (
            profile.excel_promedio_compras * profile.excel_numero_compras
        )
        if excel_estimado > 0:
            diff_pct = abs(
                profile.rsales_compras_total - excel_estimado
            ) / excel_estimado * 100
            profile.cotejo_compras_diff_pct = round(diff_pct, 1)
            if diff_pct <= 20:
                profile.cotejo_compras_ok = True
                profile.cotejo_nota = (
                    f"Compras RSales (${profile.rsales_compras_total:,.0f}) "
                    f"vs Excel (${excel_estimado:,.0f}): "
                    f"concuerdan (±{diff_pct:.0f}%)"
                )
            else:
                profile.cotejo_compras_ok = False
                profile.cotejo_nota = (
                    f"DISCREPANCIA: Compras RSales (${profile.rsales_compras_total:,.0f}) "
                    f"vs Excel (${excel_estimado:,.0f}): "
                    f"diferencia de {diff_pct:.0f}%"
                )
                alerts.append(
                    f"Discrepancia del {diff_pct:.0f}% entre compras RSales y Excel"
                )

    # Comparar mora: RSales cartera vencida vs Excel presenta_mora
    if profile.rsales_pct_vencida > 10 and not profile.excel_presenta_mora:
        alerts.append(
            "RSales muestra cartera vencida >10% pero Excel no reporta mora"
        )
    if profile.excel_presenta_mora and profile.rsales_pct_vencida < 5:
        alerts.append(
            "Excel reporta mora pero RSales muestra cartera vencida baja"
        )

    if alerts:
        profile.alertas.extend(alerts)


# ============================================================
# Scoring
# ============================================================

def _compute_score(profile: CreditProfile) -> None:
    """Calcula el score crediticio (0-1000) basado en todas las fuentes."""
    scores: dict[str, float] = {}
    alertas: list[str] = []
    positivos: list[str] = []
    negativos: list[str] = []

    # ── Evaluar disponibilidad de datos ──
    sin_datos = not profile.rsales_disponible and not profile.excel_disponible
    datos_parciales = (profile.rsales_disponible and not profile.excel_disponible) or \
                      (not profile.rsales_disponible and profile.excel_disponible)
    if sin_datos:
        negativos.append("Sin datos financieros disponibles (sin RSales ni Excel)")
    elif datos_parciales:
        negativos.append("Datos parciales: solo una fuente disponible (RSales o Excel)")

    # ── 1. Historial de pago (30%) ──
    score_pago = 300  # base: perfecto

    if sin_datos:
        score_pago = 100
        negativos.append("Historial de pago no verificable")
    elif datos_parciales:
        score_pago = 200  # penalización por datos incompletos
    if profile.rsales_disponible:
        pct_vencida = profile.rsales_pct_vencida
        if pct_vencida > 50:
            score_pago -= 250
            negativos.append(f"Cartera vencida muy alta: {pct_vencida:.0f}%")
        elif pct_vencida > 30:
            score_pago -= 180
            negativos.append(f"Cartera vencida alta: {pct_vencida:.0f}%")
        elif pct_vencida > 15:
            score_pago -= 100
            negativos.append(f"Cartera vencida moderada: {pct_vencida:.0f}%")
        elif pct_vencida > 5:
            score_pago -= 40
        else:
            positivos.append("Cartera vencida baja o nula")

        # Días de mora máxima
        dias = profile.rsales_dias_mora_max
        if dias > 360:
            score_pago -= 150
            alertas.append(f"Mora superior a 360 días ({dias} días)")
        elif dias > 180:
            score_pago -= 100
            alertas.append(f"Mora superior a 180 días ({dias} días)")
        elif dias > 90:
            score_pago -= 60
            alertas.append(f"Mora superior a 90 días ({dias} días)")
        elif dias > 30:
            score_pago -= 25

    # Mora en Excel
    if profile.excel_disponible:
        if profile.excel_presenta_mora:
            score_pago -= 100
            negativos.append("Reporta mora en bitácora de crédito")

    scores["historial_pago"] = max(0, score_pago)

    # ── 2. Capacidad de pago (20%) ──
    score_capacidad = 200  # base

    if sin_datos:
        score_capacidad = 60
        negativos.append("Capacidad de pago no verificable")
    elif datos_parciales:
        score_capacidad = 120  # penalización por datos incompletos
    if profile.rsales_disponible and profile.rsales_compras_total > 0:
        # Relación cartera total / compras totales
        ratio = (
            profile.rsales_cartera_total / profile.rsales_compras_total
            if profile.rsales_compras_total > 0
            else 0
        )
        if ratio > 2.0:
            score_capacidad -= 120
            alertas.append(
                f"Endeudamiento alto: cartera {ratio:.1f}x las compras anuales"
            )
            negativos.append(f"Alto endeudamiento: {ratio:.1f}x compras")
        elif ratio > 1.0:
            score_capacidad -= 60
            negativos.append(f"Endeudamiento moderado: {ratio:.1f}x compras")
        elif ratio < 0.3:
            positivos.append("Bajo nivel de endeudamiento relativo")
            score_capacidad += 20

        # Promedio de compra
        if profile.rsales_promedio_pedido > 0:
            if profile.rsales_cartera_total > profile.rsales_promedio_pedido * 6:
                score_capacidad -= 40
    else:
        # Sin datos RSales, revisar Excel
        if profile.excel_disponible:
            compras_anuales = (
                (profile.excel_promedio_compras or 0)
                * (profile.excel_numero_compras or 0)
            )
            credito = profile.excel_credito_actual or 0
            if compras_anuales > 0 and credito > 0:
                ratio = credito / compras_anuales
                if ratio > 1.5:
                    score_capacidad -= 80
                    negativos.append("Alto endeudamiento vs compras")

    scores["capacidad_pago"] = max(0, min(220, score_capacidad))

    # ── 3. Comportamiento de compras (20%) ──
    score_compras = 200

    if sin_datos:
        score_compras = 50
        negativos.append("Comportamiento de compras no verificable")
    elif datos_parciales:
        score_compras = 120
    elif profile.rsales_disponible:
        # Frecuencia de compra
        freq = profile.rsales_frecuencia_meses
        if freq is not None:
            if freq <= 1:  # Compra cada mes o menos
                score_compras += 15
                positivos.append("Cliente recurrente: compra cada mes")
            elif freq <= 3:
                score_compras += 5
            elif freq > 6:
                score_compras -= 30
                negativos.append("Baja frecuencia de compra")

        # Número de pedidos
        if profile.rsales_num_pedidos >= 12:
            score_compras += 10
        elif profile.rsales_num_pedidos < 3:
            score_compras -= 20

        # Última compra: ¿compró en los últimos 3 meses?
        ultima = profile.rsales_ultima_compra_fecha
        if ultima:
            try:
                ultima_dt = datetime.fromisoformat(
                    ultima.replace("Z", "+00:00")
                )
                meses_sin_comprar = (
                    (datetime.now() - ultima_dt.replace(tzinfo=None)).days / 30
                )
                if meses_sin_comprar > 12:
                    score_compras -= 80
                    alertas.append(
                        f"Más de 12 meses sin comprar ({meses_sin_comprar:.0f} meses)"
                    )
                    negativos.append("Cliente inactivo: >12 meses sin comprar")
                elif meses_sin_comprar > 6:
                    score_compras -= 40
                    negativos.append(f"Inactivo: {meses_sin_comprar:.0f} meses sin comprar")
                elif meses_sin_comprar <= 1:
                    score_compras += 10
                    positivos.append("Cliente activo: compró en el último mes")
            except (ValueError, TypeError):
                pass

    # Datos del Excel
    if profile.excel_disponible:
        compras = profile.excel_numero_compras
        if compras:
            if compras >= 24:
                score_compras += 10
                positivos.append("Alto volumen de compras histórico")
            elif compras < 3:
                score_compras -= 15

    scores["comportamiento_compras"] = max(0, min(220, score_compras))

    # ── 4. Datacrédito (10%) ──
    score_datacredito = 100

    if profile.excel_disponible:
        calif = profile.excel_calificacion_datacredito
        if calif is not None:
            if calif >= 800:
                score_datacredito += 10
                positivos.append(f"Excelente calificación datacrédito: {calif:.0f}")
            elif calif >= 700:
                score_datacredito += 5
                positivos.append(f"Buena calificación datacrédito: {calif:.0f}")
            elif calif >= 500:
                score_datacredito -= 20
                negativos.append(f"Calificación datacrédito regular: {calif:.0f}")
            elif calif >= 300:
                score_datacredito -= 60
                negativos.append(f"Calificación datacrédito baja: {calif:.0f}")
            else:
                score_datacredito -= 100
                alertas.append(f"Calificación datacrédito muy baja: {calif:.0f}")
                negativos.append(f"Mala calificación datacrédito")
        else:
            score_datacredito -= 10  # Sin dato
    else:
        score_datacredito -= 10  # Sin información

    scores["datacredito"] = max(0, score_datacredito)

    # ── 5. Mora y castigos (15%) ──
    score_mora = 150

    if profile.excel_disponible:
        if profile.excel_cartera_castigada:
            score_mora -= 150
            alertas.append("CARTERA CASTIGADA: alto riesgo")
            negativos.append("Presenta cartera castigada")

    if profile.rsales_disponible:
        if profile.rsales_documentos_vencidos > 5:
            score_mora -= 80
            negativos.append(
                f"Múltiples documentos vencidos: {profile.rsales_documentos_vencidos}"
            )
        elif profile.rsales_documentos_vencidos > 2:
            score_mora -= 40

    scores["mora_castigo"] = max(0, score_mora)

    # ── 6. Documentación (5%) ──
    score_docs = 0
    max_docs = 5
    docs_uploaded = 0

    if profile.docs_cedula_frontal:
        docs_uploaded += 1
    if profile.docs_cedula_posterior:
        docs_uploaded += 1
    if profile.docs_rut:
        docs_uploaded += 1
    if profile.docs_camara_comercio:
        docs_uploaded += 1
    if profile.docs_estados_financieros:
        docs_uploaded += 1

    # 50 puntos base si subió al menos la cédula
    if docs_uploaded >= 1:
        score_docs = 30
    if docs_uploaded >= 3:
        score_docs = 40
    if docs_uploaded >= 5:
        score_docs = 50
        positivos.append("Documentación completa (5/5 docs)")
    elif docs_uploaded >= 3:
        positivos.append(f"Documentación parcial ({docs_uploaded}/5 docs)")
    elif docs_uploaded > 0:
        negativos.append(f"Documentación mínima ({docs_uploaded}/5 docs)")
    else:
        negativos.append("Sin documentos adjuntos")
        score_docs = 10  # mínimo por si hay datos de Excel/RSales

    scores["documentacion"] = score_docs

    # ── Calcular score final ──
    total = sum(scores.values())
    # Normalizar a 1000
    profile.score = min(1000, max(0, int(total)))

    # Determinar nivel de riesgo
    for nivel, (lo, hi) in RISK_LEVELS.items():
        if lo <= profile.score <= hi:
            profile.nivel_riesgo = nivel
            break

    # Asignar factores
    profile.factores_positivos = positivos
    profile.factores_negativos = negativos

    # Alertas críticas
    if profile.nivel_riesgo == "CRITICO":
        alertas.insert(0, "RIESGO CRÍTICO: No se recomienda otorgar crédito")
    elif profile.nivel_riesgo == "ALTO":
        alertas.insert(0, "RIESGO ALTO: Requiere análisis adicional")

    profile.alertas = alertas


# ============================================================
# Recomendación
# ============================================================

def _compute_recommendation(profile: CreditProfile) -> None:
    """Determina si aprobar/rechazar y el monto máximo recomendado."""
    if profile.score >= 700:
        profile.recomendacion = "APROBADO — Riesgo bajo"
    elif profile.score >= 500:
        profile.recomendacion = "APROBADO CON CONDICIONES — Riesgo medio"
    elif profile.score >= 300:
        profile.recomendacion = "REVISIÓN MANUAL REQUERIDA — Riesgo alto"
    else:
        profile.recomendacion = "RECHAZADO — Riesgo crítico"

    # Monto máximo recomendado
    ventas_anuales = 0.0
    if profile.rsales_disponible and profile.rsales_compras_total > 0:
        ventas_anuales = profile.rsales_compras_total
    elif profile.excel_disponible:
        ventas_anuales = (
            (profile.excel_promedio_compras or 0)
            * (profile.excel_numero_compras or 0)
        )

    if ventas_anuales > 0:
        base = ventas_anuales * CAPACIDAD_PAGO_RATIO

        # Ajustar por nivel de riesgo
        if profile.nivel_riesgo == "BAJO":
            multiplicador = 1.0
        elif profile.nivel_riesgo == "MEDIO":
            multiplicador = 0.6
        elif profile.nivel_riesgo == "ALTO":
            multiplicador = 0.3
        else:
            multiplicador = 0.0

        profile.monto_maximo_recomendado = round(base * multiplicador, -3)  # redondear a miles
    else:
        # Sin datos de ventas, usar el cupo de Excel si existe
        if profile.excel_cupo_inicial:
            profile.monto_maximo_recomendado = (
                profile.excel_cupo_inicial
                if profile.nivel_riesgo in ("BAJO", "MEDIO")
                else profile.excel_cupo_inicial * 0.5
            )


# ============================================================
# Búsqueda cross-reference (lote)
# ============================================================

def cross_reference_all_excel_with_rsales(
    excel_path: str | None = None,
) -> dict[str, Any]:
    """Coteja TODOS los clientes del Excel contra RSALES.

    Retorna un resumen de:
      - Cuántos clientes del Excel existen en RSALES
      - Cuántos NO existen
      - Para los que existen, comparación de datos
    """
    from excel_reader import read_all, EXCEL_PATH
    from rsales_client import get_rsales_client

    excel_path_final = excel_path or str(EXCEL_PATH)
    excel_data = read_all(Path(excel_path_final) if excel_path else None)

    try:
        client = get_rsales_client()
        rsales_customers = client.get_all_customers()
    except Exception as e:
        return {
            "error": f"No se pudo conectar a RSales: {e}",
            "total_excel": len(excel_data["clientes"]),
        }

    # Indexar RSales por NIT
    rsales_by_nit: dict[str, dict] = {}
    rsales_by_code: dict[str, dict] = {}
    for c in rsales_customers:
        nit = (c.get("nit") or c.get("identification") or "").strip()
        code = (c.get("code") or c.get("client_code") or "").strip()
        if nit:
            rsales_by_nit[nit] = c
        if code:
            rsales_by_code[code] = c

    matched: list[dict] = []
    not_found: list[dict] = []
    discrepancies: list[dict] = []

    for exc in excel_data["clientes"]:
        cedula = exc.get("cedula_nit", "")
        nombre = exc.get("nombre_cliente", "")

        # Buscar match por cédula/NIT
        rsales_match = (
            rsales_by_nit.get(cedula)
            or rsales_by_code.get(cedula)
        )

        if not rsales_match:
            # Buscar por nombre parcial
            nombre_norm = (nombre or "").strip().upper()
            for c in rsales_customers:
                cname = (c.get("name") or c.get("business_name") or "").strip().upper()
                if nombre_norm and nombre_norm[:15] in cname:
                    rsales_match = c
                    break

        if rsales_match:
            entry = {
                "cedula_nit": cedula,
                "nombre_excel": nombre,
                "nombre_rsales": rsales_match.get("name") or rsales_match.get("business_name", ""),
                "rsales_code": rsales_match.get("code") or rsales_match.get("client_code", ""),
                "ciudad_rsales": rsales_match.get("city", ""),
                "estado_rsales": rsales_match.get("state", ""),
            }
            matched.append(entry)

            # Verificar consistencia
            exc_compras = (
                (exc.get("promedio_compras") or 0)
                * (exc.get("numero_compras") or 0)
            )
            exc_mora = exc.get("presenta_mora")
            exc_aprob = exc.get("aprobacion")

            if exc_mora or exc.get("presenta_cartera_castigada"):
                discrepancies.append({
                    "cedula_nit": cedula,
                    "nombre": nombre,
                    "tipo": "riesgo_excel",
                    "detalle": (
                        f"Mora={exc_mora}, "
                        f"Castigada={exc.get('presenta_cartera_castigada')}, "
                        f"Aprobación={exc_aprob}"
                    ),
                })
        else:
            not_found.append({
                "cedula_nit": cedula,
                "nombre": nombre,
                "tipo_solicitud": exc.get("tipo_solicitud", ""),
            })

    total_excel = len(excel_data["clientes"])
    pct_matched = (
        round(len(matched) / total_excel * 100, 1) if total_excel > 0 else 0
    )

    return {
        "total_excel": total_excel,
        "total_rsales": len(rsales_customers),
        "encontrados_en_rsales": len(matched),
        "no_encontrados_en_rsales": len(not_found),
        "pct_cobertura": pct_matched,
        "discrepancias": len(discrepancies),
        "matched": matched,
        "not_found": not_found,
        "discrepancies_detail": discrepancies[:50],
        "timestamp": datetime.now().isoformat(),
    }
