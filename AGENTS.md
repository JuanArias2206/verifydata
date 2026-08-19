# AGENTS.md — VerifyData (manual operativo)

> Webapp Flask para búsqueda automatizada en **68 fuentes públicas**
> colombianas e internacionales. Stack: Python 3.11+, Flask, Playwright,
> solvers de captcha (CapSolver/2captcha/Anthropic/trivia), reportlab,
> SQLite. Branding neutral: **VerifyData**.

Este archivo es el manual para futuros agentes/desarrolladores que toquen
este proyecto. Léelo entero antes de modificar código.

---

## 1. Stack y entry point

| Capa            | Tecnología                                 |
| --------------- | ------------------------------------------ |
| Lenguaje        | Python 3.11+                               |
| Web             | Flask 3 (templates inline en `app.py`)     |
| HTTP            | `requests` + `beautifulsoup4` + `lxml`     |
| Browser         | Playwright (síncrono; pool en `browsers/`) |
| PDF             | `reportlab`                                |
| Captcha solvers | CapSolver, 2captcha, Anthropic, trivia, noop |
| Persistencia    | SQLite (`data/verifydata.db`)              |
| Listas bulk     | Cache local con TTL (SQLite)               |
| Config          | YAML (`config.yaml`) + `config.py` loader  |

**Entry point:** `app.py` (raíz del proyecto). Levanta la webapp en
`127.0.0.1:5080` por defecto.

```bash
python3 app.py
# abrir http://127.0.0.1:5080
```

---

## 2. Comandos clave

### Correr la webapp
```bash
python3 app.py            # o: PORT=5070 python3 app.py
```

### Suite de tests (correr desde la raíz del proyecto)
```bash
python3 tests/test_phase1.py
python3 tests/test_phase2.py
python3 tests/test_phase3.py
python3 tests/test_estados.py
python3 tests/test_phase456.py
python3 tests/test_phase456_quick.py
```

> Los tests importan el código como paquete (`from sources import …`).
> Hay que correrlos **desde la raíz** o con `PYTHONPATH=.`.

### Probar una fuente individual (sin Flask)
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from sources import registry
from solvers import get_default_solver
for s in registry.all_sources():
    if 'Registradur' in s.name:
        hit = s.fetch('JUAN PEREZ', '1192722347', '03/07/2020',
                      solver=get_default_solver())
        print(hit)
        break
"
```

### Verificar que la DB SQLite está sana
```bash
python3 -c "import sqlite3; \
  sqlite3.connect('data/verifydata.db').execute('SELECT 1') \
  and print('OK: verifydata.db is valid')"
```

### Generar un PDF de reporte
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from report import build_report
hits = []
build_report(hits, query={'nombre': 'TEST', 'cedula': '123'},
             out='report_test.pdf')
"
```

---

## 3. Estructura de directorios

```
busqueda_datos/
├── app.py                 # Flask webapp (templates inline)
├── api.py                 # API REST /api/v1 + Swagger
├── auth.py                # OTP email + Microsoft SSO + RBAC
├── config.yaml            # ⚠ NO versionado — ajustes locales
├── config.example.yaml    # plantilla
├── config.py              # loader de config.yaml + env
├── db.py                  # SQLite/PostgreSQL (verifydata.db)
├── runs.py                # runs progresivos (streaming + subprocess)
├── report.py              # PDF generator (reportlab, brand VerifyData)
├── ui_theme.py            # sistema de diseño web (wordmark, CSS, shell)
├── requirements.txt
├── demo_search.py         # CLI standalone de búsqueda
├── findings.py            # hallazgos/severidad para el reporte
├── inventory.json         # inventario de fuentes (nombre/categoría/url)
├── Procfile               # despliegue PaaS
├── seed_admin.py          # alta de usuarios admin
├── maintenance.py         # retención/limpieza de data efímera
├── logging_config.py      # logging JSON + auditoría
├── migrate_sqlite_to_pg.py# migración SQLite → PostgreSQL
│
├── sources/               # 68 fuentes registradas vía @register
│   ├── __init__.py        # auto-importa todas
│   ├── base.py            # Hit, Source (Protocol), CaptchaUnsolved, safe_fetch
│   ├── registry.py        # @register, all_sources(), run_all()
│   ├── local_lists.py     # normalize, tokenize (matching)
│   ├── _existing.py       # fuentes base (SECOP×3, Registraduría, OFAC, UN, UK)
│   ├── internacionales.py # OFAC×3, UN, UK, BIS, UE, Canadá
│   ├── icij.py            # Panama/Paradise/Pandora/Offshore/Bahamas
│   ├── wanted.py          # FBI, INTERPOL, DEA, ICE, etc.
│   ├── pep.py             # PEP
│   ├── noticias.py        # Fiscalía, Policía, Presidencia, Procuraduría, Insight
│   ├── especializados.py  # FCPA, SIRNA, JCC, PACO
│   ├── dian.py            # RUT, Boletín Proveedores Ficticios
│   ├── procuraduria.py    # CANONICAL ejemplo browser+trivia
│   ├── policia.py         # Antecedentes, Delitos Sexuales
│   ├── policia_inhab.py   # Inhabilidades Ley 1918
│   ├── contraloria.py     # Responsabilidad Fiscal, Contaduría
│   ├── rama_judicial.py   # SIUGJ, JEPMS, Juzgados TYBA
│   ├── browser_sources.py # browser-only (SIGEP, INTERPOL, BIS, PTE, GC)
│   ├── supersociedades.py # Información general de sociedades
│   ├── rues_nit.py        # RUES por NIT (empresas)
│   └── _browser_helper.py # helpers de Playwright
│
├── solvers/               # captcha solvers
│   ├── base.py            # CaptchaSolver ABC + CaptchaUnsolved
│   ├── noop.py            # NoOpSolver + get_default_solver() (lee config)
│   ├── trivia.py          # TriviaSolver (math + Anthropic fallback)
│   ├── anthropic.py       # AnthropicSolver
│   ├── capsolver.py       # CapSolver
│   ├── twocaptcha.py      # TwoCaptchaSolver (API v2 + legacy)
│   ├── anticaptcha.py     # AntiCaptchaSolver
│   ├── chain.py           # FallbackSolver (cadena con respaldo)
│   ├── factory.py         # build_chain()
│   └── webshare.py        # proxies webshare
│
├── browsers/              # Playwright user-data dirs (gitignored)
├── lists/                 # bulk list manager + downloaders
├── data/                  # ⚠ gitignored — se crea en runtime
│   ├── verifydata.db      # SQLite (4 tablas)
│   ├── certs/             # Certificados generados
│   └── screenshots/       # Playwright screenshots por fuente
├── tests/                 # suite de tests
└── static/
    ├── fonts/             # Montserrat (self-hosted)
    └── report-band.png    # banda decorativa del PDF (neutral, sin marca)
```

---

## 4. Variables de entorno y API keys

⚠ **Las API keys viven en `.env` (no versionado).** `config.yaml` también
está gitignored. Ver `.env.example` para la lista completa.

| Variable                    | Servicio     | ¿Requerida?                 |
| --------------------------- | ------------ | --------------------------- |
| `TWOCAPTCHA_API_KEY`        | 2captcha.com | Captchas visuales/reCAPTCHA |
| `CAPSOLVER_API_KEY`         | CapSolver    | Cadena principal reCAPTCHA  |
| `ANTHROPIC_API_KEY`         | Anthropic    | TriviaSolver / LLM          |
| `ANTICAPTCHA_API_KEY`       | anti-captcha | Alternativa                 |
| `WEBSHARE_API_KEY`          | webshare     | Proxies para 2captcha       |
| `VERIFYDATA_ENTITY_NIT` / `VERIFYDATA_ENTITY_NAME` | — | Entidad consultante en formularios |
| `VERIFYDATA_API_KEYS`       | API REST     | Obligatoria en prod         |
| `PORT` / `HOST`             | webapp       | Default `5070` / `0.0.0.0`  |

---

## 5. Convenciones de código (¡importante!)

### Source protocol
Toda fuente en `sources/*.py` debe:

1. Exponer los atributos de la `Source` protocol (`sources/base.py`):
   - `name: str`               — único, legible
   - `source_url: str`         — URL del portal original
   - `category: str`           — agrupación lógica
   - `requires_captcha: bool`
   - `captcha_type: str | None` — `"image" | "recaptcha_v2" | "recaptcha_v2_enterprise" | "hcaptcha" | "trivia" | "js" | "login" | None`

2. Estar decorada con `@registry.register` y definir `fetch(self,
   nombre, cedula, fecha_exp=None, solver=None) -> Hit`.

3. **Nunca** lanzar excepción al caller. Capturar todo y devolver
   `Hit(..., error="<TipoEx>: <msg>")`. El helper `safe_fetch` envuelve
   `fetch` y mide `elapsed_s`.

4. Devolver un `Hit` (dataclass en `sources/base.py`):
   - `matched`, `summary`, `details`, `captcha_required`, `notice`,
     `download_url`, `evidence_urls`, `error`.

5. Si requiere captcha, llamar al solver y capturar `CaptchaUnsolved`
   para devolver `Hit(captcha_required=True, notice=…)`.

### Solver selection
El solver por defecto es `"chain"` (`config.yaml: captcha.solver`):
`FallbackSolver` con el orden de `captcha.order` (por defecto
`["capsolver", "twocaptcha"]`). Para armar la cadena localmente:
`from solvers.factory import build_chain; s = build_chain(cfg, use_proxy=True)`.

⚠ **GOTCHA #1.** La cadena resuelve reCAPTCHA/hCaptcha/imagen, pero
**no trivia**. Procuraduría necesita trivia → construye su propio
`TriviaSolver` local.

⚠ **GOTCHA #2 (timeouts).** `runs.py` corre cada fuente en un subproceso
con timeout por-fuente (`PER_SOURCE_TIMEOUT`, default 70s). Las fuentes
con captcha necesitan budget amplio (Contraloría 260s, Policía 170s).

### Browser / Playwright
- `sources/procuraduria.py` es el **ejemplo canónico** del patrón
  label-based con Playwright.
- Crear un **nuevo** `sync_playwright()` por llamada (nunca compartirlo
  entre threads).
- **NUNCA** `wait_for_timeout(N)` ciego — usar polling.

### Errores
- **Errores van en `Hit.error`, no como excepción.**

### Código / estilo
- Type hints en funciones públicas.
- `from __future__ import annotations` en módulos nuevos.
- Docstring al tope del archivo explicando el propósito.
- Mismo `category` para fuentes de un mismo portal.

---

## 6. Cómo agregar una nueva fuente

1. **Identificar el portal**: URL, tipo de captcha, API pública o
   scraping, ¿necesita Playwright?
2. **Elegir archivo** en `sources/` (mismo portal → mismo archivo; portal
   nuevo → `sources/<portal>.py`) e importarlo en `sources/__init__.py`.
3. **Implementar la clase** siguiendo el skeleton de §5 (ver
   `procuraduria.py` para browser, `_existing.py` para requests puro).
4. **Lista bulk** → usar `lists/manager.py` para cachear con TTL.
5. **Captcha** → decidir solver según `captcha_type` (§5).
6. **Probar** con el snippet de §2.
7. **Portal inestable** → timeout corto + `Hit(error="SiteUnavailable: …")`.
8. **Actualizar** `inventory.json` y agregar test en `tests/`.

---

## 7. Reglas duras (no negociables)

- **NO** commitear API keys. `.env` y `config.yaml` están gitignored.
- **NO** lanzar excepciones desde `fetch()` — todo error va a `Hit.error`.
- **NO** usar `wait_for_timeout(N)` ciego en Playwright — usar polling.
- **NO** compartir un `sync_playwright()` entre threads.
- **NO** eliminar `data/verifydata.db` ni `data/certs/` en producción —
  son datos de larga vida.
- **NO** tocar `config.yaml` para "arreglar" el solver global de una
  fuente que necesita otro tipo — construir el solver localmente.

---

## 8. Branding VerifyData

- **Wordmark de texto** (`ui_theme.WORDMARK` = `Verify<span>Data</span>`),
  sin imágenes de logo. CSS: `.wordmark` (+ `wm-light`, `wm-sm`,
  `wm-hero`).
- Paleta: magenta `#d00de3` → violeta `#6941f4` → azul `#3e7af9` → cian
  `#1de5e9`; sidebar `#221f33`; fuente Montserrat (self-hosted en
  `static/fonts/`).
- PDF (`report.py`): banda `static/report-band.png` + wordmark dibujado
  como texto ("Verify" blanco + "Data" cian). Tokens `BRAND_*`.
- Footer del PDF: "VerifyData · Documento de uso restringido al
  destinatario".
- Si se cambia el nombre de marca, basta actualizar `ui_theme.BRAND_NAME`,
  `ui_theme.WORDMARK`, y el wordmark del PDF en `report.py:_draw_chrome`.

---

## 9. Referencias rápidas

- Source protocol: `sources/base.py`.
- Source skeleton (Playwright): `sources/procuraduria.py`.
- Source skeleton (requests puro): `sources/_existing.py`.
- Captcha solver ABC: `solvers/base.py` · fábrica: `solvers/factory.py`.
- DB schema: `db.py` (tablas `list_entries`, `list_meta`, `search_runs`,
  `cert_files`).
- Runs progresivos: `runs.py:run_search_progressive`.
- PDF report: `report.py:build_report`.
- Sistema de diseño web: `ui_theme.py`.

---

_Maintained by VerifyData · 2026_
