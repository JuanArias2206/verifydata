# VerifyData

**Demo de verificación automatizada de personas y empresas en 68 fuentes
públicas** colombianas e internacionales: listas de sanciones, antecedentes,
PEP, contratación pública, fugitivos, noticias y más.

Incluye:

- **Webapp** con búsqueda progresiva en vivo (persona por cédula/nombre y
  empresa por NIT).
- **Reporte PDF** estilo dossier con evidencia (capturas y certificados).
- **API REST** versionada en `/api/v1` con docs interactivas (Swagger).
- **Solvers de captcha** configurables (CapSolver, 2captcha, Anthropic,
  trivia) con cadena de respaldo.

---

## Quickstart (5 minutos)

Requisitos: **Python 3.11+** y conexión a internet.

```bash
# 1. Descomprimir y entrar a la carpeta
cd busqueda_datos

# 2. Crear entorno virtual e instalar dependencias
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m playwright install chromium   # solo la primera vez

# 3. Configurar (opcional para probar; necesario para fuentes con captcha)
cp .env.example .env
cp config.example.yaml config.yaml
#    → edita .env y rellena las API keys que tengas (2captcha, CapSolver,
#      Anthropic). Sin keys, las fuentes con captcha devuelven el enlace
#      para consulta manual; el resto funciona igual.

# 4. Correr
python3 app.py
# abrir http://127.0.0.1:5070
```

Eso es todo: la base de datos SQLite y los caches de listas se crean solos
en `data/` en el primer arranque.

---

## Uso

### Webapp
- **Persona**: nombre + cédula (+ fecha de expedición, opcional).
- **Empresa**: NIT — resuelve razón social en RUES y verifica a los
  representantes legales.
- Al terminar se puede descargar el **PDF** del reporte.

### API REST
Mismo proceso, en `/api/v1`. Documentación interactiva:
**http://127.0.0.1:5070/api/v1/docs** · Referencia: [API.md](API.md).

```bash
curl -s -X POST http://127.0.0.1:5070/api/v1/searches/sync \
  -H "Content-Type: application/json" \
  -d '{"cedula":"1234567890","sources":"featured"}'
```

En producción, defina claves con `VERIFYDATA_API_KEYS` (header `X-API-Key`).

### CLI de búsqueda (sin web)
```bash
python3 demo_search.py --nombre "JUAN PEREZ" --cedula 1234567890
```

---

## Configuración

| Archivo | Para qué |
| --- | --- |
| `.env` | Secretos (API keys de solvers, credenciales). Nunca se versiona. |
| `config.yaml` | Ajustes no secretos: solver activo, puerto, TTLs de listas, API. |

Variables de entorno principales (todas opcionales; ver `.env.example`):

| Variable | Descripción |
| --- | --- |
| `TWOCAPTCHA_API_KEY` / `CAPSOLVER_API_KEY` | Solvers de reCAPTCHA/hCaptcha |
| `ANTHROPIC_API_KEY` | Solver de trivia con LLM |
| `VERIFYDATA_API_KEYS` | Claves de la API REST (prod) |
| `VERIFYDATA_ENV` | `dev` habilita API abierta |
| `VERIFYDATA_ENTITY_NIT` / `VERIFYDATA_ENTITY_NAME` | Entidad consultante en formularios que la exigen |
| `PORT` / `HOST` | Puerto/host del servidor (default 5070) |

---

## Tests

```bash
python3 tests/test_phase1.py     # infra + fuentes base
python3 tests/test_phase2.py     # registry + fuentes sin captcha
python3 tests/test_phase3.py     # captcha + trivia solver
python3 tests/test_estados.py    # estados, datasets y PDF
python3 tests/test_phase456.py   # integración fases 4-6
```

---

## Estructura

```
busqueda_datos/
├── app.py               # Webapp Flask (entry point)
├── api.py               # API REST /api/v1
├── auth.py              # Autenticación OTP/SSO + RBAC
├── runs.py              # Búsquedas progresivas (subprocesos + streaming)
├── report.py            # Generador de PDF (reportlab)
├── db.py                # SQLite/PostgreSQL
├── config.py            # Cargador de config.yaml + entorno
├── sources/             # 68 fuentes públicas (una clase por fuente)
├── solvers/             # Solvers de captcha (cadena con respaldo)
├── lists/               # Descarga y cache de listas bulk (OFAC, ONU, ...)
├── tests/               # Suite de tests
├── static/              # Assets (fuentes tipográficas, banda del PDF)
├── data/                # SQLite + caches (se crea en runtime)
└── requirements.txt
```

Documentación adicional:

- [API.md](API.md) — referencia de la API REST.
- [DEPLOYMENT.md](DEPLOYMENT.md) — despliegue como servicio.
- [AGENTS.md](AGENTS.md) — manual técnico para mantener/extender el código.
- [CHANGELOG.md](CHANGELOG.md) — historial de versiones.

---

## Licencia

Privado. © 2026 VerifyData.
