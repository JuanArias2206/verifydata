# PDF Crediticio — Plan de Mejora Integral

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Crear un PDF profesional, impactante y completo para el perfil de riesgo crediticio que sirva como documento comercial vendible.

**Architecture:** Reescribir `credit_report.py` completo con diseño premium usando ReportLab. El PDF será el documento principal que se envía por correo y se descarga.

**Tech Stack:** Python, ReportLab (PDF generation), Colors/Styles personalizados

---

## Estructura del PDF mejorado

```
PÁGINA 1: PORTADA
├── Branding VerifyData (wordmark)
├── Título: "INFORME DE ANÁLISIS DE RIESGO CREDITICIO"
├── Badge de decisión (APROBADO/RECHAZADO) con color
├── Datos del cliente (nombre, CC, fecha)
├── Score visual (círculo grande con color)
└── Monto máximo recomendado

PÁGINA 2: RESUMEN EJECUTIVO
├── KPIs visuales (score, nivel, monto, antecedentes)
├── Tabla de decisión con fórmula del monto
├── Justificación del monto (pasos detallados)
└── Resumen de fuentes verificadas

PÁGINA 3: PERFIL FINANCIERO
├── Tabla de ingresos y gastos
├── Ratios financieros (endeudamiento, capacidad)
├── Historial de compras (promedio, frecuencia)
└── Comparativa con promedio del sector

PÁGINA 4: HISTORIAL COMERCIAL RSALES
├── Cartera (total, vencida, corriente)
├── Aging buckets (visual)
├── Frecuencia de compra
└── Últimas transacciones

PÁGINA 5: ANTECEDENTES
├── Tabla de 8 fuentes verificadas
├── Estado de cada una (LIMPIO/ENCONTRADO)
├── Tiempo de verificación
└── Detalle de coincidencias

PÁGINA 6: DOCUMENTACIÓN
├── Lista de documentos adjuntos
├── Estado de cada uno (✓/✗)
└── Checklist de completitud

PÁGINA 7: ANÁLISIS DE RIESGO
├── Factores positivos (verde)
├── Factores negativos (rojo)
├── Alertas (amarillo)
└── Nivel de riesgo detallado

PÁGINA 8: RECOMENDACIÓN Y FIRMAS
├── Recomendación final
├── Condiciones (si aplica)
├── Espacio para firmas
└── Pie de página VerifyData
```

---

## Tareas de implementación

### Task 1: Reescribir estilos y estructura base

**Files:**
- Modify: `credit_report.py`

- [ ] Reemplazar estilos básicos por estilos premium con colores corporativos
- [ ] Agregar función helper `make_section_header()` para headers consistentes
- [ ] Agregar función helper `make_kpi_card()` para indicators visuales
- [ ] Agregar función helper `make_data_table()` para tablas con estilo
- [ ] Definir paleta de colores corporativa VerifyData

### Task 2: Portada premium

**Files:**
- Modify: `credit_report.py`

- [ ] Reescribir `_build_cover()` con diseño premium
- [ ] Badge de decisión grande y colorido (APROBADO/RECHAZADO)
- [ ] Score visual tipo gauge (círculo con progreso)
- [ ] Datos del cliente en layout limpio
- [ ] Fecha y código del reporte

### Task 3: Resumen ejecutivo con KPIs

**Files:**
- Modify: `credit_report.py`

- [ ] 4 KPIs visuales en fila (Score, Nivel, Monto, Antecedentes)
- [ ] Tabla de decisión con fórmula visible
- [ ] Justificación del monto con pasos numerados
- [ ] Indicadores de color por nivel de riesgo

### Task 4: Perfil financiero detallado

**Files:**
- Modify: `credit_report.py`

- [ ] Tabla de ingresos vs gastos
- [ ] Ratios financieros calculados:
  - Endeudamiento / Ingreso
  - Capacidad de pago
  - Ratio compras / crédito
- [ ] Historial de compras (año, promedio, frecuencia)
- [ ] Comparativa con promedio del sector

### Task 5: Historial RSALES mejorado

**Files:**
- Modify: `credit_report.py`

- [ ] Aging buckets visual (barra de progreso)
- [ ] Cartera total vs vencida vs corriente
- [ ] Frecuencia de compra (días entre pedidos)
- [ ] Últimas 3 transacciones
- [ ] Tendencia de mora

### Task 6: Antecedentes con detalle

**Files:**
- Modify: `credit_report.py`

- [ ] Tabla de 8 fuentes con iconos de color
- [ ] Tiempo de verificación por fuente
- [ ] Detalle de coincidencias (si las hay)
- [ ] Resumen: X limpias, Y encontradas, Z errores

### Task 7: Documentación y factores

**Files:**
- Modify: `credit_report.py`

- [ ] Checklist visual de documentos
- [ ] Factores positivos con iconos verdes
- [ ] Factores negativos con iconos rojos
- [ ] Alertas con iconos amarillos
- [ ] Nivel de riesgo detallado

### Task 8: Recomendación y pie

**Files:**
- Modify: `credit_report.py`

- [ ] Recomendación final grande y clara
- [ ] Condiciones (si aplica)
- [ ] Espacio para firmas
- [ ] Pie VerifyData con fecha y código
- [ ] Disclaimer legal

### Task 9: Testing y commit

**Files:**
- Modify: `credit_report.py`

- [ ] Generar PDF de prueba con datos reales
- [ ] Verificar que todas las secciones aparecen
- [ ] Verificar que los colores y estilos son correctos
- [ ] Commit y push
