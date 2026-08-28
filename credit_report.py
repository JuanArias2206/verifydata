"""
credit_report.py — Generador de PDF premium para reportes de crédito.

Genera un PDF profesional y impactante con toda la información del perfil
crediticio, diseñado como documento comercial vendible.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Circle, String, Line
from reportlab.graphics import renderPDF

ROOT = Path(__file__).parent

# Datos y estáticos (respeta Vercel /tmp)
try:
    from sources.base import get_data_path as _rep_get_data_path
    DATA_DIR = _rep_get_data_path()
except Exception:
    DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"

# ═══ PALETA DE COLORES CORPORATIVA ═══
COLORS = {
    'primary': '#4299e1',      # Azul principal
    'primary_dark': '#2b6cb0',
    'success': '#48bb78',      # Verde (aprobado)
    'success_dark': '#276749',
    'warning': '#ed8936',      # Naranja (medio)
    'warning_dark': '#c05621',
    'danger': '#f56565',       # Rojo (rechazado)
    'danger_dark': '#c53030',
    'purple': '#805ad5',       # Púrpura (antecedentes)
    'gray': '#718096',
    'gray_light': '#e2e8f0',
    'gray_dark': '#2d3748',
    'bg_light': '#f7fafc',
    'bg_blue': '#ebf8ff',
    'bg_purple': '#faf5ff',
    'bg_green': '#f0fff4',
    'bg_red': '#fff5f5',
    'white': '#ffffff',
    'black': '#1a1a2e',
}


def _hex(color_key: str) -> colors.Color:
    """Convierte color key a ReportLab Color."""
    return colors.HexColor(COLORS.get(color_key, '#000000'))


def _make_styles():
    """Crea todos los estilos personalizados del PDF."""
    base = getSampleStyleSheet()

    styles = {}

    # Títulos
    styles['title'] = ParagraphStyle(
        'PTitle', parent=base['Title'],
        fontSize=28, leading=32, spaceAfter=4,
        textColor=_hex('black'), fontName='Helvetica-Bold'
    )
    styles['subtitle'] = ParagraphStyle(
        'PSubtitle', parent=base['Normal'],
        fontSize=13, leading=16, spaceAfter=20,
        textColor=_hex('gray')
    )
    styles['section'] = ParagraphStyle(
        'PSection', parent=base['Heading2'],
        fontSize=15, leading=18, spaceBefore=18, spaceAfter=10,
        textColor=_hex('gray_dark'), fontName='Helvetica-Bold',
        borderWidth=0, borderPadding=0
    )
    styles['subsection'] = ParagraphStyle(
        'PSubsection', parent=base['Heading3'],
        fontSize=12, leading=15, spaceBefore=12, spaceAfter=6,
        textColor=_hex('primary_dark'), fontName='Helvetica-Bold'
    )

    # Cuerpo
    styles['body'] = ParagraphStyle(
        'PBody', parent=base['Normal'],
        fontSize=10, leading=14, spaceAfter=6,
        textColor=_hex('gray_dark')
    )
    styles['small'] = ParagraphStyle(
        'PSmall', parent=base['Normal'],
        fontSize=8, leading=11, textColor=_hex('gray')
    )
    styles['tiny'] = ParagraphStyle(
        'PTiny', parent=base['Normal'],
        fontSize=7, leading=9, textColor=_hex('gray')
    )

    # Especiales
    styles['kpi_value'] = ParagraphStyle(
        'KPIValue', parent=base['Normal'],
        fontSize=28, leading=32, alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    styles['kpi_label'] = ParagraphStyle(
        'KPILabel', parent=base['Normal'],
        fontSize=8, leading=10, alignment=TA_CENTER,
        textColor=_hex('gray'), spaceAfter=4
    )
    styles['badge'] = ParagraphStyle(
        'Badge', parent=base['Normal'],
        fontSize=16, leading=20, alignment=TA_CENTER,
        fontName='Helvetica-Bold', textColor=_hex('white')
    )
    styles['center'] = ParagraphStyle(
        'Center', parent=base['Normal'],
        fontSize=10, leading=14, alignment=TA_CENTER
    )
    styles['right'] = ParagraphStyle(
        'Right', parent=base['Normal'],
        fontSize=10, leading=14, alignment=TA_RIGHT
    )

    return styles


def _make_table(data, col_widths, header_color='primary', style_overrides=None):
    """Crea una tabla con estilo estándar."""
    t = Table(data, colWidths=col_widths)
    base_style = [
        ('BACKGROUND', (0, 0), (-1, 0), _hex(header_color)),
        ('TEXTCOLOR', (0, 0), (-1, 0), _hex('white')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, _hex('gray_light')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [_hex('white'), _hex('bg_light')]),
    ]
    if style_overrides:
        base_style.extend(style_overrides)
    t.setStyle(TableStyle(base_style))
    return t


def generate_credit_pdf(result: dict, output_path: str | None = None) -> bytes:
    """Genera un PDF premium del perfil crediticio completo."""
    import io

    if output_path is None:
        # Generar en memoria
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=letter,
            rightMargin=50, leftMargin=50, topMargin=60, bottomMargin=60,
        )
    else:
        doc = SimpleDocTemplate(
            output_path, pagesize=letter,
            rightMargin=50, leftMargin=50, topMargin=60, bottomMargin=60,
        )

    S = _make_styles()
    story = []

    p = result.get('perfil_crediticio', {})
    res = result.get('resumen_ejecutivo', {})
    ant = result.get('antecedentes', {})
    rsales = p.get('rsales')

    nivel = p.get('nivel_riesgo', 'NO_EVALUADO')
    score = p.get('score', 0)
    aprobado = res.get('aprobado', False)
    monto = res.get('monto_maximo', 0)
    nivel_color = {'BAJO': 'success', 'MEDIO': 'warning', 'ALTO': 'danger', 'CRITICO': 'danger'}.get(nivel, 'gray')

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 1: PORTADA
    # ═══════════════════════════════════════════════════════════════
    story.append(Spacer(1, 30))

    # Header VerifyData
    header_data = [['VerifyData', 'INFORME DE ANÁLISIS DE RIESGO CREDITICIO']]
    header_table = Table(header_data, colWidths=[2*inch, 4.5*inch])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), _hex('primary')),
        ('TEXTCOLOR', (0, 0), (0, 0), _hex('white')),
        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (0, 0), 16),
        ('BACKGROUND', (1, 0), (1, 0), _hex('gray_dark')),
        ('TEXTCOLOR', (1, 0), (1, 0), _hex('white')),
        ('FONTNAME', (1, 0), (1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (1, 0), (1, 0), 14),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 16),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 16),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 30))

    # Badge de decisión
    badge_bg = _hex('success') if aprobado else _hex('danger')
    badge_text = 'APROBADO' if aprobado else 'RECHAZADO'
    badge_icon = '✓' if aprobado else '✗'

    badge_data = [[f'{badge_icon}  {badge_text}  —  Riesgo {nivel}']]
    badge_table = Table(badge_data, colWidths=[6.5*inch])
    badge_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), badge_bg),
        ('TEXTCOLOR', (0, 0), (-1, -1), _hex('white')),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 18),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('ROUNDEDCORNERS', [8, 8, 8, 8]),
    ]))
    story.append(badge_table)
    story.append(Spacer(1, 24))

    # Datos del cliente + Score lado a lado
    client_data = [
        ['DATOS DEL CLIENTE', ''],
        ['Nombre:', result.get('nombre', '')],
        ['CC/NIT:', result.get('cedula_nit', '')],
        ['Fecha de solicitud:', result.get('fecha_solicitud', 'N/A')],
        ['Tipo de solicitud:', result.get('tipo_solicitud', 'N/A')],
        ['Ingreso mensual:', f'${(result.get("ingreso_mensual") or 0):,.0f}'],
        ['Fuente de ingreso:', result.get('fuente_ingreso', 'N/A')],
    ]
    client_table = _make_table(client_data, [2.2*inch, 4.3*inch], 'gray_dark')
    story.append(client_table)
    story.append(Spacer(1, 16))

    # KPIs principales
    ant_count = sum(1 for v in ant.values() if not v.get('matched') and not v.get('error'))
    kpi_data = [[
        Paragraph(f'<font color="{COLORS[nivel_color]}">{score}</font>', S['kpi_value']),
        Paragraph(f'<font color="{COLORS[nivel_color]}">{nivel}</font>', S['kpi_value']),
        Paragraph(f'<font color="{COLORS["purple"]}">${monto:,.0f}</font>', S['kpi_value']),
        Paragraph(f'<font color="{COLORS["primary"]}">{ant_count}/{len(ant)}</font>', S['kpi_value']),
    ], [
        Paragraph('Score / 1000', S['kpi_label']),
        Paragraph('Nivel de Riesgo', S['kpi_label']),
        Paragraph('Monto Máximo', S['kpi_label']),
        Paragraph('Listas Limpias', S['kpi_label']),
    ]]
    kpi_table = Table(kpi_data, colWidths=[1.625*inch]*4)
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), _hex('bg_light')),
        ('GRID', (0, 0), (-1, -1), 0.5, _hex('gray_light')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, 0), 16),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('TOPPADDING', (0, 1), (-1, 1), 0),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 12),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(kpi_table)

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 2: RESUMEN EJECUTIVO + JUSTIFICACIÓN
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('RESUMEN EJECUTIVO', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))

    # Decisión y fórmula
    decision_data = [
        ['INDICADOR', 'VALOR', 'OBSERVACIÓN'],
        ['Score Crediticio', f'{score} / 1000', f'Nivel: {nivel}'],
        ['Decisión', badge_text, p.get('recomendacion', '')],
        ['Monto Máximo', f'${monto:,.0f}', 'Ver justificación abajo'],
        ['Tasa de endeudamiento', f'{((result.get("endeudamiento") or 0)/max((result.get("ingreso_mensual") or 1),1)*100):.1f}%', ' Endeudamiento / Ingreso mensual'],
        ['Capacidad de pago', f'{((result.get("ingreso_mensual") or 0)*0.3):,.0f}', '30% del ingreso mensual'],
    ]
    story.append(_make_table(decision_data, [2*inch, 1.5*inch, 3*inch], 'gray_dark'))
    story.append(Spacer(1, 16))

    # Justificación del monto
    justificacion = res.get('monto_justificacion', '')
    if justificacion:
        story.append(Paragraph('JUSTIFICACIÓN DEL MONTO MÁXIMO', S['subsection']))

        # Desglose visual
        ventas = (result.get('promedio_compras') or 0) * (result.get('numero_compras') or 0)
        capacidad = ventas * 0.30
        mult = {'BAJO': 1.0, 'MEDIO': 0.6, 'ALTO': 0.3, 'CRITICO': 0}.get(nivel, 0)

        formula_data = [
            ['PASO', 'CONCEPTO', 'VALOR'],
            ['1', 'Ventas anuales estimadas', f'${ventas:,.0f}'],
            ['2', 'Capacidad de pago (30%)', f'${capacidad:,.0f}'],
            ['3', f'Multiplicador por riesgo {nivel}', f'×{mult}'],
            ['4', 'Monto máximo recomendado', f'${monto:,.0f}'],
        ]
        story.append(_make_table(formula_data, [0.6*inch, 3*inch, 2.9*inch], 'purple'))
        story.append(Spacer(1, 12))

    # Resumen de antecedentes
    story.append(Paragraph('RESUMEN DE VERIFICACIONES', S['subsection']))
    clean = sum(1 for v in ant.values() if not v.get('matched') and not v.get('error'))
    found = sum(1 for v in ant.values() if v.get('matched'))
    errors = sum(1 for v in ant.values() if v.get('error'))

    resumen_data = [
        ['CATEGORÍA', 'CANTIDAD', 'ESTADO'],
        ['Listas verificadas', str(len(ant)), ''],
        ['Sin coincidencias', str(clean), '✓ LIMPIO'],
        ['Con coincidencias', str(found), '✗ ENCONTRADO' if found else '—'],
        ['Con errores', str(errors), '⚠ ERROR' if errors else '—'],
    ]
    story.append(_make_table(resumen_data, [2.5*inch, 1.5*inch, 2.5*inch], 'primary'))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 3: PERFIL FINANCIERO
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('PERFIL FINANCIERO', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))

    # Ingresos y capacidad
    story.append(Paragraph('Ingresos y Capacidad de Pago', S['subsection']))
    ing_data = [
        ['CONCEPTO', 'VALOR', 'OBSERVACIÓN'],
        ['Ingreso mensual', f'${(result.get("ingreso_mensual") or 0):,.0f}', result.get('fuente_ingreso', '')],
        ['Ingreso anual estimado', f'${(result.get("ingreso_mensual") or 0)*12:,.0f}', 'Ingreso × 12'],
        ['Capacidad de pago (30%)', f'${(result.get("ingreso_mensual") or 0)*0.3:,.0f}', 'Máximo recomendado'],
        ['Endeudamiento total', f'${(result.get("endeudamiento") or 0):,.0f}', ''],
        ['Patrimonio estimado', f'${(result.get("patrimonio") or 0):,.0f}', ''],
    ]
    story.append(_make_table(ing_data, [2.2*inch, 2*inch, 2.3*inch], 'primary'))
    story.append(Spacer(1, 12))

    # Ratios financieros
    story.append(Paragraph('Ratios Financieros', S['subsection']))
    ing_mensual = max((result.get('ingreso_mensual') or 1), 1)
    endeudamiento = result.get('endeudamiento') or 0
    patrimonio = result.get('patrimonio') or 1

    ratio_endeud = (endeudamiento / ing_mensual) if ing_mensual > 0 else 0
    ratio_compras = ((result.get('promedio_compras') or 0) / max((result.get('credito_actual') or 1), 1))
    ratio_capacidad = ((result.get('ingreso_mensual') or 0) * 0.3 / max((result.get('credito_actual') or 1), 1))

    ratio_data = [
        ['RATIO', 'VALOR', 'REFERENCIA', 'ESTADO'],
        ['Endeudamiento / Ingreso', f'{ratio_endeud:.1f}x', '< 3.0x', '✓' if ratio_endeud < 3 else '✗'],
        ['Compras / Crédito actual', f'{ratio_compras:.1f}x', '< 2.0x', '✓' if ratio_compras < 2 else '✗'],
        ['Capacidad / Crédito', f'{ratio_capacidad:.1f}x', '> 0.5x', '✓' if ratio_capacidad > 0.5 else '✗'],
        ['Patrimonio / Endeudamiento', f'{patrimonio/max(endeudamiento,1):.1f}x', '> 1.5x', '✓' if patrimonio/max(endeudamiento,1) > 1.5 else '✗'],
    ]
    story.append(_make_table(ratio_data, [2.2*inch, 1.2*inch, 1.5*inch, 1.6*inch], 'warning'))
    story.append(Spacer(1, 12))

    # Historial de compras
    story.append(Paragraph('Historial de Compras', S['subsection']))
    compra_data = [
        ['CONCEPTO', 'VALOR'],
        ['Promedio compras', f'${(result.get("promedio_compras") or 0):,.0f}'],
        ['Compra mínima', f'${(result.get("compra_minima") or 0):,.0f}'],
        ['Compra máxima', f'${(result.get("compra_maxima") or 0):,.0f}'],
        ['Número de compras', str(result.get('numero_compras') or 0)],
        ['Año del dato', str(result.get('ano_dato_compras') or 2026)],
        ['Promedio pago (días)', f'{(result.get("promedio_pago_dias") or 0)} días'],
        ['Crédito actual', f'${(result.get("credito_actual") or 0):,.0f}'],
        ['Monto solicitado', f'${(result.get("monto_solicitar") or 0):,.0f}'],
        ['Cupo inicial', f'${(result.get("cupo_inicial") or 0):,.0f}'],
    ]
    story.append(_make_table(compra_data, [3*inch, 3.5*inch], 'success'))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 4: HISTORIAL RSALES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('HISTORIAL COMERCIAL — RSALES', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))

    if rsales:
        # Cartera
        story.append(Paragraph('Posición de Cartera', S['subsection']))
        cartera_total = rsales.get("cartera_total", 0)
        cartera_vencida = rsales.get("cartera_vencida", 0)
        cartera_corriente = rsales.get("cartera_corriente", 0)
        pct_vencida = rsales.get("pct_vencida", 0)

        cartera_data = [
            ['MÉTRICA', 'VALOR', 'Detalle'],
            ['Cartera Total', f'${cartera_total:,.0f}', ''],
            ['Cartera Vencida', f'${cartera_vencida:,.0f}', f'{pct_vencida:.1f}% del total'],
            ['Cartera Corriente', f'${cartera_corriente:,.0f}', f'{100-pct_vencida:.1f}% del total'],
            ['Días Mora Máxima', f'{rsales.get("dias_mora_max", 0)} días', ''],
            ['Documentos Vencidos', str(rsales.get("documentos_vencidos", 0)), ''],
        ]
        story.append(_make_table(cartera_data, [2*inch, 2*inch, 2.5*inch], 'primary'))
        story.append(Spacer(1, 8))

        # Indicador visual de mora
        if pct_vencida > 0:
            mora_color = 'danger' if pct_vencida > 30 else ('warning' if pct_vencida > 15 else 'success')
            mora_label = 'ALTO RIESGO' if pct_vencida > 30 else ('RIESGO MODERADO' if pct_vencida > 15 else 'BAJO RIESGO')
            mora_data = [[f'INDICADOR DE MORA: {pct_vencida:.1f}% cartera vencida — {mora_label}']]
            mora_table = Table(mora_data, colWidths=[6.5*inch])
            mora_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), _hex(mora_color + '_dark')),
                ('TEXTCOLOR', (0, 0), (-1, -1), _hex('white')),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 11),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            story.append(mora_table)
            story.append(Spacer(1, 12))

        # Compras
        story.append(Paragraph('Historial de Compras RSALES', S['subsection']))
        compras_data = [
            ['MÉTRICA', 'VALOR'],
            ['Compras Totales', f'${rsales.get("compras_total", 0):,.0f}'],
            ['Número de Pedidos', str(rsales.get("num_pedidos", 0))],
            ['Promedio Pedido', f'${rsales.get("promedio_pedido", 0):,.0f}'],
            ['Última Compra', rsales.get("ultima_compra_fecha", "N/A") or "N/A"],
            ['Visitas 12 meses', str(rsales.get("visitas_12m", 0))],
        ]
        story.append(_make_table(compras_data, [3*inch, 3.5*inch], 'success'))
    else:
        story.append(Paragraph(
            '<i>Cliente no encontrado en RSALES — sin historial comercial disponible. '
            'Esto puede indicar que es un cliente nuevo sin operaciones previas.</i>',
            S['body']
        ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 5: ANTECEDENTES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('ANTECEDENTES Y LISTAS RESTRICTIVAS', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))

    ant_data = [['FUENTE', 'ESTADO', 'DETALLE', 'TIEMPO']]
    for k, v in ant.items():
        if v.get('matched'):
            estado = '✗ ENCONTRADO'
            color = 'danger'
        elif v.get('error'):
            estado = '⚠ ERROR'
            color = 'warning'
        else:
            estado = '✓ LIMPIO'
            color = 'success'

        detalle = (v.get('summary', '') or '')[:60]
        tiempo = f'{v.get("elapsed_s", 0):.1f}s'
        ant_data.append([k, estado, detalle, tiempo])

    ant_table = Table(ant_data, colWidths=[1.6*inch, 1.3*inch, 2.8*inch, 0.8*inch])
    ant_style = [
        ('BACKGROUND', (0, 0), (-1, 0), _hex('purple')),
        ('TEXTCOLOR', (0, 0), (-1, 0), _hex('white')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, _hex('gray_light')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [_hex('white'), _hex('bg_purple')]),
    ]
    ant_table.setStyle(TableStyle(ant_style))
    story.append(ant_table)
    story.append(Spacer(1, 12))

    # Bloqueantes
    bloqueantes = res.get('bloqueantes', [])
    if bloqueantes:
        story.append(Paragraph('BLOQUEANTES ENCONTRADOS', S['subsection']))
        for b in bloqueantes:
            story.append(Paragraph(
                f'<font color="{COLORS["danger"]}">✗ {b}</font>',
                S['body']
            ))
        story.append(Spacer(1, 8))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 6: DOCUMENTACIÓN + FACTORES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('DOCUMENTACIÓN Y ANÁLISIS DE RIESGO', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))

    # Documentos
    docs = p.get('docs', {})
    doc_labels = {
        'cedula_frontal': ('Cédula de Ciudadanía — Frontal', 'Documento de identidad principal del cliente'),
        'cedula_posterior': ('Cédula de Ciudadanía — Posterior', 'Respaldo de documento de identidad'),
        'rut': ('Registro Único Tributario (RUT)', 'Identificación tributaria ante DIAN'),
        'camara_comercio': ('Cámara de Comercio', 'Certificado de existencia y representación legal'),
        'estados_financieros': ('Estados Financieros', 'Balance general y estado de resultados'),
        'declaracion_renta': ('Declaración de Renta', 'Declaración del impuesto sobre la renta'),
    }
    docs_count = sum(1 for dk, _ in doc_labels.items() if docs.get(dk))

    story.append(Paragraph(f'Checklist de Documentación ({docs_count}/{len(doc_labels)} adjuntos)', S['subsection']))

    # Tabla detallada de documentos
    doc_data = [['DOCUMENTO', 'DESCRIPCIÓN', 'ESTADO']]
    for dk, (dl, desc) in doc_labels.items():
        ok = docs.get(dk, False)
        icon = '✓ ADJUNTADO' if ok else '✗ PENDIENTE'
        doc_data.append([dl, desc, icon])

    doc_table = _make_table(doc_data, [2.2*inch, 2.8*inch, 1.5*inch], 'success')
    # Colorear filas pendientes
    for i, (dk, _) in enumerate(doc_labels.items()):
        if not docs.get(dk):
            row_idx = i + 1  # +1 for header
            doc_table.setStyle(TableStyle([
                ('TEXTCOLOR', (2, row_idx), (2, row_idx), _hex('danger')),
                ('FONTNAME', (2, row_idx), (2, row_idx), 'Helvetica-Bold'),
            ]))
    story.append(doc_table)
    story.append(Spacer(1, 8))

    # Nota sobre documentos
    if docs_count < len(doc_labels):
        missing = [dl for dk, (dl, _) in doc_labels.items() if not docs.get(dk)]
        story.append(Paragraph(
            f'<font color="{COLORS["warning"]}"><b>Documentos pendientes:</b> '
            f'{", ".join(missing)}. La documentación completa mejora el score '
            f'crediticio y facilita la aprobación.</font>',
            S['small']
        ))
    story.append(Spacer(1, 12))

    # ── Anexos reales (archivos subidos) — incrustar imágenes / listar PDFs ──
    anexos = result.get("anexos", []) or []
    if anexos:
        story.append(Paragraph(f'ANEXOS ADJUNTOS ({len(anexos)} archivo(s) cargado(s))', S['subsection']))
        for a in anexos:
            fname = a.get("original_name") or a.get("saved_name") or "archivo"
            rel = a.get("relative_path") or ""
            fpath = None
            try:
                if rel:
                    cand = DATA_DIR / rel
                    if cand.exists():
                        fpath = cand
                    else:
                        cand2 = Path(a.get("saved_path", ""))
                        if cand2.exists():
                            fpath = cand2
                else:
                    cand2 = Path(a.get("saved_path", ""))
                    if cand2.exists():
                        fpath = cand2
            except Exception:
                fpath = None
            ext = Path(fname).suffix.lower()
            is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
            # Fila info
            story.append(Paragraph(
                f'<b>{fname}</b> &nbsp;<font color="{COLORS["gray"]}">({int(a.get("size",0))/1024:.1f} KB'
                f'{" — "+a.get("mimetype","") if a.get("mimetype") else ""})</font>',
                S['small']))
            if is_image:
                # Intentar fuente física primero, luego b64 (persistencia Vercel)
                img_source = None
                tmp_to_clean = None
                if fpath and fpath.exists():
                    img_source = str(fpath)
                elif a.get("b64"):
                    try:
                        import base64 as _b64
                        import tempfile as _tf
                        raw = _b64.b64decode(a["b64"])
                        suffix = ext or ".jpg"
                        tf = _tf.NamedTemporaryFile(delete=False, suffix=suffix)
                        tf.write(raw)
                        tf.close()
                        img_source = tf.name
                        tmp_to_clean = tf.name
                    except Exception as e:
                        story.append(Paragraph(f'<font color="{COLORS["warning"]}">No se pudo decodificar imagen b64: {e}</font>', S['small']))
                        story.append(Spacer(1, 6))
                        img_source = None
                if img_source:
                    try:
                        # Escalar imagen para que quepa en página sin desbordar
                        img = Image(img_source)
                        max_w = 5.5 * inch
                        max_h = 3.2 * inch
                        iw, ih = img.imageWidth, img.imageHeight
                        scale = min(max_w / iw if iw else 1, max_h / ih if ih else 1, 1)
                        img.drawWidth = iw * scale
                        img.drawHeight = ih * scale
                        img.hAlign = 'CENTER'
                        story.append(Spacer(1, 4))
                        story.append(img)
                        story.append(Spacer(1, 8))
                        # Limpiar temp si se creó
                        if tmp_to_clean:
                            try:
                                import os as _os2
                                _os2.unlink(tmp_to_clean)
                            except Exception:
                                pass
                    except Exception as e:
                        story.append(Paragraph(f'<font color="{COLORS["warning"]}">No se pudo incrustar imagen: {e}</font>', S['small']))
                        story.append(Spacer(1, 6))
                        if tmp_to_clean:
                            try:
                                import os as _os3
                                _os3.unlink(tmp_to_clean)
                            except Exception:
                                pass
                elif not fpath:
                    # Sin archivo y sin b64
                    story.append(Paragraph(f'<font color="{COLORS["warning"]}">Imagen no disponible en este entorno (archivo efímero no persistido).</font>', S['small']))
                    story.append(Spacer(1, 6))
            elif ext == ".pdf":
                # Renderizar PDF anexo: fitz → imagen embebida; fallback pypdf → texto
                pdf_bytes_for_render = None
                if fpath and fpath.exists():
                    try:
                        pdf_bytes_for_render = fpath.read_bytes()
                    except Exception:
                        pdf_bytes_for_render = None
                if pdf_bytes_for_render is None and a.get("b64"):
                    try:
                        import base64 as _b64pdf
                        pdf_bytes_for_render = _b64pdf.b64decode(a["b64"])
                    except Exception:
                        pdf_bytes_for_render = None
                if pdf_bytes_for_render:
                    _size_kb = a.get("size", 0) / 1024
                    # 1) fitz (pymupdf) → imagen embebida
                    _fitz_ok = False
                    try:
                        import fitz as _fitz, tempfile as _tf_fitz, os as _os_fitz
                        import sys as _dbg_fitz
                        print(f"DEBUG FITZ START: {fname}, {len(pdf_bytes_for_render)} bytes", file=_dbg_fitz.stderr, flush=True)
                        _fitz_doc = _fitz.open(stream=pdf_bytes_for_render, filetype="pdf")
                        _total_pages = len(_fitz_doc)
                        print(f"DEBUG FITZ DOC: {_total_pages} pages", file=_dbg_fitz.stderr, flush=True)
                        story.append(Paragraph(
                            f'<font color="{COLORS["primary_dark"]}" size="9"><b>📄 {fname}</b> '
                            f'({_size_kb:.1f} KB — {_total_pages} página(s))</font>',
                            S['small']))
                        story.append(Spacer(1, 4))
                        for _pi in range(min(_total_pages, 3)):
                            import sys as _dbg_fitz2
                            print(f"DEBUG FITZ LOOP: page {_pi+1}/{_total_pages}", file=_dbg_fitz2.stderr, flush=True)
                            _page = _fitz_doc[_pi]
                            _pix = _page.get_pixmap(dpi=130)
                            print(f"DEBUG FITZ PIX: {_pix.width}x{_pix.height}", file=_dbg_fitz2.stderr, flush=True)
                            _tf_img = _tf_fitz.NamedTemporaryFile(delete=False, suffix=".png")
                            _pix.save(_tf_img.name)
                            _tf_img.close()
                            try:
                                import sys as _dbg_fitz3
                                _img = Image(_tf_img.name)
                                _max_w, _max_h = 5.5*inch, 3.6*inch
                                _iw, _ih = _img.imageWidth, _img.imageHeight
                                _scale = min(_max_w/_iw if _iw else 1, _max_h/_ih if _ih else 1, 1)
                                _img.drawWidth, _img.drawHeight = _iw*_scale, _ih*_scale
                                _img.hAlign = 'CENTER'
                                print(f"DEBUG FITZ IMG: {_iw}x{_ih} -> {_img.drawWidth}x{_img.drawHeight}", file=_dbg_fitz3.stderr, flush=True)
                                story.append(Paragraph(
                                    f'<font color="{COLORS["gray"]}" size="8">Página {_pi+1}/{_total_pages} — {fname}</font>',
                                    S['tiny']))
                                story.append(Spacer(1, 4))
                                story.append(_img)
                                story.append(Spacer(1, 8))
                                _fitz_ok = True
                                print(f"DEBUG FITZ APPENDED: story now has {len(story)} elements", file=_dbg_fitz3.stderr, flush=True)
                            except Exception as e:
                                import sys as _dbg_fitz4
                                print(f"DEBUG FITZ IMAGE FAIL: {e}", file=_dbg_fitz4.stderr, flush=True)
                            finally:
                                try: _os_fitz.unlink(_tf_img.name)
                                except Exception: pass
                        _fitz_doc.close()
                    except Exception as e:
                        import sys as _dbg4
                        print(f"DEBUG FITZ FAIL: {fname} error={e}", file=_dbg4.stderr, flush=True)
                    import sys as _dbg5
                    print(f"DEBUG _fitz_ok={_fitz_ok} for {fname}", file=_dbg5.stderr, flush=True)
                    # 2) fallback: pypdf → texto
                    if not _fitz_ok:
                        try:
                            import io as _io_pdf
                            from pypdf import PdfReader as _PdfReader
                            _pr = _PdfReader(_io_pdf.BytesIO(pdf_bytes_for_render))
                            _total_pages = len(_pr.pages)
                            story.append(Paragraph(
                                f'<font color="{COLORS["primary_dark"]}" size="9"><b>📄 {fname}</b> '
                                f'({_size_kb:.1f} KB — {_total_pages} página(s))</font>',
                                S['small']))
                            story.append(Spacer(1, 4))
                            for _pi in range(min(_total_pages, 3)):
                                _text = (_pr.pages[_pi].extract_text() or "").strip()
                                story.append(Paragraph(
                                    f'<font color="{COLORS["gray"]}" size="8">— Página {_pi+1}/{_total_pages} de {fname} —</font>',
                                    S['tiny']))
                                story.append(Spacer(1, 2))
                                if _text:
                                    story.append(Paragraph(
                                        f'<font color="{COLORS["gray_dark"]}" size="8">{_text[:500].replace(chr(10),"<br>")}</font>',
                                        S['small']))
                                else:
                                    story.append(Paragraph(
                                        f'<font color="{COLORS["gray"]}"><i>Página {(_pi+1)} — contenido escaneado sin texto extraíble</i></font>',
                                        S['small']))
                                story.append(Spacer(1, 6))
                            if _total_pages > 3:
                                story.append(Paragraph(
                                    f'<font color="{COLORS["gray"]}" size="7">… {_total_pages-3} página(s) adicional(es) en el PDF original.</font>',
                                    S['tiny']))
                        except Exception:
                            story.append(Paragraph(
                                f'<font color="{COLORS["gray"]}">📄 {fname} — PDF adjunto ({_size_kb:.1f} KB). '
                                f'El archivo original se adjunta en el correo.</font>',
                                S['small']))
                            story.append(Spacer(1, 6))
                    story.append(Paragraph(
                        f'<font color="{COLORS["gray"]}" size="7">📎 El PDF original completo se adjunta como archivo en el correo.</font>',
                        S['tiny']))
                    story.append(Spacer(1, 6))
            else:
                # Otros (xlsx, etc): solo listar
                story.append(Paragraph(
                    f'<font color="{COLORS["gray"]}">Archivo adjunto: {rel or fname}</font>', S['small']))
                story.append(Spacer(1, 6))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 16))

    # Factores
    story.append(Paragraph('Análisis de Factores de Riesgo', S['subsection']))

    positivos = p.get('factores_positivos', [])
    negativos = p.get('factores_negativos', [])
    alertas = p.get('alertas', [])

    if positivos:
        story.append(Paragraph(f'<font color="{COLORS["success_dark"]}"><b>Factores Positivos ({len(positivos)})</b></font>', S['body']))
        for f in positivos:
            story.append(Paragraph(f'<font color="{COLORS["success"]}">✓</font> {f}', S['body']))

    if negativos:
        story.append(Paragraph(f'<font color="{COLORS["danger_dark"]}"><b>Factores Negativos ({len(negativos)})</b></font>', S['body']))
        for f in negativos:
            story.append(Paragraph(f'<font color="{COLORS["danger"]}">✗</font> {f}', S['body']))

    if alertas:
        story.append(Paragraph(f'<font color="{COLORS["warning_dark"]}"><b>Alertas ({len(alertas)})</b></font>', S['body']))
        for a in alertas:
            story.append(Paragraph(f'<font color="{COLORS["warning"]}">⚠</font> {a}', S['body']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════
    #  PÁGINA 7: RECOMENDACIÓN FINAL
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph('RECOMENDACIÓN FINAL', S['section']))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 16))

    # Recomendación grande
    rec_bg = _hex('success') if aprobado else _hex('danger')
    rec_data = [[Paragraph(
        f'<font color="{COLORS["white"]}" size="16"><b>{p.get("recomendacion", "")}</b></font>',
        S['center']
    )]]
    rec_table = Table(rec_data, colWidths=[6.5*inch])
    rec_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), rec_bg),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 20),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
        ('ROUNDEDCORNERS', [8, 8, 8, 8]),
    ]))
    story.append(rec_table)
    story.append(Spacer(1, 20))

    # Condiciones (si aplica)
    if aprobado and nivel == 'MEDIO':
        story.append(Paragraph('CONDICIONES DE APROBACIÓN', S['subsection']))
        condiciones = [
            'Se aprobará con las siguientes condiciones:',
            '• Límite de crédito: $' + f'{monto:,.0f}',
            '• Revisión periódica cada 6 meses',
            '• Reporte de mora inmediato',
            '• Documentación completa obligatoria',
        ]
        for c in condiciones:
            story.append(Paragraph(c, S['body']))
        story.append(Spacer(1, 16))

    # Justificación final del monto
    justificacion = res.get('monto_justificacion', '')
    if justificacion:
        story.append(Paragraph('DETALLE DEL MONTO RECOMENDADO', S['subsection']))
        story.append(Paragraph(justificacion, S['body']))
        story.append(Spacer(1, 12))

    # Observaciones
    observaciones = result.get('observaciones', '')
    if observaciones:
        story.append(Paragraph('OBSERVACIONES', S['subsection']))
        story.append(Paragraph(observaciones, S['body']))
        story.append(Spacer(1, 12))

    # Resumen de hallazgos
    story.append(Paragraph('RESUMEN DE HALLAZGOS', S['subsection']))
    hallazgos_data = [
        ['ITEM', 'RESULTADO'],
        ['Score crediticio', f'{score}/1000 — Nivel {nivel}'],
        ['Decisión', badge_text],
        ['Monto recomendado', f'${monto:,.0f}'],
        ['Antecedentes verificados', f'{len(ant)} fuentes'],
        ['Coincidencias encontradas', str(found) if found else 'Ninguna'],
        ['Documentación', f'{docs_count}/{len(doc_labels)} documentos'],
        ['Factores positivos', str(len(positivos))],
        ['Factores negativos', str(len(negativos))],
    ]
    story.append(_make_table(hallazgos_data, [3*inch, 3.5*inch], 'gray_dark'))
    story.append(Spacer(1, 16))

    # Espacio para firmas
    story.append(Spacer(1, 40))
    firma_data = [
        ['_________________________', '', '_________________________'],
        ['Evaluador de Crédito', '', 'Aprobador'],
        ['Fecha: _______________', '', 'Fecha: _______________'],
    ]
    firma_table = Table(firma_data, colWidths=[2.5*inch, 1.5*inch, 2.5*inch])
    firma_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(firma_table)

    # Pie de página
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=1, color=_hex('gray_light')))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        'VerifyData — Inteligencia de datos para decisiones seguras',
        ParagraphStyle('Footer', parent=S['small'], alignment=TA_CENTER, textColor=_hex('gray'))
    ))
    story.append(Paragraph(
        'Documento generado automáticamente | Uso restringido al destinatario',
        ParagraphStyle('Footer2', parent=S['tiny'], alignment=TA_CENTER, textColor=_hex('gray'))
    ))
    story.append(Paragraph(
        f'Fecha: {result.get("fecha_solicitud", "N/A")} | Código: CV-{result.get("cedula_nit", "000")}',
        ParagraphStyle('Footer3', parent=S['tiny'], alignment=TA_CENTER, textColor=_hex('gray'))
    ))

    # Construir PDF final (ya incluye anexos renderizados como imágenes via fitz)
    doc.build(story)

    if output_path is None:
        # Retornar desde memoria
        buffer.seek(0)
        return buffer.read()
    else:
        with open(output_path, 'rb') as f:
            return f.read()
