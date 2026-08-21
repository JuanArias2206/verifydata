# Portfolio Vivo + Scoring Cobranza (Enfoque B) — Spec

> **Proyecto:** VerifyData — SaaS Mixto (personas, 65 del Excel BITACORA)
> **Eje:** Cobranza (dolor principal)

**Goal:** Migrar Excel a portfolio vivo `/cartera` con semáforo mora, scoring v2 calibrado con historial 199 filas, PDF unificado vendible con QR, y alertas 30/60/90 automatizadas.

**Arquitectura:** Postgres como fuente única (tablas `credit_requests`, `cartera_snapshots`), `rsales_client` indexado por `nit` (no O(N) scan), `report` unificado, cron cada 6h compara snapshots y escribe `audit_log` + email ejecutivo.

**Tech Stack:** Flask 3, Postgres `pg_trgm` + `psycopg_pool` 16, RSales ventasremotas.com, ReportLab, Vercel serverless con `/tmp` + `ThreadPoolExecutor` inline.

---

## Sprints

### S1 — Portfolio vivo (migración Excel → DB + /cartera)
- Migration `migrations/001_credit_requests.sql` y `002_cartera_snapshots.sql`
- `credit_requests` (id, cedula, nombre, tipo_solicitud, ejecutivo, montos, calificacion, mora, observaciones, created_at)
- Seed desde `excel_reader.read_all()` con `ON CONFLICT(cedula) DO UPDATE`
- `GET /cartera` — tabla semáforo 4 colores, filtros `?ejecutivo=&estado=`
- `GET /api/credit/cartera` — JSON para polling
- Reemplazar hardcoded 18 sujetos fallback por DB query

### S2 — Scoring v2 cobranza
- Backtest 199 filas Hoja1: target `PRESENTA MORA` o `CARTERA CASTIGADA`
- Nuevos pesos: `historial_pago 35%` (time-decay mora), `morosidad 20%`, `capacidad 15%`, `comportamiento 15%`, `datacrédito 10%`, `documentación 5%`
- Modelo `NUEVO` (sin RSales) → score base 500 + exige `ingreso_mensual`
- Función `score_cobranza(profile) -> (score, contribuciones)` para explainability
- Persistir `score_contrib` JSON en `credit_requests`

### S3 — PDF unificado vendible
- Nuevo `credit_report_unified.py` que importa helpers de `report.py` y `credit_report.py`
- 7 páginas: portada APROBADO/RECHAZADO + KPIs + perfil financiero + RSales aging + 68 fuentes resumidas (bulk) + docs + factores + QR hash SHA256 verificable `GET /verify/<hash>` que compara contra `audit_log`
- `GET /download/credit-pdf/<token>` con `Content-Disposition` y `X-Robust-Hash`

### S4 — Alertas cobranza 30/60/90
- `cron/cobranza.py` cada 6h: `SELECT cartera FROM rsales` vs `cartera_snapshots` último, detecta cruces 30/60/90
- `INSERT audit_log(event='mora.30', fields={cedula, dias})` + email `ejecutivo@`
- Badge en `/cartera` y `/credit/results/<token>` con `dias_mora_max` actualizado
- Test: simular mora 29→31 y verificar alerta

---

## Validación

- S1: `curl /api/credit/cartera?ejecutivo=SANTANDER` devuelve 6, semáforo correcto tras `python migrations/seed.py`
- S2: `pytest tests/test_cobranza.py -k backtest` → AUC > 0.70
- S3: `python -m credit_report_unified --token <tok>` genera 7 págs, QR escaneable
- S4: `python cron/cobranza.py --dry-run` loguea transiciones sin spam
