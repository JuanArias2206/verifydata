# Despliegue de VerifyData como servicio

Guía para subir el proyecto a un repositorio y desplegarlo como servicio
público (UI web + API REST `/api/v1`). La **autenticación de la API se activará
más adelante**; esta guía deja todo preparado para ello.

---

## 1. Antes de subir al repositorio

Todo lo sensible y pesado ya está en `.gitignore`. **Verifique** que no se
suban secretos ni datos:

```bash
git status --ignored           # config.yaml, .env, data/ deben salir "ignored"
git ls-files | grep -E "config.yaml$|\.env$|data/" && echo "⚠️ REVISAR" || echo "OK"
```

Lo que **sí** se versiona: código, `config.example.yaml`, `.env.example`,
`requirements.txt`, `Procfile`, `data/.gitkeep`.

Lo que **nunca** se versiona: `config.yaml` (claves reales), `.env`,
`data/` (BD, listas, capturas, certificados), reportes generados, `browsers/`.

> **Importante:** el `config.yaml` actual contiene claves reales de CapSolver,
> 2captcha, Webshare y Anthropic. Al estar en `.gitignore` no se subirá, pero
> **conviene rotarlas** si el archivo estuvo alguna vez en un repo o disco
> compartido, y usar de aquí en adelante variables de entorno.

---

## 2. Configuración en el servidor

Los secretos se inyectan por **variables de entorno** (12-factor); el entorno
tiene prioridad sobre `config.yaml`.

```bash
cp config.example.yaml config.yaml      # ajustes NO secretos (puertos, TTL…)
cp .env.example .env                     # rellenar con las claves reales
```

`.env` (o el entorno del sistema/Docker) provee:

| Variable | Uso |
| -------- | --- |
| `ANTHROPIC_API_KEY` | Claude Haiku (trivia/captcha LLM) |
| `CAPSOLVER_API_KEY` | Solver reCAPTCHA primario |
| `TWOCAPTCHA_API_KEY` | Solver de respaldo |
| `WEBSHARE_API_KEY` | Proxies residenciales (2captcha) |
| `HOST` / `PORT` | Bind del servidor (use `HOST=0.0.0.0`) |
| `VERIFYDATA_API_KEYS` | Claves de la API (coma-separadas). Vacío = abierta |
| `VERIFYDATA_DB_PATH` | Ruta alternativa de la BD (opcional) |
| `VERIFYDATA_RETENTION_HOURS` | Retención de capturas (0 = desactivar) |

---

## 3. Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install --with-deps chromium   # navegador para fuentes JS
```

`--with-deps` instala las librerías de sistema que Chromium necesita en
servidores Linux headless.

---

## 4. Arranque

`app.py` sirve **UI web + API** en el mismo proceso y usa **waitress**
(servidor WSGI de producción) si está instalado:

```bash
HOST=0.0.0.0 PORT=5070 python3 app.py
```

- UI:        `http://<host>:5070/`
- API:       `http://<host>:5070/api/v1`
- API Docs:  `http://<host>:5070/api/v1/docs`
- Health:    `http://<host>:5070/api/v1/health`

### systemd (ejemplo)

```ini
[Unit]
Description=VerifyData
After=network.target

[Service]
WorkingDirectory=/opt/verifydata/demo
EnvironmentFile=/opt/verifydata/.env
ExecStart=/opt/verifydata/.venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

> Aunque se define `WorkingDirectory`, las rutas de datos y BD se resuelven
> **relativas al proyecto** (no al CWD), así que el servicio funciona igual
> desde cualquier directorio.

---

## 5. ⚠️ Proceso ÚNICO (no escalar con múltiples workers)

El estado de las búsquedas (tokens, runs, caché de resultados) y los
**gobernadores de concurrencia** viven **en memoria del proceso**
(ver [`runs.py`](runs.py)). Por eso:

- ✅ Ejecute **una sola instancia/proceso** con múltiples **hilos** (así lo
  hace waitress: `VERIFYDATA_HTTP_THREADS`, por defecto 16). Atiende muchos
  usuarios concurrentes sin problema.
- ❌ **No** use varios workers de proceso (p. ej. `gunicorn -w 4`): un token
  creado en un worker no sería visible desde otro y el polling fallaría.
  Si usa gunicorn, hágalo con **un único worker** multihilo:
  `gunicorn -w 1 --threads 16 -t 300 app:app`.
- Para escalar horizontalmente en el futuro haría falta mover el estado a un
  almacén compartido (Redis) — hoy no es necesario.

---

## 6. Reverse proxy (TLS)

Ponga nginx/Caddy delante para TLS y dominio. Suba el timeout del proxy si
usará el endpoint **síncrono** con muchas fuentes (una búsqueda completa puede
tardar minutos). El modo **asíncrono** (token + polling) no lo necesita: cada
petición de polling es corta.

```nginx
location / {
    proxy_pass http://127.0.0.1:5070;
    proxy_read_timeout 300s;      # para /api/v1/searches/sync
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

---

## 7. Warm-up de listas (primer arranque)

En un servidor nuevo la BD (`data/verifydata.db`) empieza vacía. Las fuentes de
listas (OFAC, UN, EU, BIS, Banco Mundial…) reportarán `dataset_missing` hasta
que se descarguen. Pobléelas una vez:

```bash
curl -X POST http://<host>:5070/api/refresh-lists
```

Programe además un refresco periódico (cron semanal) para mantenerlas al día.
`GET /api/lists-inventory` muestra el estado y fecha de cada lista.

---

## 8. Datos, descargas y capturas

- **Directorios**: `data/`, `data/screenshots/`, `data/certs/` y `data/lists/`
  se crean automáticamente al arrancar (ver [`maintenance.py`](maintenance.py)).
- **Descargas/evidencias**: se sirven por la ruta pública `/download/<path>`
  (con protección contra *path traversal*). La API ya devuelve los campos
  `download_url` y `evidence_urls` como URLs `/download/...` listas para
  descargar por el cliente.
- **Retención**: un hilo daemon borra capturas/certificados más antiguos que
  `VERIFYDATA_RETENTION_HOURS` (72 h por defecto) para no llenar el disco. Ajuste
  o desactive (`0`) según su volumen. Considere montar `data/` en un volumen
  persistente con espacio suficiente.

---

## 9. Checklist de salida a producción

- [ ] `git status --ignored` no muestra `config.yaml` ni `.env` como versionados.
- [ ] Claves reales solo en `.env` / entorno; rotadas si estuvieron expuestas.
- [ ] `playwright install --with-deps chromium` ejecutado.
- [ ] `HOST=0.0.0.0` y un único proceso (waitress) detrás de TLS.
- [ ] `POST /api/refresh-lists` ejecutado y cron de refresco programado.
- [ ] `GET /api/v1/health` responde `200`.
- [ ] Volumen persistente para `data/` con retención configurada.
- [ ] (Más adelante) definir `VERIFYDATA_API_KEYS` para activar autenticación.
