"""Taxonomía de entidades del Estado colombiano (biblioteca).

Clasifica cada norma/sentencia en un árbol de 3 niveles
``Rama → sector/cabeza → entidad`` para navegar el corpus "como una biblioteca".

Enfoque híbrido pragmático:
  * Tabla curada de sectores administrativos conocidos → Rama.
  * Reparación de *mojibake* (UTF-8 mal decodificado: "CrÃ©dito" → "Crédito").
  * Normalización por plegado de acentos + may/min para fusionar variantes
    ("CONGRESO DE LA REPÚBLICA" == "Congreso de la Republica").
  * Inferencia desde la entidad cuando falta el sector.
  * Lo que no mapea cae en la rama "Otros".

Módulo puro (sin dependencias pesadas) para poder testearlo y reutilizarlo desde
la API y la migración del catálogo.
"""

from __future__ import annotations

import re
import unicodedata

OTROS = "Otros"

RAMA_EJECUTIVA = "Rama Ejecutiva"
RAMA_LEGISLATIVA = "Rama Legislativa"
RAMA_JUDICIAL = "Rama Judicial"
ORGANO_CONTROL = "Organismos de Control"
ORGANO_AUTONOMO = "Órgano Autónomo"
ORGANO_ELECTORAL = "Órgano Electoral"

# Orden de presentación de las ramas en la biblioteca.
RAMA_ORDER = [
    RAMA_EJECUTIVA,
    RAMA_LEGISLATIVA,
    RAMA_JUDICIAL,
    ORGANO_CONTROL,
    ORGANO_AUTONOMO,
    ORGANO_ELECTORAL,
    "Internacional",
    OTROS,
]


# ── Reparación de texto / normalización ─────────────────────────────────────


def repair_text(s: str) -> str:
    """Repara mojibake típico (bytes UTF-8 leídos como Latin-1)."""
    if not s:
        return s
    if "Ã" in s or "Â" in s or " Â" in s:
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s


def _fold(s: str) -> str:
    """Clave de comparación: sin acentos, minúsculas, espacios colapsados."""
    s = repair_text(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _title(s: str) -> str:
    """Display name: mojibake reparado y espacios normalizados (preserva acentos)."""
    s = repair_text(s or "").strip()
    return re.sub(r"\s+", " ", s)


# Palabras que van en minúscula y siglas que van en mayúscula al "embellecer".
_LOWER_WORDS = {"de", "del", "la", "las", "los", "y", "e", "en", "el", "para", "a"}
_UPPER_WORDS = {"dian", "dimar", "icbf", "sena", "creg", "une", "s.a.", "sa"}


def _pretty(name: str) -> str:
    """Convierte 'MINISTERIO DE HACIENDA' → 'Ministerio de Hacienda' (Title Case ES)."""
    name = _title(name)
    if not name:
        return name
    # Si ya viene en mayús/minús mixto razonable, respetarlo.
    letters = [c for c in name if c.isalpha()]
    if letters and not (name.isupper() or name.islower()):
        return name
    words = name.split(" ")
    out: list[str] = []
    for i, w in enumerate(words):
        wl = w.lower()
        if wl in _UPPER_WORDS:
            out.append(w.upper())
        elif i > 0 and wl in _LOWER_WORDS:
            out.append(wl)
        else:
            out.append(wl[:1].upper() + wl[1:] if wl else w)
    return " ".join(out)


def _accent_score(s: str) -> int:
    """Cuántos caracteres acentuados tiene (para elegir el mejor display)."""
    return sum(1 for c in s if unicodedata.normalize("NFKD", c) != c)


# ── Sectores administrativos (Rama Ejecutiva) ───────────────────────────────
# clave plegada → nombre display canónico.

_SECTOR_EJECUTIVA: dict[str, str] = {
    "agricultura y desarrollo rural": "Agricultura y Desarrollo Rural",
    "ambiente y desarrollo sostenible": "Ambiente y Desarrollo Sostenible",
    "ciencia tecnologia e innovacion": "Ciencia, Tecnología e Innovación",
    "comercio industria y turismo": "Comercio, Industria y Turismo",
    "cultura": "Cultura",
    "defensa nacional": "Defensa Nacional",
    "excelencia militar": "Defensa Nacional",
    "deporte": "Deporte",
    "educacion nacional": "Educación Nacional",
    "educacion": "Educación Nacional",
    "estadistica": "Estadística",
    "funcion publica": "Función Pública",
    "hacienda y credito publico": "Hacienda y Crédito Público",
    "igualdad y equidad": "Igualdad y Equidad",
    "inclusion social y reconciliacion": "Inclusión Social y Reconciliación",
    "inteligencia estrategica y contrainteligencia": "Inteligencia Estratégica y Contrainteligencia",
    "interior": "Interior",
    "justicia y del derecho": "Justicia y del Derecho",
    "minas y energia": "Minas y Energía",
    "planeacion": "Planeación",
    "presidencia de la republica": "Presidencia de la República",
    "relaciones exteriores": "Relaciones Exteriores",
    "salud y proteccion social": "Salud y Protección Social",
    "tecnologias de la informacion y de las comunicaciones": "Tecnologías de la Información y las Comunicaciones",
    "trabajo": "Trabajo",
    "transporte": "Transporte",
    "vivienda ciudad y territorio": "Vivienda, Ciudad y Territorio",
}

# Sectores que NO son Rama Ejecutiva → (rama, cabeza display).
_SECTOR_ESPECIAL: dict[str, tuple[str, str]] = {
    "congreso de la republica": (RAMA_LEGISLATIVA, "Congreso de la República"),
    "rama judicial": (RAMA_JUDICIAL, "Rama Judicial"),
    "entes de control": (ORGANO_CONTROL, "Organismos de Control"),
    "organismos autonomos e independientes": (ORGANO_AUTONOMO, "Organismos Autónomos e Independientes"),
    "organizacion electoral": (ORGANO_ELECTORAL, "Organización Electoral"),
}

# Cortes/altos tribunales → cabeza dentro de Rama Judicial.
_CORTE_CABEZA = {
    "cc": "Corte Constitucional",
    "csj": "Corte Suprema de Justicia",
    "ce": "Consejo de Estado",
}


# ── Clasificación ───────────────────────────────────────────────────────────


def normalize_sector(raw: str | None) -> tuple[str, str] | None:
    """Devuelve (rama, cabeza) para un sector crudo, o None si no se reconoce."""
    if not raw:
        return None
    key = _fold(raw)
    if key in _SECTOR_ESPECIAL:
        return _SECTOR_ESPECIAL[key]
    if key in _SECTOR_EJECUTIVA:
        return (RAMA_EJECUTIVA, _SECTOR_EJECUTIVA[key])
    return None


def normalize_entidad(raw: str | None) -> str:
    """Nombre display de una entidad, fusionando variantes conocidas."""
    if not raw:
        return "Sin entidad"
    key = _fold(raw)
    # Fusionar las muchas variantes del legislativo.
    if (
        key.startswith("congreso")
        or key.startswith("poder legislativo")
        or key.startswith("consejo nacional legislativo")
        or key.startswith("asamblea nacional")
        or "organo legislativo" in key
    ):
        return "Congreso de la República"
    if key.startswith("poder ejecutivo") or key == "presidencia de la republica":
        return "Presidencia de la República"
    return _title(raw)


def _rama_from_entidad(entidad: str | None) -> tuple[str | None, str | None]:
    """(rama, cabeza) inferida del EMISOR. (None, None) si no es concluyente.

    Para entidades de la Rama Ejecutiva devuelve cabeza=None: la cabeza
    (sector/ministerio que las agrupa) se decide con el campo ``sector``.
    """
    key = _fold(entidad or "")
    if not key:
        return (None, None)

    # Legislativo (emisor de leyes/actos legislativos).
    if (
        key.startswith("congreso")
        or key.startswith("poder legislativo")
        or key.startswith("consejo nacional legislativo")
        or key.startswith("consejo nacional constituyente")
        or key.startswith("asamblea nacional")
        or "organo legislativo" in key
    ):
        return (RAMA_LEGISLATIVA, "Congreso de la República")

    # Judicial.
    if "corte constitucional" in key:
        return (RAMA_JUDICIAL, "Corte Constitucional")
    if "corte suprema" in key:
        return (RAMA_JUDICIAL, "Corte Suprema de Justicia")
    if "consejo de estado" in key or "contencioso administrativo" in key:
        return (RAMA_JUDICIAL, "Consejo de Estado")
    if (
        "fiscalia" in key
        or "rama judicial" in key
        or "disciplina judicial" in key
        or "jurisdiccion especial para la paz" in key
        or "centro de arbitraje" in key
    ):
        return (RAMA_JUDICIAL, "Rama Judicial")

    # Organismos de control.
    if (
        "procuraduria" in key
        or "contraloria" in key
        or "defensoria del pueblo" in key
        or "ministerio publico" in key
        or "auditoria general" in key
    ):
        return (ORGANO_CONTROL, "Organismos de Control")

    # Órgano electoral.
    if "consejo nacional electoral" in key or "registraduria" in key:
        return (ORGANO_ELECTORAL, "Organización Electoral")

    # Órganos autónomos.
    if (
        "banco de la republica" in key
        or "comision nacional del servicio civil" in key
        or "corporacion" in key and "autonoma" in key
        or "consejo profesional" in key
        or "unidad de busqueda de personas" in key
    ):
        return (ORGANO_AUTONOMO, "Organismos Autónomos e Independientes")

    # Internacional.
    if "comunidad andina" in key or "organizacion internacional" in key:
        return ("Internacional", "Organismos Internacionales")

    # Emisores claramente ejecutivos → cabeza la decide el sector.
    if (
        key.startswith("ministerio")
        or key.startswith("departamento administrativo")
        or key.startswith("superintendencia")
        or key.startswith("agencia")
        or key.startswith("unidad administrativa")
        or key.startswith("instituto")
        or key.startswith("comision de regulacion")
        or key.startswith("presidencia")
        or key.startswith("poder ejecutivo")
        or key.startswith("direccion")
    ):
        return (RAMA_EJECUTIVA, None)

    return (None, None)


def entidad_key(entidad: str | None) -> str:
    """Clave estable (plegada) de una entidad, para filtrar/agrupar en SQL."""
    return _fold(entidad) or "sin-entidad"


def classify(
    tipo: str | None,
    sector: str | None,
    entidad: str | None,
    corte: str | None = None,
) -> tuple[str, str, str]:
    """Clasifica un documento en (rama, cabeza, entidad_display) por EMISOR.

    Sentencias → Rama Judicial según su corte. Para normas, el emisor (entidad)
    decide la rama; las entidades ejecutivas se agrupan por sector administrativo.
    """
    if (tipo or "").upper() == "SENTENCIA":
        cabeza = _CORTE_CABEZA.get((corte or "").lower(), "Rama Judicial")
        return (RAMA_JUDICIAL, cabeza, cabeza)

    entidad_disp = normalize_entidad(entidad)
    rama_e, cabeza_e = _rama_from_entidad(entidad)

    if rama_e == RAMA_EJECUTIVA:
        # Cabeza = sector administrativo (agrupa ministerio + sus agencias).
        sec = normalize_sector(sector)
        if sec is not None and sec[0] == RAMA_EJECUTIVA:
            return (RAMA_EJECUTIVA, sec[1], entidad_disp)
        return (RAMA_EJECUTIVA, cabeza_e or "Otras entidades ejecutivas", entidad_disp)

    if rama_e is not None:
        return (rama_e, cabeza_e or rama_e, entidad_disp)

    # Emisor no concluyente → usar el sector como respaldo.
    sec = normalize_sector(sector)
    if sec is not None:
        return (sec[0], sec[1], entidad_disp)
    return (OTROS, OTROS, entidad_disp)


# ── Construcción del árbol biblioteca ───────────────────────────────────────


def build_library_tree(rows: list[dict]) -> dict:
    """Agrega filas de catálogo en el árbol Rama → cabeza → entidad con conteos.

    ``rows`` items necesitan: tipo, sector, entidad, corte.
    """
    tree: dict[str, dict] = {}
    total = 0
    for r in rows:
        rama, cabeza, entidad = classify(
            r.get("tipo"), r.get("sector"), r.get("entidad"), r.get("corte")
        )
        ent_key = _fold(entidad) or "sin-entidad"
        total += 1
        rama_node = tree.setdefault(rama, {"nombre": rama, "total": 0, "_sec": {}})
        rama_node["total"] += 1
        sec_node = rama_node["_sec"].setdefault(
            cabeza, {"nombre": cabeza, "total": 0, "_ent": {}}
        )
        sec_node["total"] += 1
        # Agrupar entidades por clave plegada; recordar candidatos de display.
        ent_node = sec_node["_ent"].setdefault(
            ent_key, {"key": ent_key, "total": 0, "_disp": {}}
        )
        ent_node["total"] += 1
        ent_node["_disp"][entidad] = ent_node["_disp"].get(entidad, 0) + 1

    def _rama_sort_key(name: str) -> tuple[int, str]:
        try:
            return (RAMA_ORDER.index(name), "")
        except ValueError:
            return (len(RAMA_ORDER), name)

    def _best_display(disp_counts: dict[str, int]) -> str:
        # Preferir el candidato con más acentos, luego el más frecuente.
        best = max(disp_counts, key=lambda d: (_accent_score(d), disp_counts[d]))
        return _pretty(best)

    ramas = []
    for rama_name in sorted(tree, key=_rama_sort_key):
        rama_node = tree[rama_name]
        sectores = []
        for sec in sorted(rama_node["_sec"].values(), key=lambda s: -s["total"]):
            entidades = [
                {"nombre": _best_display(e["_disp"]), "key": e["key"], "total": e["total"]}
                for e in sec["_ent"].values()
            ]
            entidades.sort(key=lambda e: (-e["total"], e["nombre"]))
            sectores.append(
                {"nombre": sec["nombre"], "total": sec["total"], "entidades": entidades}
            )
        ramas.append(
            {"nombre": rama_node["nombre"], "total": rama_node["total"], "sectores": sectores}
        )

    return {"total": total, "ramas": ramas}
