"""
solvers/trivia.py — Solver local para captchas de "trivia" matemática
y de conocimiento general (capitales, colores, etc).

La Procuraduría usa captchas de pregunta simple tipo
"¿Cuál es la Capital de Antioquia?". Este solver las resuelve:
  1) Primero intenta math local (sin red)
  2) Si falla, intenta Anthropic Haiku 4.5 si ANTHROPIC_API_KEY está
     configurada en config.yaml
  3) Si no, lanza CaptchaUnsolved
"""
from __future__ import annotations
import re
from typing import Optional
from .base import CaptchaSolver, CaptchaUnsolved


class TriviaSolver(CaptchaSolver):
    """Solver de trivia matemática + conocimiento general (vía Anthropic)."""

    def __init__(self, anthropic_api_key: Optional[str] = None,
                 model: str = "claude-haiku-4-5"):
        self.anthropic_api_key = anthropic_api_key
        self.model = model
        self._client = None

    @property
    def name(self) -> str:
        return "TriviaSolver"

    def is_available(self) -> bool:
        return True   # Siempre disponible para math; LLM es bonus

    def _get_client(self):
        if self._client is None and self.anthropic_api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=self.anthropic_api_key)
            except Exception:
                self._client = False
        return self._client or None

    def solve_image(self, png_bytes, **kwargs) -> str:
        raise CaptchaUnsolved(
            "TriviaSolver no resuelve imágenes. "
            "Use solve_trivia(question) para preguntas de texto.")

    def solve_recaptcha_v2(self, sitekey, page_url, **kwargs) -> str:
        raise CaptchaUnsolved("TriviaSolver no resuelve recaptcha_v2.")

    def solve_hcaptcha(self, sitekey, page_url, **kwargs) -> str:
        raise CaptchaUnsolved("TriviaSolver no resuelve hcaptcha.")

    def solve_trivia(self, question: str, context: str | None = None,
                     **kwargs) -> str:
        """Resuelve trivia. Prioridad:
          1) Operación matemática local (sin red).
          2) Respuesta derivada del nombre de la persona (`context`), si la
             pregunta es de tipo "iniciales / N primeras letras / primer
             nombre / apellido" — determinística, sin red.
          3) Anthropic (Haiku) con el nombre como contexto y prompt estricto.

        `context` = nombre completo de la persona consultada (para las
        preguntas del tipo "iniciales de la persona consultada", etc.).
        """
        # Intento 1: math
        math_ans = _eval_math_question(question)
        if math_ans is not None:
            return str(math_ans)
        # Intento 2: capital de departamento/país — diccionario local con
        # matching difuso (tolera typos de la propia fuente, p.ej.
        # "Capital del Vallle del Cauca" con triple L).
        cap_ans = answer_capital(question)
        if cap_ans:
            return cap_ans
        # Intento 3: derivar del nombre (determinístico)
        if context:
            name_ans = answer_from_name(question, context)
            if name_ans:
                return name_ans
        # Intento 3: Anthropic con contexto
        client = self._get_client()
        if client:
            try:
                ctx_line = (
                    f"NOMBRE COMPLETO de la persona consultada: \"{context}\".\n"
                    if context else "")
                prompt = (
                    "Estás resolviendo la pregunta de un captcha de un trámite "
                    "web colombiano. " + ctx_line +
                    "Responde ÚNICAMENTE con la respuesta exacta que espera el "
                    "formulario: sin explicación, sin comillas, sin punto final.\n"
                    "Reglas de formato:\n"
                    "- Operación matemática → solo el número (ej. 8).\n"
                    "- Iniciales / letras / nombres / apellidos de la persona → "
                    "derívalos del NOMBRE COMPLETO de arriba, en MAYÚSCULAS y sin "
                    "espacios ni puntos (ej. iniciales de \"Ana Perez Ruiz\" → APR; "
                    "dos primeras letras de \"Daniel\" → DA).\n"
                    "- Conocimiento general (capital, color, día…) → solo la palabra.\n"
                    f"Pregunta: {question}")
                resp = client.messages.create(
                    model=self.model, max_tokens=50,
                    messages=[{"role": "user", "content": prompt}],
                )
                txt = resp.content[0].text.strip()
                txt = re.sub(r"[\"'.]+$", "", txt).strip()
                txt = txt.splitlines()[0].strip() if txt else txt
                if txt:
                    return txt
            except Exception:
                pass
        raise CaptchaUnsolved(
            f"TriviaSolver no pudo resolver: {question!r}")


_WORD_NUM = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "primera": 1, "primeras": 1,
}


def _strip_accents(s: str) -> str:
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"),
                 ("ú", "u"), ("ü", "u")):
        s = s.replace(a, b).replace(a.upper(), b.upper())
    return s


# Capitales de los 32 departamentos de Colombia + país. Claves SIN tildes
# y en minúsculas (se comparan contra la pregunta normalizada).
_CAPITALES = {
    "amazonas": "Leticia", "antioquia": "Medellín", "arauca": "Arauca",
    "atlantico": "Barranquilla", "bolivar": "Cartagena", "boyaca": "Tunja",
    "caldas": "Manizales", "caqueta": "Florencia", "casanare": "Yopal",
    "cauca": "Popayán", "cesar": "Valledupar", "choco": "Quibdó",
    "cordoba": "Montería", "cundinamarca": "Bogotá", "guainia": "Inírida",
    "guaviare": "San José del Guaviare", "huila": "Neiva",
    "la guajira": "Riohacha", "guajira": "Riohacha",
    "magdalena": "Santa Marta", "meta": "Villavicencio",
    "narino": "Pasto", "norte de santander": "Cúcuta",
    "putumayo": "Mocoa", "quindio": "Armenia", "risaralda": "Pereira",
    "san andres": "San Andrés", "san andres y providencia": "San Andrés",
    "santander": "Bucaramanga", "sucre": "Sincelejo", "tolima": "Ibagué",
    "valle del cauca": "Cali", "valle": "Cali",
    "vaupes": "Mitú", "vichada": "Puerto Carreño",
    "colombia": "Bogotá",
}


def answer_capital(question: str) -> Optional[str]:
    """Responde '¿Cuál es la capital de X?' con diccionario local y matching
    difuso (difflib) tolerante a typos, tildes y mayúsculas.

    Caso real que motivó esto: la Procuraduría mostró la pregunta
    "¿Cual es la Capital del Vallle del Cauca?" (sic, triple 'l') y el
    solver la trataba como irresoluble → la fuente quedaba como 'bloqueo'.
    """
    if not question:
        return None
    q = _strip_accents(question.lower())
    if "capital" not in q:
        return None
    # Tomar lo que sigue a "capital de/del/de la/of"
    m = re.search(r"capital\s+(?:de\s+la\s+|del\s+|de\s+|of\s+)?(.+)", q)
    if not m:
        return None
    lugar = m.group(1)
    # limpiar signos, colas tipo "(sin tilde)" y espacios
    lugar = re.sub(r"\(.*?\)", " ", lugar)
    lugar = re.sub(r"[¿?¡!.,;:]", " ", lugar)
    lugar = re.sub(r"\s+", " ", lugar).strip()
    if not lugar:
        return None
    sin_tilde = "sin tilde" in q or "sin tildes" in q
    import difflib

    def _fmt(ans: str) -> str:
        return _strip_accents(ans) if sin_tilde else ans

    if lugar in _CAPITALES:
        return _fmt(_CAPITALES[lugar])
    # Fuzzy: contra el texto completo y contra prefijos por palabras
    # ("vallle del cauca" ≈ "valle del cauca").
    candidatos = difflib.get_close_matches(lugar, _CAPITALES.keys(),
                                           n=1, cutoff=0.75)
    if candidatos:
        return _fmt(_CAPITALES[candidatos[0]])
    palabras = lugar.split()
    for n in range(len(palabras), 0, -1):
        frag = " ".join(palabras[:n])
        candidatos = difflib.get_close_matches(frag, _CAPITALES.keys(),
                                               n=1, cutoff=0.8)
        if candidatos:
            return _fmt(_CAPITALES[candidatos[0]])
    return None


def _split_name(nombre: str):
    """Divide un nombre completo colombiano en (nombres, apellidos).
    Heurística: los ÚLTIMOS 2 tokens son apellidos; el resto, nombres.
    Con 2 tokens → 1 nombre + 1 apellido; con 1 token → solo nombre."""
    toks = [re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", t)
            for t in nombre.strip().split()]
    toks = [t for t in toks if t]
    if not toks:
        return [], []
    if len(toks) == 1:
        return [toks[0]], []
    if len(toks) == 2:
        return [toks[0]], [toks[1]]
    if len(toks) == 3:
        return [toks[0]], toks[1:]
    return toks[:-2], toks[-2:]


def answer_from_name(question: str, nombre: str) -> Optional[str]:
    """Resuelve determinísticamente preguntas de trivia derivadas del nombre.
    Devuelve la respuesta (MAYÚSCULAS) o None si no aplica.

    Cubre: iniciales; primer/segundo/último nombre o apellido; nombre/apellidos
    completos; N primeras/últimas letras de un token; primera/última letra;
    número de letras (conteo)."""
    if not question or not nombre:
        return None
    q = _strip_accents(question.lower())
    nombres, apellidos = _split_name(nombre)
    todos = nombres + apellidos
    if not todos:
        return None

    # --- Iniciales ---
    if "inicial" in q or "siglas" in q:
        if "apellido" in q and "nombre" not in q:
            base = apellidos
        elif "nombre" in q and "apellido" not in q:
            base = nombres
        else:
            base = todos
        return "".join(t[0] for t in base if t).upper()

    # --- Número de letras (conteo) ---
    if ("cuantas letras" in q or "numero de letras" in q
            or "cantidad de letras" in q):
        target = _pick_token(q, nombres, apellidos, todos, nombre)
        if target is not None:
            return str(len(target))

    # --- Elegir el token objetivo ---
    target = _pick_token(q, nombres, apellidos, todos, nombre)
    if target is None:
        return None

    # --- Operación sobre el token ---
    # ¿cuántas letras? (por si el patrón vino después)
    m = re.search(r"(\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|primera[s]?)\s+(?:primeras?\s+)?letras?", q)
    if not m:
        m = re.search(r"primeras?\s+(\d+|dos|tres|cuatro|cinco)\s+letras?", q)
    if "ultima letra" in q or "ultima letra" in q:
        return target[-1].upper()
    if "primera letra" in q and "letras" not in q.split("primera letra")[-1][:6]:
        return target[0].upper()
    if m:
        tok = m.group(1)
        n = int(tok) if tok.isdigit() else _WORD_NUM.get(tok, 1)
        n = max(1, min(n, len(target)))
        if "ultima" in q or "ultimas" in q:
            return target[-n:].upper()
        return target[:n].upper()

    # --- Sin operación → palabra completa ---
    return target.upper()


def _pick_token(q: str, nombres, apellidos, todos, nombre_full) -> Optional[str]:
    """Elige el token (o cadena) al que se refiere la pregunta."""
    def g(lst, i):
        return lst[i] if 0 <= i < len(lst) else None
    # Nombre / apellido completos
    if "nombre completo" in q or "nombre y apellido" in q:
        return re.sub(r"\s+", "", nombre_full)
    if "apellidos" in q and "primer" not in q and "segundo" not in q and "ultimo" not in q:
        return "".join(apellidos) if apellidos else None
    if "segundo apellido" in q:
        return g(apellidos, 1)
    if "primer apellido" in q or ("apellido" in q and "segundo" not in q and "nombre" not in q):
        return g(apellidos, 0)
    if "segundo nombre" in q:
        return g(nombres, 1)
    if "ultimo nombre" in q:
        return g(nombres, -1) if nombres else None
    if "primer nombre" in q or ("nombre" in q and "apellido" not in q):
        return g(nombres, 0)
    return None


def _eval_math_question(text: str) -> Optional[int]:
    """Intenta extraer y resolver una operación matemática de un texto."""
    if not text:
        return None
    t = text.lower()
    for pre in ("cuanto es", "cuánto es", "cual es", "cuál es",
                "resultado de", "=", "es", "valor de", "?"):
        t = t.replace(pre, " ")
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u")):
        t = t.replace(a, b)
    t = re.sub(r"[^0-9+\-*/(). x]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.search(r"(\d+)\s*([+\-*/x])\s*(\d+)", t)
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == "+": return a + b
    if op == "-": return a - b
    if op == "*" or op == "x": return a * b
    if op == "/":
        if b == 0: return None
        return a // b
    return None
