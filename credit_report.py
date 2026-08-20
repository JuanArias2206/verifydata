"""
credit_report.py — Generador de PDF para reportes de crédito.

Genera un PDF profesional con toda la información del perfil crediticio:
- Portada con branding VerifyData
- Resumen ejecutivo (score, nivel, recomendación)
- Historial comercial RSALES
- Antecedentes y listas restrictivas
- Documentación adjunta
- Justificación del monto
- Factor de riesgo detallado
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
    PageBreak, HRFlowable, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

ROOT = Path(__file__).parent


def generate_credit_pdf(result: dict, output_path: str | None = None) -> bytes:
    """Genera un PDF del perfil crediticio completo."""
    if output_path is None:
        output_path = str(ROOT / "data" / "credit_report.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=50,
        leftMargin=50,
        topMargin=60,
        bottomMargin=60,
    )

    styles = getSampleStyleSheet()
    story = []

    # Estilos personalizados
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Title'],
        fontSize=24, spaceAfter=6, textColor=colors.HexColor('#1a1a2e')
    )
    subtitle_style = ParagraphStyle(
        'CustomSubtitle', parent=styles['Normal'],
        fontSize=12, textColor=colors.HexColor('#666666'), spaceAfter=20
    )
    section_style = ParagraphStyle(
        'SectionTitle', parent=styles['Heading2'],
        fontSize=14, textColor=colors.HexColor('#2d3748'),
        spaceBefore=16, spaceAfter=8, borderWidth=0,
        borderPadding=0, borderColor=colors.HexColor('#e2e8f0')
    )
    body_style = ParagraphStyle(
        'CustomBody', parent=styles['Normal'],
        fontSize=10, leading=14, spaceAfter=6
    )
    small_style = ParagraphStyle(
        'SmallText', parent=styles['Normal'],
        fontSize=8, textColor=colors.HexColor('#718096')
    )

    p = result.get('perfil_crediticio', {})
    res = result.get('resumen_ejecutivo', {})
    ant = result.get('antecedentes', {})

    # ═══ PORTADA ═══
    story.append(Spacer(1, 40))
    story.append(Paragraph("REPORTE DE ANÁLISIS DE RIESGO CREDITICIO", title_style))
    story.append(Paragraph("VerifyData — Inteligencia de datos para decisiones seguras", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#4299e1')))
    story.append(Spacer(1, 20))

    # Datos del cliente
    client_data = [
        ['DATOS DEL CLIENTE', ''],
        ['Nombre:', result.get('nombre', '')],
        ['CC/NIT:', result.get('cedula_nit', '')],
        ['Fecha de solicitud:', result.get('fecha_solicitud', 'N/A')],
        ['Tipo de solicitud:', result.get('tipo_solicitud', 'N/A')],
    ]
    client_table = Table(client_data, colWidths=[2*inch, 4.5*inch])
    client_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4299e1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(client_table)
    story.append(Spacer(1, 20))

    # ═══ RESUMEN EJECUTIVO ═══
    story.append(Paragraph("RESUMEN EJECUTIVO", section_style))

    score = p.get('score', 0)
    nivel = p.get('nivel_riesgo', 'NO_EVALUADO')
    aprobado = res.get('aprobado', False)
    monto = res.get('monto_maximo', 0)

    nivel_color = {
        'BAJO': '#48bb78', 'MEDIO': '#ed8936',
        'ALTO': '#f56565', 'CRITICO': '#c53030'
    }.get(nivel, '#718096')

    summary_data = [
        ['INDICADOR', 'VALOR', 'OBSERVACIÓN'],
        ['Score Crediticio', str(score) + ' / 1000', f'Nivel: {nivel}'],
        ['Decisión', 'APROBADO' if aprobado else 'RECHAZADO', p.get('recomendacion', '')],
        ['Monto Máximo Recomendado', f'${monto:,.0f}', res.get('monto_justificacion', '')[:100]],
    ]
    summary_table = Table(summary_data, colWidths=[2*inch, 1.5*inch, 3*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d3748')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (1, 1), (1, 1), colors.HexColor(nivel_color + '20')),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 12))

    # ═══ INFORMACIÓN FINANCIERA ═══
    story.append(Paragraph("INFORMACIÓN FINANCIERA", section_style))

    fin_data = [
        ['CONCEPTO', 'VALOR'],
        ['Crédito Actual', f'${result.get("credito_actual", 0):,.0f}'],
        ['Monto Solicitado', f'${result.get("monto_solicitar", 0):,.0f}'],
        ['Cupo Inicial', f'${result.get("cupo_inicial", 0):,.0f}'],
        ['Ingreso Mensual', f'${result.get("ingreso_mensual", 0):,.0f}'],
        ['Fuente de Ingreso', result.get('fuente_ingreso', 'N/A')],
        ['Actividad Económica', result.get('actividad_economica', 'N/A')],
        ['Promedio Compras', f'${result.get("promedio_compras", 0):,.0f}'],
        ['Compra Mínima', f'${result.get("compra_minima", 0):,.0f}'],
        ['Compra Máxima', f'${result.get("compra_maxima", 0):,.0f}'],
        ['Número de Compras', str(result.get('numero_compras', 0))],
        ['Promedio Pago (días)', str(result.get('promedio_pago_dias', 0))],
        ['Patrimonio Estimado', f'${result.get("patrimonio", 0):,.0f}'],
        ['Endeudamiento Total', f'${result.get("endeudamiento", 0):,.0f}'],
    ]
    fin_table = Table(fin_data, colWidths=[3*inch, 3.5*inch])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
    ]))
    story.append(fin_table)
    story.append(Spacer(1, 12))

    # ═══ HISTORIAL RSALES ═══
    rsales = p.get('rsales')
    if rsales:
        story.append(Paragraph("HISTORIAL COMERCIAL — RSALES", section_style))

        rsales_data = [
            ['MÉTRICA', 'VALOR'],
            ['Cartera Total', f'${rsales.get("cartera_total", 0):,.0f}'],
            ['Cartera Vencida', f'${rsales.get("cartera_vencida", 0):,.0f} ({rsales.get("pct_vencida", 0):.1f}%)'],
            ['Cartera Corriente', f'${rsales.get("cartera_corriente", 0):,.0f}'],
            ['Días Mora Máxima', f'{rsales.get("dias_mora_max", 0)} días'],
            ['Compras Totales', f'${rsales.get("compras_total", 0):,.0f}'],
            ['Número de Pedidos', str(rsales.get("num_pedidos", 0))],
            ['Promedio Pedido', f'${rsales.get("promedio_pedido", 0):,.0f}'],
            ['Última Compra', rsales.get("ultima_compra_fecha", "N/A") or "N/A"],
            ['Visitas 12 meses', str(rsales.get("visitas_12m", 0))],
        ]
        rsales_table = Table(rsales_data, colWidths=[3*inch, 3.5*inch])
        rsales_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3182ce')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ebf8ff')]),
        ]))
        story.append(rsales_table)
        story.append(Spacer(1, 12))

    # ═══ ANTECEDENTES ═══
    story.append(Paragraph("ANTECEDENTES Y LISTAS RESTRICTIVAS", section_style))

    ant_data = [['FUENTE', 'ESTADO', 'DETALLE']]
    for k, v in ant.items():
        estado = 'ENCONTRADO' if v.get('matched') else ('ERROR' if v.get('error') else 'LIMPIO')
        detalle = v.get('summary', '')[:80] if v.get('summary') else ''
        ant_data.append([k, estado, detalle])

    ant_table = Table(ant_data, colWidths=[1.8*inch, 1.2*inch, 3.5*inch])
    ant_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#805ad5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#faf5ff')]),
    ]))
    story.append(ant_table)
    story.append(Spacer(1, 12))

    # ═══ BLOQUEANTES ═══
    bloqueantes = res.get('bloqueantes', [])
    if bloqueantes:
        story.append(Paragraph("⚠ BLOQUEANTES ENCONTRADOS", ParagraphStyle(
            'BlockTitle', parent=styles['Heading2'],
            fontSize=13, textColor=colors.HexColor('#c53030'),
            spaceBefore=12, spaceAfter=6
        )))
        for b in bloqueantes:
            story.append(Paragraph(f"• {b}", ParagraphStyle(
                'BlockItem', parent=body_style,
                textColor=colors.HexColor('#9b2c2c'),
                leftIndent=20, spaceBefore=2, spaceAfter=2
            )))
        story.append(Spacer(1, 8))

    # ═══ DOCUMENTACIÓN ═══
    docs = p.get('docs', {})
    docs_count = sum(1 for v in docs.values() if v)
    docs_total = len(docs) if docs else 0

    story.append(Paragraph(f"DOCUMENTACIÓN ADJUNTA ({docs_count}/{docs_total})", section_style))

    doc_labels = {
        'cedula_frontal': 'Cédula Frontal',
        'cedula_posterior': 'Cédula Posterior',
        'rut': 'RUT',
        'camara_comercio': 'Cámara de Comercio',
        'estados_financieros': 'Estados Financieros',
        'declaracion_renta': 'Declaración de Renta'
    }

    doc_data = [['DOCUMENTO', 'ESTADO']]
    for dk, dl in doc_labels.items():
        ok = docs.get(dk, False)
        doc_data.append([dl, '✓ Adjuntado' if ok else '✗ No adjuntado'])

    doc_table = Table(doc_data, colWidths=[3*inch, 3.5*inch])
    doc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#38a169')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0fff4')]),
    ]))
    story.append(doc_table)
    story.append(Spacer(1, 12))

    # ═══ FACTORES DE RIESGO ═══
    story.append(Paragraph("ANÁLISIS DE FACTORES DE RIESGO", section_style))

    # Factores positivos
    positivos = p.get('factores_positivos', [])
    if positivos:
        story.append(Paragraph("Factores Positivos:", ParagraphStyle(
            'PosTitle', parent=body_style, textColor=colors.HexColor('#276749'),
            fontName='Helvetica-Bold', spaceBefore=6
        )))
        for f in positivos:
            story.append(Paragraph(f"✓ {f}", ParagraphStyle(
                'PosItem', parent=body_style, leftIndent=20,
                textColor=colors.HexColor('#276749'), spaceBefore=2
            )))

    # Factores negativos
    negativos = p.get('factores_negativos', [])
    if negativos:
        story.append(Paragraph("Factores Negativos:", ParagraphStyle(
            'NegTitle', parent=body_style, textColor=colors.HexColor('#c53030'),
            fontName='Helvetica-Bold', spaceBefore=6
        )))
        for f in negativos:
            story.append(Paragraph(f"✗ {f}", ParagraphStyle(
                'NegItem', parent=body_style, leftIndent=20,
                textColor=colors.HexColor('#c53030'), spaceBefore=2
            )))

    # Alertas
    alertas = p.get('alertas', [])
    if alertas:
        story.append(Paragraph("Alertas:", ParagraphStyle(
            'AlertTitle', parent=body_style, textColor=colors.HexColor('#b7791f'),
            fontName='Helvetica-Bold', spaceBefore=6
        )))
        for a in alertas:
            story.append(Paragraph(f"⚠ {a}", ParagraphStyle(
                'AlertItem', parent=body_style, leftIndent=20,
                textColor=colors.HexColor('#b7791f'), spaceBefore=2
            )))

    story.append(Spacer(1, 16))

    # ═══ JUSTIFICACIÓN DEL MONTO ═══
    justificacion = res.get('monto_justificacion', '')
    if justificacion:
        story.append(Paragraph("JUSTIFICACIÓN DEL MONTO MÁXIMO RECOMENDADO", section_style))
        story.append(Paragraph(justificacion, body_style))
        story.append(Spacer(1, 12))

    # ═══ RECOMENDACIÓN FINAL ═══
    story.append(Paragraph("RECOMENDACIÓN FINAL", section_style))
    story.append(Paragraph(p.get('recomendacion', ''), ParagraphStyle(
        'RecFinal', parent=styles['Heading3'],
        fontSize=14, textColor=colors.HexColor(nivel_color),
        spaceBefore=8, spaceAfter=12
    )))

    # ═══ PIE DE PÁGINA ═══
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "VerifyData — Documento generado automáticamente | Uso restringido al destinatario",
        ParagraphStyle('Footer', parent=small_style, alignment=TA_CENTER)
    ))
    story.append(Paragraph(
        f"Fecha de generación: {result.get('fecha_solicitud', 'N/A')}",
        ParagraphStyle('FooterDate', parent=small_style, alignment=TA_CENTER)
    ))

    # Construir PDF
    doc.build(story)

    # Leer y devolver bytes
    with open(output_path, 'rb') as f:
        return f.read()
