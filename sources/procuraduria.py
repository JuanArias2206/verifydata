"""
sources/procuraduria.py — Procuraduría General de la Nación.

URL: https://procuraduria.gov.co/Pages/Consulta-de-Antecedentes.aspx
Tiene form: Tipo Identificación (SELECT) + Número (INPUT) + trivia
captcha (INPUT) dentro de un iframe apps.procuraduria.gov.co.

El selector del iframe real es:
  #ddlTipoID            — <select> "Tipo de Identificación" (5 opciones)
  #txtNumID             — <input type=text> "Número Identificación" (maxLength=15)
  #lblPregunta          — <span> con la pregunta de trivia
  #txtRespuestaPregunta — <input type=text> trivia
  #btnConsultar         — <input type=submit> "Consultar"

La trivia en vivo (capturada 2026-06-12) es:
  "¿Escriba las dos primeras letras del primer nombre de la persona
   a la cual esta expidiendo el certificado?"
→ Es DETERMINÍSTICA a partir de `nombre`: primeras 2 letras del primer
  token. No necesita LLM. Si la trivia cambia a math (¿Cuánto es X+Y?)
  o a geografía (¿Cuál es la capital de X?), cae al TriviaSolver local
  (math) o al LLM (Haiku 4.5) según corresponda.

Flujo crítico: el <select ddlTipoID> es ASP.NET WebForms con
AutoPostBack=True. Seleccionar una opción DISPARA un postback que
re-renderiza el iframe y resetea los inputs. Por eso el ORDEN es:
  1) seleccionar dropdown  → esperar postback (~2-4s)
  2) llenar cédula          → type() + verificar
  3) resolver trivia        → en este punto ya tenemos el nombre
  4) llenar trivia          → type() + Tab + verificar
  5) click Consultar        → esperar postback de resultado
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from .base import Hit
from .registry import register
# A8: enrutar los print() de diagnóstico por logging (ver logging_config).
from logging_config import route_print_to_logger as _rptl
print = _rptl(__name__)


from sources.base import DATA
(DATA / "screenshots").mkdir(parents=True, exist_ok=True)


def _have_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def _save_shot(page, source: str, query: str, tag: str = "") -> str | None:
    """Guarda screenshot de la página actual y devuelve ruta relativa."""
    safe = re.sub(r"[^\w-]", "_", f"{source}_{query}")[:50]
    suffix = f"_{tag}" if tag else ""
    fname = f"screenshots/{safe}{suffix}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(DATA / fname), full_page=False, timeout=15000)
        return fname
    except Exception as e:
        print(f"  [screenshot fail: {e}]", flush=True)
        return None


def _get_trivia_solver():
    """Construye un TriviaSolver con la API key de Anthropic desde config.yaml.

    Se usa SIEMPRE para resolver trivia, independientemente del solver que
    reciba fetch() como argumento (porque TwoCaptchaSolver.no resuelve trivia).
    """
    try:
        from config import load_config
        cfg = load_config()
        tri_cfg = cfg.get("captcha", {}).get("trivia", {})
        from solvers.trivia import TriviaSolver
        return TriviaSolver(
            anthropic_api_key=tri_cfg.get("anthropic_api_key", ""),
            model=tri_cfg.get("anthropic_model", "claude-haiku-4-5"),
        )
    except Exception as e:
        print(f"  [trivia-solver init fail: {e}]", flush=True)
        return None


def _answer_trivia_deterministic(question: str, nombre: str) -> str | None:
    """Detecta si la trivia es determinística a partir del nombre y devuelve
    la respuesta esperada. Devuelve None si no se puede resolver
    determinísticamente (cae al LLM).

    Casos conocidos en producción (capturados 2026-06-12):
    - "¿Escriba las dos primeras letras del primer nombre de la persona
       a la cual esta expidiendo el certificado?" → 2 primeras letras
      del PRIMER token de `nombre`.
    - Variantes: "primeras dos letras del primer nombre", "2 letras
      del nombre", "primer nombre" en general.
    - v2 (2026-06-12): nueva rotación observada — "¿Cual es el primer
      nombre de la persona...?" → PRIMER nombre COMPLETO, no las 2
      primeras letras. Si respondemos "DA" cuando el form espera
      "DANIEL", el form rechaza con "valor o tipo de dato".
    """
    if not question or not nombre:
        return None
    # Parser general (iniciales, apellidos, N letras, conteo, etc.)
    try:
        from solvers.trivia import answer_from_name
        general = answer_from_name(question, nombre)
        if general:
            return general
    except Exception:
        pass
    q_low = question.lower()
    # Patrones determinísticos (específicos, se mantienen como respaldo)
    # Caso A: "¿Cuál es el primer nombre?" → nombre completo
    if any(p in q_low for p in [
        "cual es el primer nombre",
        "cuál es el primer nombre",
        "primer nombre de la persona",
        "cual es el nombre",
        "cuál es el nombre",
    ]):
        # Pero NO si la pregunta pide 2 letras / 2 primeras letras
        if any(letter_p in q_low for letter_p in [
            "dos primeras letras", "2 primeras letras", "2 letras",
            "primeras dos letras", "primeras letras",
        ]):
            pass  # cae al caso B
        else:
            first = nombre.strip().split()[0] if nombre.strip() else ""
            first = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", first)
            if first:
                return first.upper()

    # Caso B: "...las 2 / dos primeras letras del primer nombre"
    if any(p in q_low for p in [
        "primeras dos letras del primer nombre",
        "dos primeras letras del primer nombre",
        "2 letras del primer nombre",
        "2 primeras letras del primer nombre",
        "primeras letras del primer nombre",
    ]):
        first = nombre.strip().split()[0] if nombre.strip() else ""
        first = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", first)
        if len(first) >= 2:
            return first[:2].upper()
        if first:
            return first.upper()
    return None


def _answer_trivia(question: str, nombre: str, trivia_solver) -> tuple[str, str]:
    """Resuelve trivia con prioridad:
    1) Determinística desde `nombre` (no requiere red)
    2) TriviaSolver local (math, luego LLM Anthropic)
    Devuelve (answer, source) donde source es 'deterministic' / 'trivia_solver'.
    """
    # 1) Determinística
    det = _answer_trivia_deterministic(question, nombre)
    if det:
        return det, "deterministic"
    # 2) TriviaSolver (math local → Anthropic fallback)
    if trivia_solver is not None:
        try:
            ans = trivia_solver.solve_trivia(question, context=nombre)
            if ans:
                return str(ans).strip(), "trivia_solver"
        except Exception as e:
            print(f"  [procuraduria] trivia_solver.solve_trivia fail: {e}",
                  flush=True)
    return "", ""


def _safe_fill_input(locator, value: str, page, *, press_tab: bool = True,
                      verify: bool = True) -> bool:
    """Llena un input con type() (simula teclado) en lugar de fill().

    Algunos formularios ASP.NET WebForms en iframes cross-origin no
    registran los eventos `input`/`change` que dispara fill(), así que
    los handlers de validación no ven el valor. type() con delay
    dispara los eventos de teclado uno a uno.

    - click() + select_all (Ctrl+A) + type() + press Tab (confirma blur)
    - verifica con input_value() y retry una vez con JS directo si no quedó
    """
    try:
        # Focus + click
        try:
            locator.click(timeout=5000)
        except Exception:
            try:
                locator.focus(timeout=2000)
            except Exception:
                pass
        # Select all + delete por si hay texto residual
        try:
            page.keyboard.press("ControlOrMeta+A")
            page.wait_for_timeout(100)
            page.keyboard.press("Delete")
            page.wait_for_timeout(100)
        except Exception:
            pass
        # Type the value
        locator.type(value, delay=40)
        # Tab para confirmar (dispara blur → algunos handlers lo necesitan)
        if press_tab:
            try:
                page.keyboard.press("Tab")
                page.wait_for_timeout(200)
            except Exception:
                pass
        # Verify
        if verify:
            try:
                actual = locator.input_value(timeout=2000)
                if actual and value in actual:
                    return True
            except Exception:
                pass
            # Retry con JS directo
            try:
                # Buscar el input por su id/name
                target_handle = locator.element_handle()
                if target_handle is not None:
                    target_handle.evaluate(
                        "(el, v) => {"
                        "  el.focus();"
                        "  el.value = v;"
                        "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                        "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                        "  el.dispatchEvent(new Event('blur', {bubbles: true}));"
                        "}", value)
                    page.wait_for_timeout(300)
                    actual = locator.input_value(timeout=2000)
                    if actual and value in actual:
                        return True
            except Exception:
                pass
            return False
        return True
    except Exception as e:
        print(f"  [procuraduria] _safe_fill_input fail: {e}", flush=True)
        return False


def _read_trivia_text_from_iframes(page, *, log_prefix: str = "") -> str:
    """Lee el texto de trivia del iframe de Procuraduría.

    Estrategia por orden de preferencia:
    1. <span id='lblPregunta'> dentro del iframe (específico del form)
    2. Cualquier <label>/<span>/<font> con '?' o '¿' dentro del iframe
    3. Outer page: <label>/<span>/<div>/<p>/<td>/<font> con '?' o '¿'
    4. Regex sobre page.content() como último fallback

    Esta función es el ÚNICO punto donde leemos trivia. Se llama
    múltiples veces (antes y después de esperar postback) para
    detectar rotaciones anti-bot del sitio.
    """
    trivia_text = ""
    for fr, _kind in _get_candidate_frames(page):
        try:
            trivia_text = fr.evaluate("""
                () => {
                  // Primero: el span#lblPregunta (específico
                  // del form de Procuraduría)
                  const lbl = document.getElementById('lblPregunta');
                  if (lbl) {
                    const t = (lbl.innerText || '').trim();
                    if (t.length > 0) return t;
                  }
                  // Fallback: primer label que tenga ? o ¿
                  const labels = document.querySelectorAll(
                    'label, span, font');
                  for (const l of labels) {
                    const t = (l.innerText || '').trim();
                    if (t.length < 5 || t.length > 200) continue;
                    if (t.includes('?') || t.includes('¿')) {
                      return t;
                    }
                  }
                  return '';
                }
            """)
            if trivia_text:
                break
        except Exception:
            continue
    if not trivia_text:
        try:
            # Try the outer page (legacy fallback)
            trivia_text = page.evaluate("""
                () => {
                  const all = document.querySelectorAll(
                    'label, span, div, p, td, font');
                  for (const l of all) {
                    const t = (l.innerText || '').trim();
                    if (t.length < 5 || t.length > 200) continue;
                    if (t.includes('?') || t.includes('¿')) {
                      return t;
                    }
                  }
                  return '';
                }
            """)
        except Exception:
            pass
    if not trivia_text:
        try:
            body_text2 = page.content()
            m = re.search(r"[¿?][^¿?<>\n]{5,150}\??", body_text2)
            if m:
                trivia_text = m.group(0).strip()
        except Exception:
            pass
    if log_prefix and trivia_text:
        print(f"  [procuraduria] {log_prefix}: {trivia_text!r}", flush=True)
    return trivia_text


def _safe_fill_input_in_iframe(page, iframe, input_id: str, value: str,
                                 *, press_tab: bool = True,
                                 verify: bool = True) -> bool:
    """Llena un input en un iframe cross-origin con estrategia más agresiva.

    Específico para el form de Procuraduría (apps.procuraduria.gov.co):
    - el <select> dispara AutoPostBack que re-renderiza el iframe, así
      que el locator del input puede volverse stale
    - usamos `iframe.evaluate()` para setear el value directamente via
      JS, lo que es cross-origin safe en Playwright
    - luego type() por si el primer intento no se queda, y verificamos
      con `iframe.evaluate(el => el.value)`.

    IMPORTANTE (v2 — 2026-06-12): en iframes cross-origin, el clear
    con Control+A + Delete a nivel de página NO limpia el input
    dentro del iframe (los eventos de teclado no cruzan el
    boundary). Por eso ANTES de type() y ANTES del set directo,
    hacemos un CLEAR via JS puro (`el.value = ''` + dispatch
    `input`), y verificamos que `el.value === ''` antes de continuar.
    Si el clear falla (texto residual como 'JU'), retry con
    otra ronda de clear (hasta 3 rondas).
    """
    if iframe is None:
        return False

    def _js_clear_input(fid, eid, max_rounds: int = 3) -> bool:
        """Limpia el input via JS (cross-origin safe) y verifica.

        Devuelve True si el input termina con value === ''. Si
        quedan caracteres residuales, dispatcha otro input event
        hasta max_rounds veces.
        """
        try:
            # NOTE: Playwright's `frame.evaluate` does NOT support
            # `//` line comments in the expression string (it parses
            # the expression in a context that truncates at `//`).
            # Use `/* ... */` block comments only, or no comments.
            ok = fid.evaluate(
                "(args) => {"
                "  const el = document.getElementById(args.id);"
                "  if (!el) return {ok: false, reason: 'no_element'};"
                "  el.focus();"
                "  for (let i = 0; i < args.rounds; i++) {"
                "    /* triple set para IE/Edge/ASP.NET */"
                "    el.value = '';"
                "    el.setAttribute('value', '');"
                "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                "    el.dispatchEvent(new Event('change', {bubbles: true}));"
                "    if (el.value === '' && "
                "        (el.getAttribute('value') || '') === '') {"
                "      return {ok: true, value: el.value};"
                "    }"
                "  }"
                "  return {ok: false, value: el.value, "
                "          attr: el.getAttribute('value') || ''};"
                "}",
                {"id": eid, "rounds": max_rounds})
            if isinstance(ok, dict):
                return bool(ok.get("ok"))
            return bool(ok)
        except Exception as e:
            print(f"  [procuraduria] _js_clear_input fail ({eid}): {e}",
                  flush=True)
            return False

    # 0) CLEAR via JS — primero, antes de cualquier otra cosa. Esto
    #    es robusto cross-origin.
    cleared = _js_clear_input(iframe, input_id, max_rounds=3)
    if not cleared:
        # Segundo intento con 5 rondas
        cleared = _js_clear_input(iframe, input_id, max_rounds=5)
    if not cleared:
        # Diagnóstico: leer el value residual
        try:
            residual = iframe.evaluate(
                "(id) => (document.getElementById(id) || {}).value || ''",
                input_id)
            print(f"  [procuraduria] _safe_fill_input_in_iframe CLEAR FAIL "
                  f"({input_id}) — residual value: {residual!r}", flush=True)
        except Exception:
            pass

    try:
        # 1) JS directo: setear el value + dispatchar todos los eventos
        #    que ASP.NET WebForms espera (input, change, blur, keyup)
        ok = iframe.evaluate(
            "(args) => {"
            "  const el = document.getElementById(args.id);"
            "  if (!el) return false;"
            "  el.focus();"
            "  el.value = args.v;"
            "  el.dispatchEvent(new Event('input', {bubbles: true}));"
            "  el.dispatchEvent(new Event('change', {bubbles: true}));"
            "  el.dispatchEvent(new Event('blur', {bubbles: true}));"
            "  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));"
            "  return el.value === args.v;"
            "}",
            {"id": input_id, "v": value})
        if ok:
            if press_tab:
                try:
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(200)
                except Exception:
                    pass
            return True
    except Exception as e:
        print(f"  [procuraduria] _safe_fill_input_in_iframe JS fail "
              f"({input_id}): {e}", flush=True)

    if not verify:
        return False
    # 2) Fallback: type() a través de la página (puede fallar si el
    #    iframe es cross-origin pero Playwright lo permite). Re-clear
    #    antes de type, en caso de que el clear JS hubiera dejado
    #    basura.
    try:
        # Re-find the locator — it may be stale
        loc = iframe.locator(f"#{input_id}").first
        if loc.count() == 0:
            return False
        loc.click(timeout=5000)
        # Re-clear via JS (no confiamos en Ctrl+A cross-origin)
        _js_clear_input(iframe, input_id, max_rounds=3)
        loc.type(value, delay=40)
        if press_tab:
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)
        # Verify
        actual = iframe.evaluate(
            "(id) => (document.getElementById(id) || {}).value || ''",
            input_id)
        if actual and value in actual:
            return True
    except Exception as e:
        print(f"  [procuraduria] _safe_fill_input_in_iframe type() "
              f"fail ({input_id}): {e}", flush=True)
    return False


def _run_in_fresh_browser(fn):
    """Crea un Playwright sync NUEVO por llamada (evita bug de threads)."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerifyData-Demo/1.0 (Mozilla/5.0 compatible)")
            try:
                page = ctx.new_page()
                return fn(page)
            finally:
                try: ctx.close()
                except Exception: pass
        finally:
            try: browser.close()
            except Exception: pass
    finally:
        try: pw.stop()
        except Exception: pass


def _get_candidate_frames(page):
    """Devuelve una lista de objetos (frame, kind) donde 'kind' indica si
    es 'page' (la página principal) o 'frame' (un iframe).

    Importante: el sitio de Procuraduría carga el form dentro de un
    <iframe src='https://apps.procuraduria.gov.co/webcert/inicio.aspx'>.
    El dropdown, los inputs y el botón Consultar están TODOS dentro del
    iframe, no en la página SharePoint externa. Si solo buscamos en
    page.locator(...), jamás vamos a encontrarlos.
    """
    out = [(page, "page")]
    try:
        for fr in page.frames:
            url = fr.url or ""
            # El iframe de procuraduría (aplicación real del form)
            if "apps.procuraduria.gov.co/webcert" in url:
                out.append((fr, "frame"))
    except Exception:
        pass
    return out


def _find_cedula_input(page):
    """Localiza el INPUT de cédula. Prueba en orden:

    1. page.get_by_label("Número Identificación"/"Número de Documento")
    2. input con placeholder/name/id que contenga 'Identificacion'/'Documento'
    3. input con name='txtNumID' o id='txtNumID' (específico del form
       de Procuraduría)
    4. input con maxlength=15 (largo típico de una cédula/NIT colombiano)
       visible en la mitad inferior de la página (donde está el campo de
       cédula, no el de trivia que es un input más pequeño en la mitad
       superior).

    Busca tanto en la página principal como en iframes (ver
    _get_candidate_frames).

    Devuelve el (locator, frame) o (None, None).
    """
    label_patterns = [
        r"N[uú]mero\s+Identificaci[oó]n",
        r"N[uú]mero\s+de\s+Identificaci[oó]n",
        r"N[uú]mero\s+de\s+Documento",
        r"Identificaci[oó]n",
    ]
    for fr, _kind in _get_candidate_frames(page):
        # Intento 1: por label
        for label_re in label_patterns:
            try:
                inp = fr.get_by_label(re.compile(label_re, re.I)).first
                if inp.count() > 0:
                    return inp, fr
            except Exception:
                pass
        # Intento 2: por placeholder / name / id
        try:
            inp = fr.locator(
                "input[placeholder*='Identificacion' i], "
                "input[placeholder*='Documento' i], "
                "input[name*='identificacion' i], "
                "input[id*='identificacion' i], "
                "input[id*='Numero' i], "
                "input[id*='numero' i]").first
            if inp.count() > 0:
                return inp, fr
        except Exception:
            pass
        # Intento 3: selectores específicos del form de Procuraduría
        try:
            inp = fr.locator(
                "input[name='txtNumID'], "
                "input[id='txtNumID']").first
            if inp.count() > 0:
                return inp, fr
        except Exception:
            pass
        # Intento 4: input visible con maxlength >= 10 (cédula) y
        # ubicado en la mitad inferior de la página (excluye inputs
        # de búsqueda/header). Esto evita confundir el input de trivia
        # con el de cédula.
        try:
            candidates = fr.evaluate("""
                () => {
                  const out = [];
                  const inputs = document.querySelectorAll('input');
                  for (const i of inputs) {
                    if (i.offsetParent === null) continue;
                    if (i.type === 'hidden' || i.type === 'submit' ||
                        i.type === 'button' || i.type === 'checkbox' ||
                        i.type === 'radio') continue;
                    const r = i.getBoundingClientRect();
                    if (r.width < 50 || r.height < 10) continue;
                    const maxLen = parseInt(i.getAttribute('maxlength') || '0', 10);
                    out.push({
                      id: i.id || '',
                      name: i.name || '',
                      maxLength: maxLen,
                      top: r.top,
                      windowHeight: window.innerHeight,
                      placeholder: i.placeholder || ''
                    });
                  }
                  return out;
                }
            """)
            # Preferir un input con maxLength >= 10 en la mitad inferior
            cands = sorted(
                candidates,
                key=lambda c: -(c.get("maxLength") or 0)
            )
            for c in cands:
                if (c.get("maxLength") or 0) >= 10:
                    sel = (f"input[name='{c['name']}']"
                           if c.get("name") else
                           f"input[id='{c['id']}']" if c.get("id") else
                           None)
                    if sel:
                        loc = fr.locator(sel).first
                        if loc.count() > 0:
                            return loc, fr
        except Exception:
            pass
    return None, None


def _find_tipo_select(page):
    """Localiza el dropdown 'Tipo de Identificación'.

    El dropdown puede ser:
    a) <select> nativo (caso más común — el form de Procuraduría usa
       <select id='ddlTipoID'> dentro de un iframe)
    b) p-dropdown (PrimeNG): div.p-dropdown con aria-label o label
       cercano que contenga 'Tipo' o 'Identificacion'
    c) mat-select (Angular Material): mat-select con aria-label
       'Tipo de Identificacion'
    d) div[role=combobox] con texto cercano 'Tipo de Identificacion'
    e) Cualquier elemento clickable que tenga 'Cedula'/'ciudadania' como
       texto o aria-label

    Busca primero en iframes (la forma vive ahí) y luego en la página
    principal. Devuelve (locator, kind, frame) o (None, None, None).
    """
    candidates = _get_candidate_frames(page)

    for fr, _kind in candidates:
        # (a) <select> nativo: <select> con option text que contenga
        # "Cédula" o "ciudadanía"
        try:
            selects = fr.locator("select")
            n = selects.count()
            for i in range(n):
                sel = selects.nth(i)
                try:
                    opts = sel.locator("option").all_inner_texts()
                except Exception:
                    continue
                joined = " | ".join(opts).lower()
                if ("cédula" in joined or "cedula" in joined or
                        "ciudadanía" in joined or "ciudadania" in joined):
                    return sel, "select", fr
        except Exception:
            pass

        # (b) p-dropdown (PrimeNG): div.p-dropdown
        try:
            for sel_re in [
                "div.p-dropdown[aria-label*='Tipo' i]",
                "div.p-dropdown[aria-label*='Identificacion' i]",
                "div.p-dropdown:has(.p-dropdown-label)",
            ]:
                el = fr.locator(sel_re).first
                if el.count() > 0:
                    return el, "p-dropdown", fr
        except Exception:
            pass

        # (c) mat-select (Angular Material)
        try:
            for sel_re in [
                "mat-select[aria-label*='Tipo' i]",
                "mat-select[aria-label*='Identificacion' i]",
                "mat-select:has(mat-label:has-text('Tipo'))",
            ]:
                el = fr.locator(sel_re).first
                if el.count() > 0:
                    return el, "mat-select", fr
        except Exception:
            pass

        # (d) div[role=combobox] con texto cercano 'Tipo de Identificacion'
        try:
            combos = fr.locator("[role='combobox'], [role='listbox']")
            n = combos.count()
            for i in range(n):
                el = combos.nth(i)
                # check si el element o su ancestro cercano tiene
                # 'Tipo de Identificacion' como label
                try:
                    aria = el.get_attribute("aria-label") or ""
                    if "tipo" in aria.lower() and "identificaci" in aria.lower():
                        return el, "role-combobox", fr
                except Exception:
                    pass
                # buscar label adyacente
                try:
                    has_label = el.evaluate("""
                        (el) => {
                          // search in element itself, parent, and previous siblings
                          let cur = el;
                          for (let k = 0; k < 5 && cur; k++) {
                            const txt = (cur.innerText || '').toLowerCase();
                            if (txt.includes('tipo') &&
                                txt.includes('identificaci')) return true;
                            cur = cur.parentElement;
                          }
                          // check previous label sibling
                          const prev = el.parentElement &&
                                       el.parentElement.previousElementSibling;
                          if (prev) {
                            const pt = (prev.innerText || '').toLowerCase();
                            if (pt.includes('tipo') &&
                                pt.includes('identificaci')) return true;
                          }
                          // check label[for] pointing to this id
                          if (el.id) {
                            const lbl = document.querySelector(
                              `label[for='${el.id}']`);
                            if (lbl && (lbl.innerText || '')
                                .toLowerCase()
                                .includes('tipo')) return true;
                          }
                          return false;
                        }
                    """)
                    if has_label:
                        return el, "role-combobox", fr
                except Exception:
                    pass
        except Exception:
            pass

        # (e) Cualquier elemento clickable con 'Cédula'/'ciudadania' como
        # texto o aria-label (es muy laxo — último fallback)
        try:
            el = fr.locator(
                "[aria-label*='Cedula' i], "
                "[aria-label*='Cédula' i], "
                "[aria-label*='ciudadania' i], "
                "[aria-label*='ciudadanía' i]").first
            if el.count() > 0:
                return el, "aria-cedula", fr
        except Exception:
            pass

    return None, None, None


def _select_option_in_dropdown(locator, kind: str, option_text: str,
                                page=None) -> bool:
    """Selecciona una opción en el dropdown identificado por (locator,
    kind).

    kind='select'        : Playwright select_option(label=...)
    kind='p-dropdown'    : click → click opción con texto
    kind='mat-select'    : click → click opción
    kind='role-combobox' : click → click opción con role=option
    kind='aria-cedula'   : click (no-op adicional; ya tiene la opción)
    """
    try:
        if kind == "select":
            try:
                locator.select_option(label=option_text)
            except Exception:
                # fallback: buscar opción por texto parcial
                opts = locator.locator("option").all_inner_texts()
                for o in opts:
                    if "Cédula" in o or "Cedula" in o:
                        locator.select_option(label=o)
                        break
            return True

        # Para los demás tipos, click en el dropdown para abrirlo y luego
        # click en la opción con el texto deseado.
        try:
            locator.click(timeout=5000, force=True)
        except Exception:
            try:
                # Algunos PrimeNG necesitan click en el label interno
                locator.locator(".p-dropdown-label, .mat-select-value, "
                                "[role='combobox']").first.click(
                    timeout=5000, force=True)
            except Exception:
                pass
        # Pequeña espera para que la lista de opciones se renderice
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Intentar hacer click en la opción por varios selectores
        for opt_sel in [
            # PrimeNG: li.p-dropdown-item con texto
            f"li.p-dropdown-item:has-text('{option_text}')",
            # PrimeNG: p-dropdownItem
            f"p-dropdownitem li:has-text('{option_text}')",
            # Material: mat-option
            f"mat-option:has-text('{option_text}')",
            # genérico: li con role=option
            f"[role='option']:has-text('{option_text}')",
            # genérico: cualquier elemento visible con ese texto
            f"text='{option_text}'",
        ]:
            try:
                opt = page.locator(opt_sel).first
                if opt.count() > 0:
                    opt.click(timeout=5000, force=True)
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        print(f"  [procuraduria] _select_option_in_dropdown fail: {e}",
              flush=True)
        return False


def _find_trivia_input(page):
    """Localiza el INPUT de trivia. La trivia pregunta 'Cuál es la Capital...'
    o similar, así que el label cambia. Buscamos label con '?' o palabra clave
    'Capital'/'Cuanto'/'Suma' y encontramos el input adyacente.

    Devuelve (locator, frame) o (None, None)."""
    # Intento 0: selectores específicos del form de Procuraduría
    for fr, _kind in _get_candidate_frames(page):
        try:
            inp = fr.locator(
                "input[name='txtRespuestaPregunta'], "
                "input[id='txtRespuestaPregunta']").first
            if inp.count() > 0:
                return inp, fr
        except Exception:
            pass

    # Intento 1: por label con patrón pregunta
    for label_re in [
        r"[¿?].*[Cc]apital",
        r"[¿?].*[Cc]u[aá]l",
        r"[¿?].*[Cc]u[aá]nto",
        r"[¿?].*[Ss]uma",
        r"[¿?].*[Rr]esta",
        r"[¿?].*[Mm]ultiplic",
        r"[¿?].*[\+\-*/].*\d",
    ]:
        for fr, _kind in _get_candidate_frames(page):
            try:
                inp = fr.get_by_label(re.compile(label_re, re.I)).first
                if inp.count() > 0:
                    return inp, fr
            except Exception:
                pass

    # Intento 2: buscar el ÚLTIMO input visible de tipo texto (distinto
    # del de cédula) en el frame apropiado
    for fr, _kind in _get_candidate_frames(page):
        try:
            visibles = fr.evaluate("""
                () => {
                  const inputs = document.querySelectorAll('input');
                  const out = [];
                  for (const i of inputs) {
                    if (i.offsetParent !== null &&
                        i.type !== 'hidden' &&
                        i.type !== 'submit' &&
                        i.type !== 'button' &&
                        i.type !== 'checkbox' &&
                        i.type !== 'radio') {
                      out.push(true);
                    } else {
                      out.push(false);
                    }
                  }
                  return Array.from(inputs).map((i, idx) => ({
                    idx, visible: out[idx],
                    type: i.type || '',
                    maxLength: i.maxLength || 0,
                    name: i.name || '',
                    id: i.id || ''
                  })).filter(x => x.visible);
                }
            """)
            if visibles and len(visibles) >= 2:
                # trivia es el último visible
                last = visibles[-1]
                sel = f"input[name='{last['name']}']" if last['name'] else \
                      f"input[id='{last['id']}']" if last['id'] else None
                if sel:
                    return fr.locator(sel).first, fr
        except Exception:
            pass
    return None, None
    return None


@register
class ProcuraduriaAntecedentesSource:
    name = "Procuraduría — Antecedentes Disciplinarios"
    source_url = "https://www.procuraduria.gov.co/Pages/Consulta-de-Antecedentes.aspx"
    category = "Antecedentes disciplinarios"
    requires_captcha = True
    captcha_type = "trivia"

    def fetch(self, nombre, cedula, fecha_exp=None, solver=None):
        t0 = time.time()
        # Debug: nombre del solver inyectado
        solver_name_dbg = getattr(solver, "name", "None") if solver else "None"
        print(f"  [procuraduria] solver inyectado: {solver_name_dbg}", flush=True)

        if not cedula:
            return Hit(self.name, False, "",
                       notice="Procuraduría requiere cédula/NIT.",
                       elapsed_s=time.time()-t0)
        if not _have_browser():
            return Hit(self.name, False, "",
                       notice="Playwright no instalado. "
                              "Click 'abrir fuente' para buscar manualmente.",
                       captcha_required=True,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)

        # Para trivia SIEMPRE usamos TriviaSolver local con Anthropic.
        # El solver que llega como argumento puede ser TwoCaptchaSolver
        # (que no resuelve trivia) o un wrapper.
        trivia_solver = _get_trivia_solver()
        print(f"  [procuraduria] trivia solver: "
              f"{trivia_solver.name if trivia_solver else 'None'}",
              flush=True)
        if not trivia_solver:
            return Hit(self.name, False, "",
                       notice="No se pudo inicializar TriviaSolver con "
                              "Anthropic. Revisar config.yaml.",
                       captcha_required=True,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)

        try:
            # results acumula info de debug que devolvemos en evidence
            results: dict = {"solver_name": trivia_solver.name,
                             "solver_injected": solver_name_dbg}

            def _do_proc(page):
                page.set_default_timeout(30000)
                # El sitio a veces tarda >30s en responder; usar timeout
                # extendido (50s) para no abortar navegaciones lentas.
                try:
                    page.goto(self.source_url, wait_until="domcontentloaded",
                              timeout=50000)
                except Exception as e:
                    # Si goto falla (timeout/red), retornar aviso de sitio
                    # no responde inmediatamente.
                    return Hit(self.name, False, "",
                               notice=f"Sitio Procuraduría no responde "
                                      f"({type(e).__name__}). Reintentar.",
                               evidence_urls=[self.source_url],
                               elapsed_s=time.time()-t0)

                # Chequeo temprano: si en 10s el sitio dice "No Disponible"
                # o el título es "blocked", no tiene sentido esperar más.
                # Acumulado de tiempo desde goto: 3 + 3 + 4 = 10s.
                for wait_ms in (3000, 3000, 4000):
                    page.wait_for_timeout(wait_ms)
                    try:
                        body_early = page.evaluate(
                            "() => document.body.innerText")
                        title_early = page.evaluate(
                            "() => document.title || ''")
                    except Exception:
                        body_early = ""
                        title_early = ""
                    if ("No Disponible" in body_early or
                        "no se encuentra disponible" in
                            body_early.lower() or
                        "Página Web No Disponible" in body_early or
                        "blocked" in title_early.lower() or
                        "The URL you requested has been blocked"
                            in body_early):
                        shot_early = _save_shot(page, "procuraduria",
                                                cedula, tag="down")
                        return Hit(self.name, False, "",
                                   notice="Sitio Procuraduría no responde. "
                                          f"Reintentar. Detalle: "
                                          f"{body_early[:80]}",
                                   evidence_urls=[self.source_url],
                                   download_url=shot_early,
                                   elapsed_s=time.time()-t0)

                # Esperar hidratación del form (puede tardar 8-25s).
                # Hacer polling cada 1.5s hasta encontrar un <select> o
                # <input>, o hasta 25s totales.
                hydrated = False
                for poll in range(17):  # 17 * 1.5s = 25.5s
                    try:
                        cnt = page.evaluate(
                            "() => document.querySelectorAll("
                            "'select, input').length")
                    except Exception:
                        cnt = 0
                    if cnt > 0:
                        hydrated = True
                        break
                    # Chequear en cada poll si el sitio cayó
                    try:
                        body_poll = page.evaluate(
                            "() => document.body.innerText")
                    except Exception:
                        body_poll = ""
                    if ("No Disponible" in body_poll or
                        "no se encuentra disponible" in
                            body_poll.lower() or
                        "Página Web No Disponible" in body_poll):
                        shot_d = _save_shot(page, "procuraduria", cedula,
                                            tag="down_late")
                        return Hit(self.name, False, "",
                                   notice="Sitio Procuraduría no responde. "
                                          f"Reintentar. Detalle: "
                                          f"{body_poll[:80]}",
                                   evidence_urls=[self.source_url],
                                   download_url=shot_d,
                                   elapsed_s=time.time()-t0)
                    page.wait_for_timeout(1500)

                if not hydrated:
                    # No hidrató en 25s. Probable sitio caído.
                    shot_nh = _save_shot(page, "procuraduria", cedula,
                                         tag="no_hydrate")
                    try:
                        body_nh = page.evaluate(
                            "() => document.body.innerText")
                    except Exception:
                        body_nh = ""
                    if ("No Disponible" in body_nh or
                        "no se encuentra disponible" in
                            body_nh.lower()):
                        return Hit(self.name, False, "",
                                   notice="Sitio Procuraduría no responde. "
                                          f"Reintentar. Detalle: "
                                          f"{body_nh[:80]}",
                                   evidence_urls=[self.source_url],
                                   download_url=shot_nh,
                                   elapsed_s=time.time()-t0)
                    return Hit(self.name, False, "",
                               notice="Formulario no hidrató en 25s. "
                                      "Ver screenshot.",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot_nh,
                               elapsed_s=time.time()-t0)

                # Screenshot post-hidratación
                shot_initial = _save_shot(page, "procuraduria", cedula,
                                          tag="initial")
                results["shot_initial"] = shot_initial

                # Re-check final: que el sitio no se haya caído justo
                # después de hidratar.
                try:
                    body_text = page.evaluate("() => document.body.innerText")
                except Exception:
                    body_text = ""
                if "No Disponible" in body_text or \
                   "no se encuentra disponible" in body_text.lower() or \
                   "Página Web No Disponible" in body_text:
                    return Hit(self.name, False, "",
                               notice="Sitio Procuraduría no responde. "
                                      f"Reintentar. Detalle: "
                                      f"{body_text[:80]}",
                               evidence_urls=[self.source_url],
                               download_url=shot_initial,
                               elapsed_s=time.time()-t0)

                # 2) Seleccionar "Cédula de ciudadanía" en el dropdown
                # CRÍTICO: el <select id='ddlTipoID'> es ASP.NET WebForms
                # con AutoPostBack=True. Al seleccionar una opción se
                # DISPARA un postback que re-renderiza el iframe. Por eso
                # el ORDEN es: (a) dropdown, (b) ESPERAR postback,
                # (c) llenar inputs (cedula + trivia). Si llenamos los
                # inputs ANTES del postback, sus valores se pierden.
                sel, sel_kind, sel_frame = None, None, None
                try:
                    sel, sel_kind, sel_frame = _find_tipo_select(page)
                    if sel is None:
                        # fallback: el primer <select> visible (de la
                        # página principal, no del frame)
                        try:
                            fallback = page.locator("select").first
                            if fallback.count() > 0:
                                sel = fallback
                                sel_kind = "select"
                                sel_frame = page
                        except Exception:
                            pass
                    if sel is None:
                        print(f"  [procuraduria] no se encontró dropdown "
                              f"de tipo de identificación.", flush=True)
                        shot = _save_shot(page, "procuraduria", cedula,
                                          tag="no_select")
                        # Re-check si el sitio dice "No Disponible"
                        try:
                            body2 = page.evaluate(
                                "() => document.body.innerText")
                        except Exception:
                            body2 = ""
                        if ("No Disponible" in body2 or
                            "no se encuentra disponible" in
                                body2.lower()):
                            return Hit(self.name, False, "",
                                       notice="Sitio Procuraduría no "
                                              "responde. Reintentar.",
                                       evidence_urls=[self.source_url],
                                       download_url=shot,
                                       elapsed_s=time.time()-t0)
                        return Hit(self.name, False,
                                   "Dropdown de tipo de identificación "
                                   "no presente en el DOM. Ver screenshot "
                                   "para diagnóstico.",
                                   captcha_required=True,
                                   evidence_urls=[self.source_url],
                                   download_url=shot,
                                   elapsed_s=time.time()-t0)
                    # Esperar a que el dropdown esté adjunto al DOM
                    # (timeout 20s — PrimeNG se hidrata más lento).
                    try:
                        sel.wait_for(state="attached", timeout=20000)
                    except Exception as e:
                        print(f"  [procuraduria] dropdown wait fail: {e}",
                              flush=True)
                    # Seleccionar la opción "Cédula de ciudadanía".
                    # _select_option_in_dropdown sabe cómo actuar según
                    # el tipo (select / p-dropdown / mat-select / etc).
                    ok = _select_option_in_dropdown(
                        sel, sel_kind, "Cédula de ciudadanía", page=sel_frame or page)
                    if not ok and sel_kind == "select":
                        # Fallback final: buscar option que contenga "Cédula"
                        try:
                            opts = sel.locator("option").all_inner_texts()
                            for opt in opts:
                                if ("Cédula" in opt or "Cedula" in opt):
                                    sel.select_option(label=opt)
                                    ok = True
                                    break
                        except Exception:
                            pass
                    results["dropdown_kind"] = sel_kind
                    results["dropdown_ok"] = ok
                    # Si el dropdown es un <select> nativo de ASP.NET,
                    # hay postback automático. Esperar 3s para que el
                    # iframe se re-renderice con los inputs limpios.
                    # (Otros tipos de dropdown no disparan postback, pero
                    # la espera es segura — solo son 3s del budget total.)
                    if sel_kind == "select":
                        page.wait_for_timeout(3000)
                    else:
                        page.wait_for_timeout(800)
                except Exception as e:
                    print(f"  [procuraduria] dropdown fail: {e}",
                          flush=True)
                    results["dropdown_kind"] = "exception"
                    results["dropdown_error"] = str(e)

                # 3) Llenar el INPUT de cédula (con type() + verify).
                # Usamos el helper directo-en-iframe porque conocemos el
                # id exacto (#txtNumID) y es más robusto que el locator
                # general (que puede confundir con inputs de re-render).
                proc_iframe = None
                for fr, _kind in _get_candidate_frames(page):
                    if "apps.procuraduria.gov.co" in (fr.url or ""):
                        proc_iframe = fr
                        break
                cedula_ok = False
                if proc_iframe is not None:
                    cedula_ok = _safe_fill_input_in_iframe(
                        page, proc_iframe, "txtNumID", cedula,
                        press_tab=False, verify=True)
                if not cedula_ok:
                    # Fallback al locator general
                    cedula_input, cedula_frame = _find_cedula_input(page)
                    if cedula_input is not None:
                        cedula_ok = _safe_fill_input(
                            cedula_input, cedula, sel_frame or page,
                            press_tab=False, verify=True)
                if not cedula_ok:
                    shot = _save_shot(page, "procuraduria", cedula,
                                      tag="cedula_fail")
                    return Hit(self.name, False, "",
                               notice="No se pudo llenar el input de "
                                      "cédula. Ver screenshot.",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                results["cedula_ok"] = cedula_ok

                # 4) Detectar trivia. El form está dentro de un iframe
                # (apps.procuraduria.gov.co), por lo que la trivia se
                # encuentra en el iframe, NO en la página principal.
                # page.evaluate() no puede cruzar el cross-origin, así
                # que hay que usar fr.evaluate() sobre el frame.
                #
                # v2 (2026-06-12): la trivia rota con CADA postback del
                # <select> y posiblemente con el llenado de la cédula.
                # Esto significa que la primera lectura puede ser de
                # una trivia obsoleta. Estrategia:
                #   (a) leer trivia_text_1 INMEDIATAMENTE
                #   (b) esperar un postback (1.5s × 5 = 7.5s) y
                #       re-leer como trivia_text_2
                #   (c) usar la MÁS RECIENTE (la que llegó última)
                # Si las dos lecturas son iguales, no rotó.
                # Si trivia_text_2 != trivia_text_1, el sitio rotó y
                # la respuesta debe ser para trivia_text_2.
                trivia_text_1 = _read_trivia_text_from_iframes(
                    page, log_prefix="trivia_text_leido_1 (post-cedula)")
                results["trivia_text_leido_1"] = trivia_text_1
                print(f"  [procuraduria] trivia_text_leido_1: "
                      f"{trivia_text_1!r}", flush=True)

                # Esperar posible postback rotador (la trivia puede
                # cambiar tras el llenado de la cédula en algunos
                # navegadores / sesiones). 1.5s × 5 = 7.5s.
                trivia_text_2 = trivia_text_1
                if trivia_text_1:
                    trivia_re_read_start = time.time()
                    for _poll in range(5):
                        page.wait_for_timeout(1500)
                        try_again = _read_trivia_text_from_iframes(
                            page, log_prefix="trivia_text_leido_2 (poll)")
                        if try_again and try_again != trivia_text_1:
                            trivia_text_2 = try_again
                            print(f"  [procuraduria] TRIVIA ROTÓ: "
                                  f"leído_1={trivia_text_1!r} → "
                                  f"leído_2={trivia_text_2!r}", flush=True)
                            break
                        # Si NO rotó pero al menos hay texto, listo
                        if try_again and try_again == trivia_text_1:
                            trivia_text_2 = try_again
                            break
                    print(f"  [procuraduria] trivia_text_leido_2: "
                          f"{trivia_text_2!r} "
                          f"(re-read {time.time() - trivia_re_read_start:.1f}s)",
                          flush=True)
                results["trivia_text_leido_2"] = trivia_text_2

                # Usar la trivia MÁS RECIENTE (la última que se
                # observó). Si la trivia rotó, _text_2 será la buena.
                trivia_text = trivia_text_2 or trivia_text_1
                if not trivia_text:
                    shot = _save_shot(page, "procuraduria", cedula,
                                      tag="no_trivia")
                    return Hit(self.name, False, "",
                               notice="No se detectó trivia captcha.",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                results["trivia_text"] = trivia_text

                # 5) Resolver trivia.
                # 1° intento: respuesta determinística a partir del
                #    nombre (cubre "¿primeras dos letras del primer
                #    nombre?" que es la trivia en vivo de Procuraduría).
                # 2° intento: TriviaSolver local (math + LLM Anthropic
                #    Haiku 4.5 fallback).
                answer, answer_src = _answer_trivia(
                    trivia_text, nombre or "", trivia_solver)
                results["trivia_answer"] = answer or ""
                results["trivia_answer_src"] = answer_src or "none"
                print(f"  [procuraduria] trivia_text_resolved: "
                      f"{trivia_text!r}", flush=True)
                print(f"  [procuraduria] answer_resolved: "
                      f"{answer!r} (source={answer_src!r})", flush=True)
                if not answer:
                    shot = _save_shot(page, "procuraduria", cedula,
                                      tag="no_answer")
                    return Hit(self.name, False, "",
                               notice=f"Solver no pudo resolver trivia: "
                                      f"'{trivia_text}'",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)

                # 5b) POST-CLEAR re-read: el sitio puede rotar la trivia
                # OTRA VEZ mientras calculamos la respuesta. Re-leer
                # AHORA (justo antes de escribir el answer) y, si la
                # trivia cambió DESPUÉS del clear/relleno, re-resolver
                # el answer con la nueva trivia.
                trivia_pre_fill = _read_trivia_text_from_iframes(
                    page, log_prefix="trivia_text_pre_fill (post-clear)")
                results["trivia_text_pre_fill"] = trivia_pre_fill
                if (trivia_pre_fill and trivia_pre_fill != trivia_text):
                    print(f"  [procuraduria] TRIVIA ROTÓ entre lectura y "
                          f"pre-fill: {trivia_text!r} → {trivia_pre_fill!r}. "
                          f"Re-resolviendo answer...", flush=True)
                    new_answer, new_answer_src = _answer_trivia(
                        trivia_pre_fill, nombre or "", trivia_solver)
                    if new_answer:
                        answer = new_answer
                        answer_src = new_answer_src
                        trivia_text = trivia_pre_fill
                        results["trivia_text"] = trivia_text
                        results["trivia_answer"] = answer
                        results["trivia_answer_src"] = answer_src
                        print(f"  [procuraduria] re-resolved answer: "
                              f"{answer!r} (source={answer_src!r})",
                              flush=True)

                # 6) Llenar INPUT de trivia (con type() + Tab + verify).
                # Re-detect el iframe por si re-renderizó tras el postback.
                proc_iframe = None
                for fr, _kind in _get_candidate_frames(page):
                    if "apps.procuraduria.gov.co" in (fr.url or ""):
                        proc_iframe = fr
                        break
                trivia_ok = False
                if proc_iframe is not None:
                    trivia_ok = _safe_fill_input_in_iframe(
                        page, proc_iframe, "txtRespuestaPregunta", answer,
                        press_tab=True, verify=True)
                if not trivia_ok:
                    # Fallback al locator general
                    trivia_input, trivia_frame = _find_trivia_input(page)
                    if trivia_input is not None:
                        trivia_ok = _safe_fill_input(
                            trivia_input, answer, trivia_frame or page,
                            press_tab=True, verify=True)
                if not trivia_ok:
                    shot = _save_shot(page, "procuraduria", cedula,
                                      tag="trivia_fail")
                    return Hit(self.name, False, "",
                               notice="No se pudo llenar el input de trivia. "
                                      "Ver screenshot.",
                               captcha_required=True,
                               evidence_urls=[self.source_url],
                               download_url=shot,
                               elapsed_s=time.time()-t0)
                results["trivia_ok"] = trivia_ok

                # 6b) Log: el answer que se ENVIÓ al form. Esto es lo
                # que el validador del servidor va a comparar contra la
                # trivia mostrada al momento del submit-click.
                print(f"  [procuraduria] answer_enviado: {answer!r} "
                      f"(source={answer_src!r})", flush=True)
                results["answer_enviado"] = answer
                results["answer_enviado_src"] = answer_src

                # 7) Screenshot DESPUÉS de llenar trivia y ANTES de Consultar
                shot_pre = _save_shot(page, "procuraduria", cedula,
                                      tag="pre_consultar")
                results["shot_pre_consultar"] = shot_pre

                # 8) Click "Consultar" — buscar en el frame correcto.
                # En el form de Procuraduría el botón es
                # <input type="submit" name="btnConsultar" value="Consultar">
                # dentro del iframe apps.procuraduria.gov.co.
                clicked = False
                for fr, _kind in _get_candidate_frames(page):
                    for sel_re in [
                        "input[name='btnConsultar']",
                        "input[value='Consultar']",
                        "button:has-text('Consultar')",
                    ]:
                        try:
                            btn = fr.locator(sel_re).first
                            if btn.count() > 0:
                                btn.click(timeout=5000, force=True)
                                clicked = True
                                break
                        except Exception:
                            continue
                    if clicked:
                        break
                if not clicked:
                    try:
                        page.locator(
                            "input[value='Consultar'], "
                            "button:has-text('Consultar')").first.click(
                            timeout=5000, force=True)
                        clicked = True
                    except Exception as e:
                        print(f"  [procuraduria] click Consultar fail: {e}",
                              flush=True)
                        try:
                            page.keyboard.press("Enter")
                        except Exception:
                            pass

                # 9) Esperar resultado con POLLING (no wait_for_timeout
                # ciego). El postback de Consultar tarda 2-8s y muestra
                # el resultado en #divSec del iframe. Salimos del loop
                # apenas #divSec tenga texto que NO sea la pregunta de
                # trivia original, o cuando aparezca un ValidationSummary
                # de error, o tras 25s (timeout duro — aumentado de 15s
                # para tolerar postbacks lentos observados en producción).
                divsec_text = ""
                poll_start = time.time()
                RESULT_POLL_TIMEOUT_S = 25  # antes 15s; el sitio es
                                            # intermitente y a veces el
                                            # postback de Consultar
                                            # tarda >15s
                while time.time() - poll_start < RESULT_POLL_TIMEOUT_S:
                    page.wait_for_timeout(800)
                    for fr, _kind in _get_candidate_frames(page):
                        try:
                            divsec_text = fr.evaluate("""
                                () => {
                                  // #divSec es el contenedor del
                                  // resultado del form de Procuraduría
                                  // (mensaje "NO REGISTRA..." o
                                  //  "REGISTRA..." con tabla)
                                  for (const sel of [
                                    '#divSec',
                                    '#divResultado',
                                    '#divMensaje',
                                    '#ValidationSummary1',
                                    '#lblResultado',
                                  ]) {
                                    const el = document.querySelector(sel);
                                    if (el) {
                                      const t = (el.innerText || '').trim();
                                      if (t.length > 5) return t;
                                    }
                                  }
                                  return '';
                                }
                            """)
                            if divsec_text and divsec_text != trivia_text:
                                break
                        except Exception:
                            continue
                    if divsec_text and divsec_text != trivia_text:
                        break

                # Screenshot del resultado (después del polling)
                shot_post = _save_shot(page, "procuraduria", cedula,
                                       tag="post_consultar")
                results["shot_post_consultar"] = shot_post
                results["divsec_text"] = divsec_text

                # Collect iframe text (cross-origin safe: usamos
                # fr.evaluate solo si el frame está en same-origin, sino
                # usamos el HTML de fr.content() que funciona con
                # cross-origin porque Playwright lo captura internamente).
                iframe_text = ""
                iframe_html = ""
                for fr, _kind in _get_candidate_frames(page):
                    try:
                        # Intentar evaluate primero (más rico)
                        try:
                            iframe_text = fr.evaluate("""
                                () => {
                                  // buscar SOLO en span/td/font/label
                                  // (no en divs, que contienen el FAQ)
                                  const out = [];
                                  for (const e of document.querySelectorAll(
                                      'span, td, font, label')) {
                                    const t = (e.innerText || '').trim();
                                    if (t.length > 3 && t.length < 500) {
                                      out.push(t);
                                    }
                                  }
                                  return out.join(' | ');
                                }
                            """)
                        except Exception:
                            pass
                        # Fallback: usar content() (cross-origin safe)
                        if not iframe_text:
                            try:
                                iframe_html = fr.content()
                                # strip tags y scripts
                                cleaned = re.sub(
                                    r"<script[^>]*>.*?</script>",
                                    "", iframe_html, flags=re.S)
                                cleaned = re.sub(r"<style[^>]*>.*?</style>",
                                                 "", cleaned, flags=re.S)
                                iframe_text = re.sub(r"<[^>]+>", " ",
                                                     cleaned)
                                iframe_text = re.sub(r"\s+", " ",
                                                     iframe_text)
                            except Exception:
                                pass
                        if iframe_text:
                            break
                    except Exception:
                        continue
                if not iframe_text:
                    iframe_text = ""

                # Texto del outer page (solo para detectar outage)
                try:
                    html = page.content()
                    outer_text = re.sub(r"<[^>]+>", " ", html)
                    outer_text = re.sub(r"\s+", " ", outer_text)
                except Exception:
                    html = ""
                    outer_text = ""

                # Outage detection (outer page)
                if ("No Disponible" in outer_text or
                    "no se encuentra disponible" in outer_text.lower() or
                    "The URL you requested has been blocked" in outer_text):
                    return Hit(self.name, False, "",
                               notice="Sitio Procuraduría no responde. "
                                      "Reintentar.",
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # Parsear resultado. Priorizar el contenido de
                # #divSec (resultado real), luego iframe_text (sin
                # FAQ), y finalmente outer_text (con guard contra
                # el FAQ).
                parse_text = (divsec_text + " || " + iframe_text) \
                    if divsec_text else iframe_text
                if not parse_text:
                    parse_text = outer_text

                # Texto en mayúsculas para matching
                pt_up = parse_text.upper()
                pt_low = parse_text.lower()

                # 1) "NO REGISTRA" / "no presenta antecedentes" — el
                # resultado real del form. Excluye el FAQ estático "EL
                # NÚMERO DE IDENTIFICACIÓN INGRESADO NO SE ENCUENTRA
                # REGISTRADO EN EL SISTEMA".
                if (("NO REGISTRA" in pt_up and
                     "NO SE ENCUENTRA REGISTR" not in pt_up and
                     "INGRESADO" not in pt_up)
                        or "NO TIENE ANTECEDENTES" in pt_up
                        or "NO REGISTRA SANCIONES" in pt_up
                        or "NO PRESENTA ANTECEDENTES" in pt_up
                        or "EL CIUDADANO NO PRESENTA" in pt_up):
                    return Hit(self.name, False,
                               "NO REGISTRA antecedentes disciplinarios",
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # 2) "REGISTRA antecedentes" — frase corta
                if ("REGISTRA" in pt_up and
                        "ANTECEDENTE" in pt_up and
                        "NO REGISTRA" not in pt_up and
                        "NO SE ENCUENTRA REGISTR" not in pt_up and
                        "INGRESADO" not in pt_up):
                    details = []
                    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>',
                                          iframe_html, re.S):
                        cells = re.findall(r'<td[^>]*>([^<]+)</td>',
                                           m.group(1))
                        if not cells:
                            continue
                        joined = " | ".join(c.strip() for c in cells)
                        if len(joined) > 20:
                            details.append({"fila": joined[:200]})
                            if len(details) >= 10:
                                break
                    return Hit(self.name, True,
                               f"REGISTRA antecedentes ({len(details)} filas)",
                               details,
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # 3) Trivia incorrecta
                if "incorrecta" in pt_low or \
                   "respuesta incorrecta" in pt_low:
                    return Hit(self.name, False,
                               "Trivia resuelta incorrectamente. "
                               f"Trivia era: '{trivia_text}', "
                               f"respuesta: '{answer}'",
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # 4) Validación cliente falló (cedula vacía, etc)
                if "Escriba un n" in parse_text or \
                   "mayor a 2 caracteres" in pt_low or \
                   "menor a 10 caracteres" in pt_low or \
                   "debe ser numérico" in pt_low or \
                   "debe ser mayor" in pt_low or \
                   "debe ser menor" in pt_low:
                    return Hit(self.name, False,
                               "Submit rechazado por validación del sitio "
                               f"(cedula={cedula!r}, trivia={trivia_text!r}, "
                               f"respuesta={answer!r}). Ver screenshot.",
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # 5) #divSec tiene contenido no vacío que no pudimos
                # clasificar
                if divsec_text and len(divsec_text.strip()) > 5:
                    return Hit(self.name, False,
                               f"Resultado Procuraduría: {divsec_text[:200]}",
                               evidence_urls=[self.source_url],
                               download_url=shot_post,
                               elapsed_s=time.time()-t0)

                # 6) Default: el form fue enviado pero no pudimos
                # clasificar el resultado. NO devolver "Búsqueda
                # ejecutada" que oculta el bug — devolver honestamente
                # lo que sabemos.
                return Hit(self.name, False,
                           "Búsqueda ejecutada (ver screenshot — no se "
                           "pudo clasificar el resultado del iframe)",
                           evidence_urls=[self.source_url],
                           download_url=shot_post,
                           elapsed_s=time.time()-t0)

            hit = _run_in_fresh_browser(_do_proc)
            # hit puede ser None si el browser crashea
            if hit is None:
                return Hit(self.name, False, "",
                           notice="Browser cerró sin devolver resultado.",
                           captcha_required=True,
                           evidence_urls=[self.source_url],
                           elapsed_s=time.time()-t0)

            # Si el hit es un outage del sitio (WAF, "No Disponible",
            # "blocked", etc), reintentar hasta 3 veces con espera
            # de 10-30s entre intentos. El sitio es intermitente.
            def _is_site_outage(h: Hit) -> bool:
                n = (h.notice or "").lower()
                s = (h.summary or "").lower()
                if not n and not s:
                    return False
                signals = [
                    "no responde", "no disponible", "blocked",
                    "the url you requested has been blocked",
                    "browser cerró sin devolver",
                    "sitio procuraduría no responde",
                    "no se encontró select",  # legacy — si pasa al inicio
                ]
                return any(sig in n or sig in s for sig in signals)

            if _is_site_outage(hit):
                max_attempts = 3
                for attempt in range(1, max_attempts):
                    wait_s = 10 + (attempt * 5)  # 15, 20, 25
                    print(f"  [procuraduria] outage detectado "
                          f"(intento {attempt}/{max_attempts}), "
                          f"esperando {wait_s}s antes de reintentar...",
                          flush=True)
                    time.sleep(wait_s)
                    if time.time() - t0 > 65:
                        print(f"  [procuraduria] budget 70s casi agotado, "
                              f"no reintento más.", flush=True)
                        break
                    retry = _run_in_fresh_browser(_do_proc)
                    if retry is None:
                        continue
                    if not _is_site_outage(retry):
                        return retry
                    hit = retry
                # Después de 3 intentos sigue caído
                return hit
            return hit
        except Exception as e:
            return Hit(self.name, False, "",
                       notice=f"Procuraduría error: {type(e).__name__}: {e}.",
                       captcha_required=True,
                       evidence_urls=[self.source_url],
                       elapsed_s=time.time()-t0)
