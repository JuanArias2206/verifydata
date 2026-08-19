# VerifyData API — Referencia

API REST para consultar personas y entidades en **70 fuentes públicas**
colombianas e internacionales (antecedentes, sanciones, PEP, prófugos) como
servicio. Producto de **VerifyData**.

- **Base URL:** `http://<host>:<port>/api/v1`
- **Documentación interactiva (Swagger UI):** `/api/v1/docs`
- **Especificación OpenAPI 3.1:** `/api/v1/openapi.json`
- **Formato:** JSON en peticiones y respuestas (UTF-8).

La API se monta como un Blueprint independiente sobre la misma app Flask; la
interfaz web HTML sigue disponible en `/`. El motor de búsqueda, la
concurrencia multiusuario y el caché de resultados se reutilizan tal cual
(ver [`runs.py`](runs.py)).

---

## 1. Autenticación

Autenticación por **API key**. Envíe la clave en una de estas cabeceras:

```
X-API-Key: <clave>
# o bien
Authorization: Bearer <clave>
```

Las claves válidas se configuran en [`config.yaml`](config.yaml) → `api.keys`:

```yaml
api:
  enabled: true
  keys:
    - "vd_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Genere una clave nueva:

```bash
python3 -c "import secrets; print('vd_live_' + secrets.token_urlsafe(32))"
```

- Si `api.keys` está **vacío**, la API queda **abierta** (solo para desarrollo).
- Endpoints **públicos** (nunca requieren clave): `/health`, `/sources`,
  `/docs`, `/openapi.json`.
- Endpoints **protegidos**: todos los `/searches*`.

Sin clave válida en un endpoint protegido → `401 unauthorized`.

---

## 2. Modelos de uso

### Asíncrono (recomendado para "todas las fuentes")

Las fuentes se consultan en paralelo; las pesadas (navegador/captcha) pueden
tardar entre 70 y 260 s. El modo asíncrono no bloquea:

1. `POST /searches` → responde `202` con un `token`.
2. Haga *polling* a `GET /searches/{token}` hasta que `status == "completed"`.
   Cada respuesta trae los resultados **parciales** disponibles hasta el
   momento (útil para ir mostrando fuentes a medida que terminan).

### Síncrono

`POST /searches/sync` ejecuta la búsqueda y **bloquea** hasta que termina o
hasta agotar `api.sync_timeout` segundos (por defecto 240). Si expira, devuelve
`200` con `status: "running"` y los resultados parciales; continúe por polling
con `links.self`. Ideal para subconjuntos pequeños de fuentes.

---

## 3. Endpoints

| Método | Ruta | Auth | Descripción |
| ------ | ---- | :--: | ----------- |
| `GET`  | `/health` | — | Estado del servicio. |
| `GET`  | `/sources` | — | Catálogo de fuentes (filtros `category`, `captcha`). |
| `POST` | `/searches` | ✔ | Inicia búsqueda asíncrona → `202` + token. |
| `POST` | `/searches/sync` | ✔ | Búsqueda síncrona (bloqueante con timeout). |
| `GET`  | `/searches/{token}` | ✔ | Estado + resultados (polling). |
| `GET`  | `/searches/{token}/report` | ✔ | Descarga el reporte PDF. |
| `GET`  | `/openapi.json` | — | Especificación OpenAPI 3.1. |
| `GET`  | `/docs` | — | Swagger UI. |

### Cuerpo de una búsqueda

```jsonc
{
  "nombre": "Juan Pérez Gómez",   // al menos uno de nombre/cedula es obligatorio
  "cedula": "1234567890",
  "fecha_exp": "2005-04-12",       // opcional; requerido por algunas fuentes
  "sources": "all"                  // "all" | "featured" | ["Fuente A", "Fuente B"]
}
```

- `sources: "all"` (por defecto) consulta las 70 fuentes.
- `sources: "featured"` consulta solo las fuentes principales.
- `sources: [...]` consulta un subconjunto exacto (nombres tal como aparecen en
  `GET /sources`; nombres desconocidos → `422 unknown_sources`).

---

## 4. Esquema de respuesta (SearchRun)

```jsonc
{
  "token": "a1b2c3d4e5f6",
  "status": "completed",              // "running" | "completed"
  "query": { "nombre": "...", "cedula": "...", "fecha_exp": "..." },
  "started_at": "2026-07-03 10:15:00",
  "progress": { "total": 70, "completed": 70, "pending": 0 },
  "summary": {
    "sources_total": 70,
    "matches": 3,
    "captcha_blocked": 1,
    "errors": 2
  },
  "results": [
    {
      "source": "OFAC — Sanctions List Search (form web oficial)",
      "category": "Sanciones internacionales",
      "source_url": "https://sanctionssearch.ofac.treas.gov/",
      "matched": true,
      "state": "match",               // ver tabla de estados abajo
      "status": "match_exacto",       // estado fino de la fuente (o null)
      "confidence": "exacta",         // "exacta" | "fuerte" | "posible" | null
      "summary": "Coincidencia en lista SDN",
      "matched_name": "PEREZ GOMEZ, Juan",
      "matched_document": "1234567890",
      "role": "SDN",
      "case_number": null,
      "dataset_version": "2026-07-01",
      "dataset_records": 12873,
      "error": null,
      "error_type": null,
      "notice": null,
      "requires_manual_review": false,
      "notes": null,
      "evidence_urls": ["https://..."],
      "download_url": null,
      "elapsed_s": 4.21,
      "details": [ { "campo": "valor" } ]
    }
  ],
  "links": {
    "self": "/api/v1/searches/a1b2c3d4e5f6",
    "report_pdf": "/api/v1/searches/a1b2c3d4e5f6/report"
  }
}
```

### Estados por fuente (`state`)

`state` es un estado normalizado y estable. Prioridad de cálculo:
`captcha_required` → `error` → estado fino de la fuente (`status`) →
`match`/`no_match`.

| `state` | Significado |
| ------- | ----------- |
| `match` | Coincidencia encontrada. |
| `no_match` / `nomatch_verified` | Sin coincidencia (consulta completada). |
| `captcha_required` / `captcha_blocked` | Captcha/WAF impidió automatizar. |
| `error` | Error técnico (ver `error` / `error_type`). |
| `timeout` | La fuente no respondió a tiempo. |
| `dataset_missing` / `dataset_stale` | Lista local ausente o vencida. |
| `not_implemented` / `requires_login` / `requires_payment` | No automatizable sin más. |
| `source_changed` | La estructura de la fuente cambió y el parser falló. |

> **error ≠ no_match:** un error técnico nunca se reporta como "sin
> coincidencia". Si `requires_manual_review: true`, el veredicto **no** es
> automático y debe revisarse manualmente.

### Envelope de error

```json
{ "error": { "code": "unknown_sources", "message": "…", "unknown": ["…"] } }
```

Códigos HTTP: `400` cuerpo inválido · `401` sin auth · `404` token inexistente ·
`422` parámetros inválidos · `500` error interno.

---

## 5. Ejemplos (curl)

```bash
BASE=http://localhost:5070/api/v1
KEY=vd_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Salud (público)
curl $BASE/health

# Catálogo de fuentes que NO requieren captcha
curl "$BASE/sources?captcha=false"

# Búsqueda ASÍNCRONA
TOKEN=$(curl -s -X POST $BASE/searches \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"nombre":"Juan Pérez","cedula":"1234567890"}' | jq -r .token)

# Polling hasta completar
curl -s $BASE/searches/$TOKEN -H "X-API-Key: $KEY" | jq '.status, .progress'

# Descargar el PDF cuando termine
curl -s $BASE/searches/$TOKEN/report -H "X-API-Key: $KEY" -o reporte.pdf

# Búsqueda SÍNCRONA sobre un subconjunto
curl -s -X POST $BASE/searches/sync \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"cedula":"1234567890","sources":["OFAC — Sanctions List Search (form web oficial)"]}' \
  | jq '.summary, .results[0].state'
```

### Ejemplo (Python)

```python
import requests, time

BASE = "http://localhost:5070/api/v1"
H = {"X-API-Key": "vd_live_..."}

# Iniciar
r = requests.post(f"{BASE}/searches", headers=H,
                  json={"nombre": "Juan Pérez", "cedula": "1234567890"})
token = r.json()["token"]

# Polling
while True:
    run = requests.get(f"{BASE}/searches/{token}", headers=H).json()
    if run["status"] == "completed":
        break
    time.sleep(3)

matches = [x for x in run["results"] if x["matched"]]
print(f"{len(matches)} coincidencias de {run['summary']['sources_total']} fuentes")
```

---

## 6. Configuración (`config.yaml → api`)

| Clave | Defecto | Descripción |
| ----- | ------- | ----------- |
| `enabled` | `true` | Si `false`, no se monta el Blueprint. |
| `keys` | `[]` | Claves válidas. Vacío = abierto (solo dev). |
| `sync_timeout` | `240` | Timeout (s) del endpoint síncrono. |
| `sync_poll_interval` | `1.0` | Intervalo de sondeo interno del síncrono. |
| `cors_origins` | `"*"` | Origen(es) CORS. Vacío = sin CORS. |

Gobernadores de concurrencia (variables de entorno, compartidos con la webapp):
`VERIFYDATA_MAX_CONCURRENCY`, `VERIFYDATA_MAX_BROWSER`, `VERIFYDATA_PER_RUN_WORKERS`,
`VERIFYDATA_CACHE_TTL_S`, `VERIFYDATA_HTTP_THREADS`.

---

## 7. Despliegue como servicio

```bash
pip install -r requirements.txt        # incluye waitress (WSGI de producción)
HOST=0.0.0.0 PORT=5070 python3 app.py   # sirve UI + API con waitress
```

- Configure `api.keys` antes de exponer públicamente.
- Ponga la app detrás de un reverse proxy (nginx/Caddy) con TLS.
- Suba el timeout de proxy si usa `/searches/sync` con muchas fuentes; para el
  modo asíncrono, el timeout de proxy solo afecta cada petición corta de
  polling.
