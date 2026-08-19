# VerifyData Demo — Onboarding para el desarrollador receptor

## 1. ¿Qué es esto?

Es una webapp Flask que automatiza búsquedas en **68 fuentes públicas**
colombianas e internacionales (Registraduría, Policía, Contraloría,
Procuraduría, DIAN, OFAC, INTERPOL, Rama Judicial, etc.) para consultar
antecedentes, inhabilidades, PEP, listas restrictivas, procesos judiciales
y más.

El proyecto está empaquetado para correr **offline** (sin deploy) luego de
instalar dependencias y configurar las API keys de los servicios de captcha.

---

## 2. Stack técnico

| Componente       | Tecnología                              |
| ---------------- | --------------------------------------- |
| Lenguaje         | Python 3.11+                            |
| Web framework    | Flask 3 (templates inline en app.py)    |
| Captcha solvers  | CapSolver, 2captcha, Anthropic, trivia  |
| Browser          | Playwright (cromado)                    |
| PDF reports      | reportlab                               |
| Base de datos    | SQLite (`data/verifydata.db`)           |
| Config           | `config.yaml` + `.env`                  |

---

## 3. Cómo ponerlo a correr

### 3.1 Requisitos previos

- Python 3.11 o superior
- `pip` (o `uv`, o `poetry`)
- Conexión a internet (para descargar dependencias y hacer consultas)

### 3.2 Instalación

```bash
cd busqueda_datos

# Dependencias Python
pip install -r requirements.txt

# Playwright (navegador headless para fuentes con JavaScript)
python3 -m playwright install chromium
```

### 3.3 API keys (obligatorio para captchas)

Copia el archivo de ejemplo:

```bash
cp .env.example .env
```

Edita `.env` y completa **al menos** estas variables (sin ellas las
fuentes con captcha como Policía, Contraloría, Registraduría no
resolverán):

| Variable               | Qué es                   | Dónde conseguirla          |
| ---------------------- | ------------------------ | -------------------------- |
| `CAPSOLVER_API_KEY`    | reCAPTCHA solver         | https://capsolver.com      |
| `TWOCAPTCHA_API_KEY`   | Fallback reCAPTCHA       | https://2captcha.com       |
| `ANTHROPIC_API_KEY`    | LLM para trivia captcha  | https://console.anthropic.com |
| `WEBSHARE_API_KEY`     | Proxies residenciales    | https://webshare.io        |

> **Nota:** Si no tienes alguna de estas keys, las fuentes que dependan
> de ese solver mostrarán "Requiere solver" en el reporte, pero el resto
> de fuentes (la mayoría) seguirá funcionando.

### 3.4 Entidad consultante (Policía — Inhabilidades)

En el formulario de la Policía para consulta de inhabilidades por delitos
sexuales (Ley 1918), se envía el nombre y NIT de la **entidad que
consulta**. Esto está configurable en `.env`:

```
VERIFYDATA_ENTITY_NIT=9001234339
VERIFYDATA_ENTITY_NAME=NAPROLAB S.A.
```

Si el receptor necesita otra entidad, solo cambia esos valores.

### 3.5 Arrancar

```bash
python3 app.py
```

Abrir en el navegador: **http://127.0.0.1:5070**

La app inicia **sin login** (`LOGIN_DISABLED=1` por defecto). Si se
quiere autenticación OTP, quitar esa variable del `.env`.

---

## 4. Cómo se usa

1. Abrir http://127.0.0.1:5070
2. Ingresar **Nombres y Apellidos** y **Número de documento**
3. Opcional: **Fecha de expedición** (dd/mm/aaaa) — necesaria para
   algunas fuentes como Registraduría y Policía Inhabilidades
4. Click en **Buscar**
5. Esperar ~2-8 minutos mientras recorre 68 fuentes en paralelo
6. Ver resultados: coincidencias, avisos, errores
7. Descargar **PDF** del reporte

### Búsqueda por empresa (NIT)

Hay una pestaña "Empresa / NIT" que consulta RUES, SECOP, DIAN
Proveedores Ficticios y otras fuentes empresariales.

---

## 5. Estructura de archivos (lo esencial)

```
busqueda_datos/
├── app.py                 # Entry point de la webapp
├── config.yaml            # Config de solvers (no tocar)
├── .env                   # API keys y secrets (crear desde .env.example)
├── sources/               # Las 68 fuentes de datos
│   ├── policia_inhab.py   # Inhabilidades Ley 1918 (usa la entidad consultante de .env)
│   ├── procuraduria.py    # Ejemplo canónico de fuente con Playwright
│   ├── ...                # 66 fuentes más
├── solvers/               # Resolvedores de captcha
├── tests/                 # Tests unitarios
├── data/                  # Base de datos SQLite (se crea sola al arrancar)
├── requirements.txt       # Dependencias
└── AGENTS.md              # Manual completo del proyecto
```

---

## 6. Comandos útiles

### Probar que la DB está sana

```bash
python3 -c "import sqlite3; sqlite3.connect('data/verifydata.db').execute('SELECT 1') and print('OK')"
```

### Probar una fuente individual (sin webapp)

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from sources import registry
for s in registry.all_sources():
    if 'Registradur' in s.name:
        hit = s.fetch('JUAN PEREZ', '1192722347', '03/07/2020')
        print(hit)
        break
"
```

### Correr tests

```bash
python3 tests/test_phase1.py
python3 tests/test_estados.py
python3 tests/test_phase456_quick.py
```

---

## 7. Solución de problemas comunes

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| "Requiere solver de reCAPTCHA" | API key no configurada | Revisar `CAPSOLVER_API_KEY` en `.env` |
| "No se pudo inicializar TwoCaptchaSolver" | WEBSHARE_API_KEY faltante | Configurar en `.env` |
| Playwright error | Chromium no instalado | `python3 -m playwright install chromium` |
| Puerto ocupado | Otro proceso en :5070 | `kill $(lsof -ti:5070)` y reiniciar |
| Fuente tarda >2min | Captcha lento | Normal — 2captcha puede demorar 40-90s |
| DB no existe | Se crea automático | Solo esperar a que termine el primer arranque |

---

## 8. Branding

La app se llama **VerifyData** (paleta magenta/violeta/azul/cian).
No usa imágenes de logo — todo es texto CSS. Si se quiere cambiar el
nombre de marca, editar `ui_theme.py`:

- `BRAND_NAME` (texto)
- `WORDMARK` (HTML con `<span>` para el color cian)

---

## 9. Notas importantes

- **No subir a GitHub** con las API keys reales. `.env` está en
  `.gitignore`.
- Las fuentes usan Playwright headless. En servidores sin GPU o con
  poca RAM, algunas fuentes pueden fallar por timeout.
- Si el portal de la Policía cambia su sitio, el flujo de
  `policia_inhab.py` puede romperse (tiene selectores CSS frágiles).
- `AGENTS.md` tiene la documentación técnica completa del proyecto.
  Leerlo antes de modificar cualquier fuente.

---

## Contacto

Para dudas sobre el proyecto, contactar al equipo que te entregó esta
carpeta.
