"""
findings.py — Deriva un "Resumen de hallazgos" (estilo TusDatos) a partir de
los Hit de un run. Lo usan tanto el PDF (report.py) como el HTML/presentación
(build_presentation.py), para tener UNA sola lógica de clasificación.

Salida de `derive_findings(hits, query)`:
  {
    "panel":    {campo: valor}   # datos de identidad (CC, Estado, RUT, …)
    "findings": [ {severity, label, color, novedad, source} … ]  # ordenados
    "counts":   {alto, medio, bajo, informativo}
    "riesgo":   "ALTO" | "MEDIO" | "SIN RIESGO"
  }

Severidad:
  ALTO (rojo)        — sanciones/fugitivos/antecedentes con registro
  MEDIO (ámbar)      — procesos judiciales como demandado
  BAJO (azul)        — demandante / noticias / registros menores
  INFORMATIVO (teal) — identidad vigente, RUT, registros positivos
"""
from __future__ import annotations

SEVERITY = {
    "alto":        ("ALTO",        "#DC2626"),
    "medio":       ("MEDIO",       "#D97706"),
    "bajo":        ("BAJO",        "#2563EB"),
    "informativo": ("INFORMATIVO", "#0E9F6E"),
}
_ORDER = {"alto": 0, "medio": 1, "bajo": 2, "informativo": 3}


def _g(h, k, default=None):
    if isinstance(h, dict):
        return h.get(k, default)
    return getattr(h, k, default)


def _find(hits, *needles, exclude=()):
    """Primer hit cuyo nombre contiene TODOS los needles (case-insensitive)
    y NINGUNO de los `exclude`."""
    for h in hits:
        n = (_g(h, "source", "") or "").lower()
        if all(x in n for x in needles) and not any(e in n for e in exclude):
            return h
    return None


def _detail_val(h, *keys):
    for d in (_g(h, "details", None) or []):
        if isinstance(d, dict):
            for k, v in d.items():
                kl = str(k).lower()
                if any(x in kl for x in keys) and v not in (None, "", "N/A"):
                    return str(v)
    return None


# Clasificación por tipo de fuente (keywords en el nombre)
_SANCION = ("ofac", "onu", "un security", "uk hm", "unión europea", "ue —",
            "sanciones", "bis ", "canadá", "sema")
_FUGITIVO = ("interpol", "fbi", "dea", "europol", "wanted", "buscados",
             "fugitiv", "guardia civil", "ice ", "dss", "cbi", "epa",
             "más buscados")
_CORRUP = ("banco mundial", "debarred", "icij", "panama", "paradise",
           "pandora", "offshore", "bahamas", "fcpa")


def _classify(name: str, h) -> tuple[str, str] | None:
    """Devuelve (severity, novedad) para un hit CON match, o None."""
    n = name.lower()
    summ = (_g(h, "summary", "") or "")
    if "registrad" in n and "defuncion" not in n:
        estado = _detail_val(h, "estado") or "VIGENTE"
        return ("informativo",
                f"Registraduría: el documento consultado se encuentra "
                f"{estado.split('(')[0].strip()} en la Registraduría.")
    if "rut" in n and "dian" in n:
        return ("informativo",
                "RUT: el documento consultado se encuentra en el Registro "
                "Único Tributario.")
    if any(x in n for x in _SANCION):
        return ("alto",
                f"Sanciones: el nombre consultado APARECE en {name}.")
    if any(x in n for x in _FUGITIVO):
        return ("alto",
                f"Fugitivos: el nombre consultado APARECE en {name}.")
    if "contralor" in n:
        return ("alto",
                "Contraloría: registra en el Boletín de Responsables Fiscales.")
    if "procuradur" in n and "antecedente" in n:
        return ("alto",
                "Procuraduría: registra antecedentes disciplinarios o "
                "inhabilidades vigentes.")
    if "policía" in n and "antecedente" in n:
        return ("alto",
                "Policía: registra antecedentes judiciales.")
    if "delitos sexuales" in n or "inhabilidad" in n:
        return ("alto",
                "Ley 1918: registra inhabilidad por delitos sexuales contra "
                "menores.")
    if any(x in n for x in ("rama judicial", "siugj", "juzgado", "tyba", "jepms")):
        sl = summ.lower()
        if "penal" in sl:
            return ("alto",
                    "Rama Judicial: el nombre consultado registra vinculación a "
                    "proceso(s) PENAL(es).")
        dem_do = "demandado" in sl
        dem_te = "demandante" in sl
        if dem_do and dem_te:
            return ("medio",
                    "Rama Judicial: el nombre consultado registra como DEMANDADO "
                    "y como DEMANDANTE en procesos judiciales.")
        if dem_do:
            return ("medio",
                    "Rama Judicial: el nombre consultado registra como "
                    "DEMANDADO en uno o varios procesos.")
        if dem_te:
            return ("bajo",
                    "Rama Judicial: el nombre consultado registra como "
                    "DEMANDANTE en uno o varios procesos.")
        return ("bajo",
                "Rama Judicial: el nombre consultado registra en procesos "
                "judiciales (verificar coincidencia).")
    if "paco" in n or "anticorrupción" in n or "anticorrupcion" in n:
        sl = summ.lower()
        if "sanción" in sl or "sancion" in sl or "fiscal" in sl or "penal" in sl:
            return ("alto",
                    "PACO: el documento registra sanciones (disciplinarias/"
                    "penales/fiscales) en el Portal Anticorrupción.")
        return ("bajo",
                "PACO: el documento figura como contratista del Estado (SECOP).")
    if "noticia" in n or "insight" in n:
        return ("bajo",
                "Reputacional: el nombre consultado aparece en al menos una "
                "noticia o sitio web.")
    if any(x in n for x in _CORRUP):
        return ("medio",
                f"Corrupción internacional: aparece en {name}.")
    if "secop" in n or "proveedores ficticios" in n:
        return ("medio",
                f"Contratación: registra en {name}.")
    # Genérico
    return ("bajo", f"{name}: registra coincidencia — {summ[:90]}")


def _clean(v, strip_paren=False):
    if not v:
        return v
    v = str(v).splitlines()[0].strip()
    # cortar colas de parseo de PDF ("... A nombre de", "Estado", etc.)
    for tail in (" A nombre de", " Estado", " Nombre"):
        if v.endswith(tail):
            v = v[: -len(tail)].strip()
    if strip_paren and "(" in v:
        v = v.split("(")[0].strip()
    return v


def derive_findings(hits, query: dict) -> dict:
    query = query or {}
    reg = (_find(hits, "registrad", "estado de cédula", exclude=("defuncion",))
           or _find(hits, "registrad", "estado", exclude=("defuncion",)))
    rut = _find(hits, "rut", "dian")

    panel = {
        "Nombre": query.get("nombre", "—"),
        "Cédula": query.get("cedula", "—"),
        "Estado cédula": _clean(_detail_val(reg, "estado") if reg else None,
                                strip_paren=True) or "—",
        "Fecha expedición": query.get("fecha_exp")
            or _clean(_detail_val(reg, "fecha_exp", "expedic") if reg else None) or "—",
        "Lugar expedición": _clean(_detail_val(reg, "lugar") if reg else None) or "—",
        "RUT": (_detail_val(rut, "nit", "rut") if rut else None)
            or (query.get("cedula", "") if rut and _g(rut, "matched") else "—"),
        "Estado RUT": _clean(_detail_val(rut, "estado") if rut else None)
            or ("Activo" if (rut and _g(rut, "matched")) else "No consultado"),
    }

    findings = []
    for h in hits:
        if not _g(h, "matched"):
            continue
        name = _g(h, "source", "") or ""
        res = _classify(name, h)
        if not res:
            continue
        sev, novedad = res
        # Coincidencia PARCIAL / posible homónimo → se degrada a
        # INFORMATIVO (estilo TusDatos: "coincidencia parcial ... con
        # fines informativos"), nunca sube el nivel de riesgo global.
        if (_g(h, "status") == "possible_homonym"
                or (_g(h, "confidence") or "") == "posible"):
            sev = "informativo"
            novedad = (f"{name}: coincidencia PARCIAL por nombre (posible "
                       "homónimo). Se presenta con fines informativos; "
                       "verificar identidad.")
        label, color = SEVERITY[sev]
        findings.append({"severity": sev, "label": label, "color": color,
                         "novedad": novedad, "source": name})

    findings.sort(key=lambda f: (_ORDER.get(f["severity"], 9), f["source"]))
    counts = {k: sum(1 for f in findings if f["severity"] == k)
              for k in SEVERITY}
    if counts["alto"]:
        riesgo = "ALTO"
    elif counts["medio"]:
        riesgo = "MEDIO"
    elif counts["bajo"]:
        riesgo = "BAJO"
    else:
        riesgo = "SIN RIESGO"
    return {"panel": panel, "findings": findings, "counts": counts,
            "riesgo": riesgo}
