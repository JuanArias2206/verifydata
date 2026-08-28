# Prompt para Claude — Auditoría Profunda VerifyData (50 hallazgos)

> Copia este prompt tal cual en Claude Code / Claude.ai. Incluye rutas y contexto completo.

---

## PROMPT (copiar desde aquí)

```
Eres auditor senior full-stack para VerifyData — SaaS Flask riesgo crediticio Colombia (68 fuentes + RSales + Excel BITACORA). Haz auditoría profunda, sin inventar, solo sobre el diff y archivos reales.

Contexto:
- Stack: Python 3.11, Flask 3, Playwright sync, reportlab, SQLite (/tmp en Vercel) / Postgres via DATABASE_URL, Vercel serverless (vercel.json, api/index.py), Google Sheets.
- Repo root: /Users/juanmanuelarias/Documents/trabajo/naprolab/busqueda_datos (o el path donde estés). Entry: app.py (puerto 5080 local, 5080 portal), config.py, db.py, credit_report.py, rsales_client.py, sheets_sync.py, sources/*, solvers/*, ui_theme.py.
- Roles: naprolab/naprolab -> ejecutivo, jefecartera/jefecartera123 -> jefe_cartera (admin). Auth simple por sesión en app.py:98-128, LOGIN_DISABLED bypass solo si VERIFYDATA_ENV != production a menos que ALLOW_ANON=1.
- Rutas clave a auditar:
  GET /                    -> index, redirect a /login?next=/ si no auth, muestra form persona/NIT
  GET /login + POST /login -> auth simple, preserva ?next, sanitiza open redirect
  GET /logout, GET /auth/* (OTP/SSO legacy deshabilitado), GET /auth/admin/users (RBAC)
  GET /credito             -> CREDITO_TEMPLATE form financiero + 6 file inputs (name=doc_*)
  POST /api/credit/full-check -> multipart (FormData) y JSON legacy, guarda en DATA/credit_docs/<token>/ + b64, build_credit_profile, 8 fuentes antecedentes paralelas, score 0-1000, retorna result_token
  GET /credit/results/<token> -> RESULTS_TEMPLATE render JS var DATA=result_json, try/catch, extra-email solo jefe
  GET /download/credit-pdf/<token> -> generate_credit_pdf(result) + pypdf merge anexos PDF
  POST /api/credit/send-email -> adjunta PDF mergeado + anexos (disco o b64), inyecta nota, SMTP_HOST/PORT/USERNAME/PASSWORD
  GET /cartera             -> CARTERA_TEMPLATE, is_ephemeral banner si SQLite en prod, fallback a 20 filas Excel BITACORA si credit_requests vacío, filtros, KPIs, historial modal
  POST /api/credit/approve, /reject, /revert, /toggle -> 403 si ejecutivo, audit log
  GET /api/credit/history/<id>, GET /api/credit/profile/<cedula>, GET /api/credit/warm-rsales, /api/sheets/sync, /api/sheets/status
  GET /api/v1/* (api_routes.py) -> API REST versionada con X-API-Key, /api/v1/health, /sources, /searches
  GET /download/<path:filename> -> sirve DATA/<path> con mime
  GET /api/run/<token>, /api/nit/<token>, /results/<token> -> runs progresivos
- Vercel: vercel.json rewrites /api/* y /* a api/index,functions api/index.py memory 1024 maxDuration 60, DATA get_data_path() -> /tmp/data en prod, credit_results en SQLite/Postgres.
- Tests: tests/test_phase*.py, LOGIN_DISABLED=1 para bypasear auth en tests.

Tarea:
1. Revisa el diff actual y los archivos listados. No inventes rutas fuera del repo.
2. Detecta 50 hallazgos accionables, priorizados por criticidad. Para cada uno indica: archivo:línea aproximada, gravedad (CRITICA/ALTA/MEDIA/BAJA), categoría (Seguridad/Arquitectura/Performance/Correctitud/UX/Calidad/DevOps), descripción, impacto, fix concreto con snippet o pasos.
3. Seguridad: auth bypass, sesión (SESSION_SECRET efímero), CSRF, XSS (esc en templates inline), validación entradas, manejo secretos (.env, service account), RBAC, rate limit, manejo PII cédula en logs/URL/filename.
4. Arquitectura: DB efímera /tmp vs Postgres, manejo anexos b64 vs Blob, Playwright en serverless, manejo de creds Sheets, manejo de errores safe_fetch -> Hit.error, solver chain.
5. Performance: RSales get_all_customers paginado, antecedentes 8 fuentes ThreadPool, PDF merge pypdf, b64 en DB, etc.
6. Correctitud: validación tipos (FormData strings vs ints), manejo None, cálculo score/monto, cotejo RSales vs Excel, manejo de fechas, etc.
7. UX: blank results, hang 60s, preview docs, extra-email, cartera filtros, responsive, accesibilidad.
8. Calidad: lint, tests coverage, tipado, manejo excepciones, logging, audit log.
9. DevOps: Vercel MCP (opencode.json), GitHub Actions (opencode-review.yml, quality.yml) con ANTHROPIC_API_KEY, env vars por environment (Production/Preview).

Formato de salida:
- Tabla o lista numerada 1-50 con columnas: # | Archivo:Línea | Gravedad | Categoría | Hallazgo | Fix
- Al final: resumen de 5 bloqueantes que impiden pasar a prod y checklist de 10 quick wins.
- No repitas hallazgos, no inventes archivos inexistentes. Cita rutas reales.

Empieza auditando app.py, credit_report.py, rsales_client.py, db.py, api/index.py, vercel.json, ui_theme.py, sheets_sync.py, sources/base.py, tests/*, opencode.json, .github/workflows/*.
```

---

## 50 hallazgos — verificado contra el código real (main, 2026-08-27)

> Actualización sobre el draft anterior: varios hallazgos previos ya están corregidos en `main`
> (bypass de `LOGIN_DISABLED` en prod, `is_postgres()` implementado, `api/index.py` ya usa
> `setdefault`, sidebar de Cartera deduplicado). Esta tabla refleja el estado **actual**, verificado
> línea por línea, no una repetición del prompt.

### 🚫 Bloqueantes para producción (1-5)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 1 | `app.py:223-230` | CRITICA | Seguridad | El login tiene un fallback hardcodeado: si `username=="naprolab" and password=="naprolab"` entra como ejecutivo **sin importar** lo que valgan `VERIFYDATA_USER`/`VERIFYDATA_PASS` en producción. Rotar la contraseña en Vercel no cierra el acceso demo. | Eliminar el fallback o condicionarlo a `VERIFYDATA_ENV != "production"`. Usar `werkzeug.security.check_password_hash` en vez de `==`. |
| 2 | `app.py:57-63` | CRITICA | Seguridad | `SESSION_SECRET` se genera con `token_hex` si no está en env (`app.secret_key`). En serverless cada instancia fría genera un secreto distinto → sesiones inválidas de forma intermitente y cookies firmadas con claves distintas por instancia. | Fijar `SESSION_SECRET=$(openssl rand -hex 32)` en Vercel (Production **y** Preview). |
| 3 | `db.py` (backend SQLite) + `api/index.py:29-38` + `app.py:2305` | CRITICA | Arquitectura | Sin `DATABASE_URL`, `set_db_path(/tmp/verifydata.db)` usa SQLite en `/tmp`, efímero por instancia lambda. `credit_requests`/`credit_results` se pierden entre cold starts → `/cartera` cae al fallback de 20 filas del Excel BITACORA (`app.py:2305,2409`) y `/credit/results/<token>` puede dar 404 si el token se generó en otra instancia. | Crear Vercel Postgres (o Neon) y setear `DATABASE_URL` en Production+Preview. `db.py` ya soporta `is_postgres()`; correr `migrate_sqlite_to_pg.py`. |
| 4 | `app.py` — `/login`, `/api/credit/full-check`, `/api/credit/approve|reject|revert|toggle`, `/api/credit/send-email` | CRITICA | Seguridad | Ninguna ruta `POST` tiene protección CSRF. No hay `Flask-WTF`, ni token de doble-submit; solo hay cookie de sesión (sin `SameSite` explícito visible en el código de sesión). | Añadir `Flask-WTF` (`CSRFProtect(app)`) o un token `csrf_token` en sesión + campo hidden, validado en `before_request` para métodos mutantes. |
| 5 | `app.py:3600-3776` (`api_credit_send_email`) | CRITICA | Seguridad | El endpoint no valida el rol del usuario autenticado antes de aceptar el campo `emails` del body JSON — la restricción "solo Jefe/Admin" (`app.py:4086`) es **solo de UI** (input oculto en frontend). Cualquier usuario con sesión (rol `ejecutivo`) puede llamar el endpoint directo con un `email` arbitrario y recibir el PDF + PII del reporte. | En `api_credit_send_email`, leer `g.user["rol"]`; si hay un email en `data["emails"]` que no sea el/los destinatarios por defecto y el rol no es `jefe_cartera`/`admin`, rechazar con 403. |

### Seguridad (6-19)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 6 | `app.py:203-230` | ALTA | Seguridad | Comparación de contraseña en texto plano (`password == p`), sin hash, sin límite de intentos ni backoff. Fuerza bruta viable contra `/login`. | `generate_password_hash`/`check_password_hash`; contador de intentos fallidos por IP+usuario con backoff (ej. Flask-Limiter). |
| 7 | `app.py` (config Flask, cerca de `app = Flask(...)`) | ALTA | Seguridad | No hay `app.config["MAX_CONTENT_LENGTH"]`. `/api/credit/full-check` acepta 6 archivos sin límite de tamaño total — DoS de memoria/disk fácil. | `app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024` y capturar `413` con mensaje claro. |
| 8 | `app.py:3694-3711` | ALTA | Seguridad | `msg["Subject"]` y `pdf_name` interpolan `result.get('nombre')` sin sanitizar `\r\n`; `msg["To"] = ", ".join(destinatarios)` tampoco valida formato de email server-side. Riesgo de inyección de cabeceras SMTP/MIME si `nombre` o `emails` llegan manipulados (mismo payload del punto 5). | `re.sub(r"[\r\n]", "", nombre)` antes de usarlo en headers; validar cada email de `destinatarios` con regex estricta y rechazar el request si no matchea. |
| 9 | `app.py:1475-1478` (`download`) | MEDIA | Seguridad | El chequeo anti-traversal usa `str(full).startswith(str(DATA.resolve()))`, vulnerable a bypass por directorio hermano (`/x/data` vs `/x/data-evil`) porque compara strings sin separador. | Usar `full.resolve().is_relative_to(DATA.resolve())` (Python 3.9+, ya en uso: 3.11). |
| 10 | `app.py:265-275` (`_CSP`) | MEDIA | Seguridad | CSP permite `'unsafe-inline'` en `script-src` y `style-src` porque toda la UI es HTML+JS inline en `app.py`. Anula gran parte del valor de la CSP contra XSS. | Migrar templates a archivos estáticos + usar `nonce` por request (Flask puede inyectar `{{ csp_nonce }}` vía `context_processor`). |
| 11 | `auth.py:85-87` | MEDIA | Seguridad | `OTP_ALLOWED_DOMAINS` está vacío por defecto → si la ruta OTP/SSO legacy se reactiva, acepta cualquier dominio de correo. | Fijar `OTP_ALLOWED_DOMAINS=naprolab.com` (o el dominio corporativo) en Vercel/`.env` de producción. |
| 12 | `api_routes.py:93-99` | BAJA | Seguridad | `provided in keys` es una comparación no constante-en-tiempo; con múltiples claves configuradas hay un canal lateral de timing (bajo impacto práctico, pero barato de arreglar). | Iterar claves con `secrets.compare_digest(provided, k)` en vez de `in`. |
| 13 | `.env.example:22` | BAJA | Seguridad/Privacidad | `GOOGLE_SHEETS_ID=1nBCNIaI_V78SQeyOZRDrhet0Oiw_I5FZsHk92aKIkEE` — el ID real de la hoja de producción está committeado como valor "de ejemplo", no un placeholder genérico. | Reemplazar por `GOOGLE_SHEETS_ID=` (vacío) o `<tu-sheet-id>` en el `.example`. |
| 14 | `.gitignore` | BAJA | Seguridad | No hay una regla explícita para `credentials/*.json` o `*.serviceaccount.json` (hoy no existe la carpeta, pero nada impide que alguien la cree y la commitee por accidente). | Añadir `credentials/` y `*serviceaccount*.json` al `.gitignore` como defensa en profundidad. |
| 15 | `app.py:3646-3689` | MEDIA | Privacidad/PII | El correo con nombre, cédula/NIT y score se puede enviar a un destinatario arbitrario por el gap del punto 5, exponiendo PII financiera bajo Ley 1581/2012. | Se resuelve junto con el fix del punto 5 (RBAC server-side) + whitelist de dominios de destino. |
| 16 | `app.py:1102,1530` (llamadas a `audit(...)`) | MEDIA | Privacidad | `audit()` registra la cédula/NIT en claro en el log JSON de auditoría (`logging_config.py`). Cumple el propósito de trazabilidad AML pero sin hashing, cualquier acceso al log expone PII directamente. | Guardar `cedula_hash = sha256(cedula).hexdigest()[:16]` junto al valor claro solo si se requiere, o solo el hash si el log sale de un entorno controlado. |
| 17 | `app.py:172-174` (`LOGIN_TEMPLATE`) | MEDIA | Seguridad | Las credenciales demo (`naprolab/naprolab`, `jefecartera/jefecartera123`) se muestran siempre en la pantalla de login, incluida producción — no está condicionado a `VERIFYDATA_ENV != production`. | Envolver ese bloque en `{% if not is_prod %}` pasando `is_prod` al template. |
| 18 | `app.py:3710-3711` | BAJA | Seguridad | `pdf_name` se arma con `result.get('nombre','cliente').replace(' ','_')` y se inserta sin comillas en `Content-Disposition: attachment; filename=...` — un nombre con `"` o `;` podría romper el header. | `werkzeug.utils.secure_filename(pdf_name)` antes de usarlo en el header, o usar `email.utils.encode_rfc2231`. |
| 19 | `sheets_sync.py:38-48` | BAJA | Seguridad | Cuando `GOOGLE_SHEETS_CREDENTIALS` es un JSON string, se escribe a un archivo temporal con `delete=False` y nunca se borra — el service account queda en `/tmp` mientras viva el contenedor. | Usar `delete=True` con un context manager que mantenga el archivo abierto durante la llamada, o borrar explícitamente tras `build('sheets', ...)`. |

### Arquitectura (20-27)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 20 | `app.py:3176-3197` (`_save_credit_attachments`) | ALTA | Arquitectura | Cada anexo <4MB se guarda también en base64 dentro del propio registro (`entry["b64"]`), que termina en `credit_results.result` (JSON/JSONB). Con 6 anexos, una sola evaluación puede pesar >20MB en la fila. | Migrar anexos a **Vercel Blob** (o S3) y guardar solo `blob_url` + `sha256` en el JSON; eliminar el campo `b64` del payload persistido. |
| 21 | `sources/_browser_helper.py:110-111` + `runs.py:363,622` (`skip_browser`) | ALTA | Arquitectura | Playwright (`sync_playwright()`) no funciona en Vercel serverless (sin Chromium empaquetado). Las fuentes que dependen de navegador solo se saltan si el caller pasa `skip_browser=True`; varias llamadas en `runs.py` (801, 804, 823, 826) usan `skip_browser=False`. | Detectar `VERIFYDATA_ENV=="production"` (Vercel) y forzar `skip_browser=True` automáticamente en ese entorno, o enrutar esas fuentes a un servicio externo tipo Browserless. |
| 22 | `vercel.json:6-9` | MEDIA | Arquitectura | `maxDuration: 60` para `api/index.py`. `full-check` combina RSales + 8 fuentes de antecedentes + generación de PDF; si RSales está lento, puede acercarse al límite sin un timeout parcial que devuelva resultado incompleto en vez de 504. | Subir a `maxDuration: 120` (requiere plan Pro) o añadir un `time.monotonic()` budget interno que corte antecedentes lentos y devuelva lo que haya, marcando las fuentes no completadas. |
| 23 | `requirements.txt` vs `requirements-vercel.txt` (diff) | MEDIA | Arquitectura/DevOps | `playwright`, `pdfplumber`, `msal`, `waitress`, `psycopg[binary,pool]` están fuera de `requirements-vercel.txt` a propósito (bundle size), pero no hay comentario en el archivo que lo explique — riesgo de que alguien los "arregle" agregándolos y rompa el build serverless (o los quite de `requirements.txt` sin darse cuenta de que rompe local/Postgres). | Añadir un comentario al inicio de `requirements-vercel.txt`: `# NO agregar playwright/waitress/msal — no funcionan o no aplican en Vercel serverless`. |
| 24 | `db.py` (rama `is_postgres()`) | BAJA | Arquitectura | El pool de `psycopg_pool.ConnectionPool` no fija explícitamente `min_size`/`max_size` acorde a los límites de conexión de Neon/Vercel Postgres en entorno serverless (riesgo de agotar conexiones con concurrencia de varias lambdas). | Configurar `ConnectionPool(..., min_size=1, max_size=3)` y usar el modo "pooled" (pgbouncer) de Neon si aplica. |
| 25 | `migrate_sqlite_to_pg.py` | BAJA | Arquitectura | El script de migración existe pero no hay paso de checklist/CI que confirme que se ejecutó tras crear `DATABASE_URL`. | Añadirlo al checklist de quick wins (ver abajo) y a `DEPLOYMENT.md`. |
| 26 | `sheets_sync.py:34-48` | BAJA | Arquitectura | Ya soporta credenciales vía JSON string en env (bien, ya resuelve el problema típico de Vercel), pero recrea el archivo temporal en cada invocación fría sin reutilizarlo — no es cacheable entre requests calientes del mismo contenedor. | Cachear la ruta del archivo temporal a nivel de módulo (`_cached_cred_path`) para no rehacer el `NamedTemporaryFile` en cada llamada dentro de la misma instancia. |
| 27 | `app.py` (4193 líneas) | BAJA | Arquitectura | Monolito: rutas, HTML inline (`LOGIN_TEMPLATE`, `TEMPLATE`, `RESULTS_TEMPLATE`, etc.) y lógica de negocio en un único archivo. No es bloqueante pero encarece cualquier cambio futuro. | Extraer a `routes/credit.py`, `routes/cartera.py`, `routes/auth_simple.py` y mover HTML a `templates/*.html` con Jinja real. |

### Performance (28-33)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 28 | `rsales_client.py:532,562,577-579` + `app.py:2756,3336` | ALTA (ya mitigado) | Performance | `find_customer_in_rsales(..., use_cache=False)` ya evita el fallback a `get_all_customers()` (10 páginas) en el camino caliente de `full-check` — el hang de 60s→~4-5s ya está resuelto. Queda pendiente documentar cuándo sí se usa el camino con cache (`use_cache=True` por defecto en la firma) para no reintroducir el problema en otra ruta. | Auditar todos los callers de `find_customer_in_rsales` sin `use_cache=False` explícito y decidir caso por caso; considerar loguear el cache-hit-rate. |
| 29 | `app.py:3431` (`ThreadPoolExecutor(max_workers=4)`) | MEDIA | Performance | Las 8 fuentes clave de antecedentes se procesan con solo 4 workers → 2 rondas secuenciales. En Vercel (`memory: 1024`) subir workers arriesga OOM si cada fuente usa Playwright/requests pesados. | Subir a `max_workers=8` solo si esas 8 fuentes son livianas (HTTP, no browser); medir memoria real en un run de prueba antes de subirlo en prod. |
| 30 | `credit_report.py` (sección de anexos-imagen, ~581-598) | MEDIA | Performance | Las imágenes anexas se incrustan en el PDF vía reportlab sin redimensionar (no hay `Pillow`/`Image.thumbnail`); una foto de 4000×3000 px infla el PDF y el tiempo de render. | Antes de incrustar, abrir con `PIL.Image`, `img.thumbnail((800,800))` y guardar a buffer JPEG con calidad ~80 antes de pasarlo a reportlab. |
| 31 | `credit_report.py:787-834` (merge pypdf) | MEDIA | Performance | El merge de anexos PDF se rehace completo en memoria (`BytesIO`) cada vez que se descarga o se envía por correo el mismo `token` — sin cache del PDF ya mergeado. | Cachear el PDF final mergeado (en disco `/tmp` o Blob) keyed por `token` + hash de anexos, invalidando si cambian los anexos. |
| 32 | `app.py:1974` (`fetch('/api/credit/full-check', ...)`) | BAJA | Performance/UX | El `fetch` no tiene `AbortController`/timeout en el cliente — si el backend cuelga, el spinner queda indefinidamente sin opción de cancelar. | Envolver el `fetch` con `AbortController` + `setTimeout(controller.abort, 90000)` y mostrar mensaje de "tardando más de lo esperado" a los 20s. |
| 33 | `db.py` (`init_db`) | BAJA | Performance | `init_db()` se invoca en cada cold start de `api/index.py` (correcto), pero dentro de un mismo contenedor caliente no hay guard para evitar rechequear el esquema en llamadas repetidas si algún caller la invoca de más. | Añadir un flag de módulo `_schema_ready` que se setee tras el primer `init_db()` exitoso también para SQLite (hoy solo aplicaría de forma implícita). |

### Correctitud (34-39)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 34 | `app.py:3549` | MEDIA | Correctitud | `calificacion=float(excel_data.get("calificacion_datacredito", 0) or 0)` — si el dato no existe, cae a `0`, el peor puntaje posible, en vez de un valor neutro o `None` explícito que el scoring pueda tratar como "sin dato". | `calificacion = excel_data.get("calificacion_datacredito"); calificacion = float(calificacion) if calificacion not in (None, "") else None` y ajustar `credit_risk.py` para tratar `None` como "sin datacrédito" (ya existe la rama `sin_datos`/`datos_parciales`). |
| 35 | `app.py:3322-3325` | MEDIA | Correctitud | `if excel_data.get(k) in (None, "", 0, False): excel_data[k] = v` trata un `0` legítimo del formulario (p. ej. `promedio_compras=0` en un cliente nuevo) como "vacío" y lo sobreescribe con el dato de Excel, perdiendo el valor real capturado en el formulario. | Distinguir explícitamente: `if excel_data.get(k) is None or excel_data.get(k) == "":` sin incluir `0`/`False` en la condición. |
| 36 | `app.py:3447-3463` | BAJA | Correctitud | La lista de `bloqueantes` está hardcodeada a 7 fuentes (OFAC SDN, OFAC consolidado, ONU, BIS, Banco Mundial, PEP, SECOP Multas). Si se agrega una fuente nueva marcada como "principal"/bloqueante en `antecedentes`, no aparece aquí automáticamente. | Generar `bloqueantes` iterando `antecedentes.items()` y filtrando por un atributo `is_bloqueante` definido en la fuente, en vez de una lista fija de nombres. |
| 37 | `credit_risk.py:334-348,391-394,444-447` | BAJA | Correctitud | Las ramas `sin_datos`/`datos_parciales` aplican ajustes en múltiples secciones del cálculo (historial, capacidad, mora); no hay un test que verifique que la suma de penalizaciones bajo esos casos no exceda los pesos de `WEIGHTS` (que suman 100). | Añadir un test unitario que construya un perfil `sin_datos=True` y verifique que el score final quede en un rango esperado, no que colapse a 0 por doble conteo. |
| 38 | `app.py` (≈44 bloques `except Exception` / `except Exception as e`) | BAJA | Correctitud | Manejo de excepciones muy genérico en varios puntos (ej. `credit_report.py:802-807` retorna el PDF base silenciosamente si el merge de anexos falla, sin marcarlo en la respuesta al usuario). | En los puntos donde el fallo es visible para el usuario (merge de anexos, envío de correo), propagar un flag `anexos_incompletos: true` en la respuesta para que la UI lo muestre. |
| 39 | `sheets_sync.py:29-31` (`is_configured`) | BAJA | Correctitud | Solo verifica que `SHEET_ID` y `CREDENTIALS_PATH` no estén vacíos, no que las credenciales sean válidas o que el Sheet exista — el error real solo aparece en el primer intento de sync. | Añadir un `GET /api/sheets/status` que intente un `spreadsheets.get` liviano y reporte "credenciales inválidas" antes del primer sync real. |

### UX / Accesibilidad (40-45)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 40 | `app.py:3928` (`var DATA = {{ result_json|safe }};`) | ALTA | UX | El resultado completo — incluyendo el `b64` de hasta 6 anexos — se embebe inline en el HTML de `/credit/results/<token>`. Con anexos grandes esto genera varios MB de HTML, parseo lento del navegador y riesgo de pantalla en blanco si el JSON se trunca. | Cargar el resultado vía `fetch('/api/credit/result/<token>')` de forma lazy (ya existe patrón similar en `/api/run/<token>`) en vez de inyectarlo inline; excluir `b64` de la respuesta usada para pintar la UI y servirlo solo bajo demanda (preview/descarga). |
| 41 | `app.py:1974` + spinner (`app.py:566`) | ALTA | UX | Durante `full-check` (que puede tardar varios segundos por RSales + 8 fuentes) solo se muestra un spinner genérico sin pasos ni porcentaje, y sin opción de cancelar. | Reusar el patrón de `/api/run/<token>` (polling progresivo) también para `full-check`, mostrando qué fuente se está consultando. |
| 42 | `app.py` (preview de documentos en `/credito`) | MEDIA | UX | La previsualización de adjuntos solo funciona para imágenes (`FileReader`); PDFs y Excel no muestran preview, solo el nombre del archivo. | Añadir un link "Ver PDF" que abra el archivo en una pestaña nueva usando una URL de objeto (`URL.createObjectURL`), sin necesidad de una librería de render. |
| 43 | `app.py:2360` área (botón "Sincronizar Sheets") | MEDIA | UX | El botón de sincronización no refleja el estado de `/api/sheets/status` (configurado/no configurado) ni el resultado tras el click (cuántas filas se exportaron). | Al cargar la página, hacer `GET /api/sheets/status` y pintar un badge; tras el click, mostrar un toast con el conteo devuelto por `/api/sheets/sync`. |
| 44 | `app.py:161-167` (`LOGIN_TEMPLATE`) | MEDIA | A11y | Los `<input>` de usuario/contraseña no tienen `<label for="...">` asociado (solo un `<label>` visual sin `for`/`id` correspondiente) ni `autocomplete="username"`/`"current-password"`. | Añadir `id="login-user"` al input y `for="login-user"` al label correspondiente (y análogo para password), más los atributos `autocomplete`. |
| 45 | CSS de animaciones (`ui_theme.py` / bloques `<style>` en `app.py`) | BAJA | A11y | Las transiciones/`@keyframes` (ej. fade-in de resultados) no respetan `prefers-reduced-motion`. | Envolver las animaciones en `@media (prefers-reduced-motion: no-preference) { ... }` o añadir una regla `reduce` que las desactive. |

### Calidad / DevOps (46-50)

| # | Archivo:Línea | Gravedad | Cat | Hallazgo | Fix |
|---|---|---|---|---|---|
| 46 | `tests/` (no existe `conftest.py`) | MEDIA | Calidad | No hay fixtures compartidas; cada `test_phase*.py` monta su propia DB temporal y depende de `LOGIN_DISABLED=1` seteado manualmente en el entorno (como en `.github/workflows/quality.yml`), sin un cliente Flask reutilizable logueado. | Crear `tests/conftest.py` con un fixture `client` que haga login programático (`session["verifydata_user"]=...`) sin depender de `LOGIN_DISABLED`. |
| 47 | `.github/workflows/quality.yml:24-35` | MEDIA | DevOps | Los pasos de tests y lint usan `|| true` (`python tests/test_phase1.py || true`, `flake8 ... || true`), por lo que el pipeline **nunca falla** aunque los tests o el lint reporten errores — falso verde permanente en el PR check. | Quitar `|| true` de los pasos de test; dejarlo solo (si acaso) en el paso de `pip install -r requirements-vercel.txt || true` mientras se estabiliza, pero no en tests/lint. |
| 48 | `app.py` (monolito, ver #27) | BAJA | Calidad | Sin tipado estático (`mypy`) ni chequeo en CI; `flake8` en `quality.yml` solo corre con `--select=E9,F63,F7,F82` (errores de sintaxis), no detecta imports no usados, complejidad, etc. | Añadir un paso opcional (`continue-on-error: true` en vez de `|| true` inline) con `flake8 --select=C901` o `ruff` para visibilidad, sin bloquear aún el merge. |
| 49 | `opencode.json` | MEDIA | DevOps | Solo el MCP `vercel` está habilitado. No hay MCP de `github` configurado, así que el review automatizado (`opencode-review.yml`) no puede comentar directamente en la PR ni leer más contexto del repo vía MCP. | Añadir un bloque `"github": {"type":"remote","url":"https://mcp.github.com", ...}` (o el equivalente de opencode) si se quiere que el reviewer comente inline. |
| 50 | `.github/workflows/opencode-review.yml:20` | BAJA | DevOps | El modelo está fijado a `anthropic/claude-sonnet-4-20250514` (versión superada) y el job es puramente informativo — no hay un paso que falle el check si el review detecta bloqueantes, dependiendo 100% de que un humano lea el comentario. | Actualizar el modelo a la versión vigente y, si se quiere gate real, hacer que el step falle (`exit 1`) cuando el output del reviewer contenga la palabra clave de bloqueante acordada (ej. `BLOQUEANTE:`). |

---

## Resumen — 5 bloqueantes que impiden pasar a prod

1. **Login con fallback hardcodeado** (`app.py:223-230`) — `naprolab/naprolab` entra siempre, ignorando `VERIFYDATA_USER`/`VERIFYDATA_PASS`.
2. **`SESSION_SECRET` efímero** (`app.py:57-63`) — sesiones inconsistentes entre instancias serverless.
3. **DB efímera sin Postgres** (`db.py`, `api/index.py:29-38`, `app.py:2305`) — Cartera y resultados se pierden entre cold starts.
4. **Sin CSRF en ninguna ruta POST** — login, full-check, approve/reject/revert/toggle, send-email.
5. **`send-email` sin RBAC server-side** (`app.py:3600-3776`) — cualquier usuario autenticado exfiltra el reporte a un correo arbitrario.

## Checklist — 10 quick wins (hazlos hoy)

1. Quitar/condicionar el fallback `naprolab/naprolab` en `app.py:229-230` a `VERIFYDATA_ENV != "production"`.
2. Fijar `SESSION_SECRET=$(openssl rand -hex 32)` en Vercel (Production **y** Preview).
3. Crear Vercel Postgres (o Neon) → setear `DATABASE_URL` → correr `migrate_sqlite_to_pg.py`.
4. Añadir chequeo de rol (`jefe_cartera`/`admin`) antes de aceptar `emails` extra en `api_credit_send_email` (`app.py:3600`).
5. Sanitizar `nombre`/`emails` con `re.sub(r"[\r\n]", "", ...)` antes de usarlos en headers de correo (`app.py:3694-3711`).
6. Añadir `app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024`.
7. Cambiar el chequeo de `/download` a `full.resolve().is_relative_to(DATA.resolve())` (`app.py:1478`).
8. Añadir `Flask-WTF` / CSRF token a `/login` y a las rutas mutantes de crédito y cartera.
9. Quitar `|| true` de los pasos de test en `.github/workflows/quality.yml`.
10. Ocultar el bloque de credenciales demo del `LOGIN_TEMPLATE` cuando `VERIFYDATA_ENV == "production"`.

## Prompt para Vercel MCP (para tu próxima sesión)

```
Usa Vercel MCP: revisa el último deployment de verifydata (projectId verifydata-psi), sus env vars (¿DATABASE_URL y SESSION_SECRET ya están seteadas en Production y Preview?), logs de build y runtime de /api/credit/full-check y /api/credit/send-email. Correlaciona con el diff de main vs origin/main y dime si los 5 bloqueantes de CLAUDE_AUDIT_PROMPT.md ya están resueltos en prod. Si no, indica el redeploy o cambio de env var exacto.
```
