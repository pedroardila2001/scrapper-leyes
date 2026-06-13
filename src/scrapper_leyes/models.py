"""Domain models: canonical IDs, affectation types, parsed structures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Canonical ID Grammar
# ═══════════════════════════════════════════════════════════════════════════
#
# Format:  co:{tipo_norm}:{numero}:{año}[:art:{art_ref}[:par:{par_num}]]
#
# Examples:
#   co:ley:1712:2014                      → the whole law
#   co:ley:1712:2014:art:1                → article 1
#   co:sentencia:cc:plena:c-274:2013      → Corte Constitucional, Sala Plena, Sentencia C-274/2013
#
# The grammar regex (for validation):
CANONICAL_ID_PATTERN = re.compile(
    r"^co:"
    r"(?P<tipo>[a-z_]+):"
    r"(?:(?P<corte>[a-z_]+):(?P<sala>[a-z0-9_]+):)?"
    r"(?P<numero>[a-z0-9_-]+):"
    r"(?P<anio>\d{4})"
    r"(?::art:(?P<art>(?:trans:\d+|\d+[a-z]?))(?::par:(?P<par>\d+))?)?"
    r"$"
)

# Tipo normalization (from SUIN's uppercase to canonical lowercase)
TIPO_CANONICAL: dict[str, str] = {
    "LEY": "ley",
    "DECRETO": "decreto",
    "ACTO LEGISLATIVO": "acto_legislativo",
    "RESOLUCION": "resolucion",
    "CIRCULAR EXTERNA": "circular_externa",
    "DIRECTIVA PRESIDENCIAL": "directiva_presidencial",
    "CONSTITUCION POLITICA": "constitucion",
    "CODIGO": "codigo",
    "CIRCULAR": "circular",
    "ACUERDO": "acuerdo",
    "INSTRUCCION ADMINISTRATIVA CONJUNTA": "instruccion_admin",
    "RESOLUCION EXTERNA": "resolucion_externa",
    "CIRCULAR CONJUNTA": "circular_conjunta",
    "INSTRUCCION": "instruccion",
    "DIRECTIVA VICEPRESIDENCIAL": "directiva_vicepresidencial",
    "DIRECTIVA MINISTERIAL": "directiva_ministerial",
    "CIRCULAR VICEPRESIDENCIAL": "circular_vicepresidencial",
    "CARTA CIRCULAR": "carta_circular",
}


def build_canonical_id(
    tipo: str,
    numero: str,
    anio: str,
    *,
    corte: str | None = None,
    sala: str | None = None,
    art: str | None = None,
    par: str | None = None,
) -> str:
    """Build a canonical ID string.

    Args:
        tipo: Norm type (uppercase SUIN format, e.g. "LEY", "SENTENCIA")
        numero: Norm number (e.g. "1712", "C-274")
        anio: Year (e.g. "2014")
        corte: High court code (e.g. "cc", "csj", "ce")
        sala: Sala/Seccion code (e.g. "plena", "sec1")
        art: Article reference (e.g. "1", "5a", "trans:1")
        par: Paragraph number (e.g. "2")
    """
    tipo_norm = TIPO_CANONICAL.get(tipo, tipo.lower().replace(" ", "_"))
    
    if corte and sala:
        cid = f"co:{tipo_norm}:{corte.lower()}:{sala.lower()}:{numero.lower()}:{anio}"
    else:
        cid = f"co:{tipo_norm}:{numero.lower()}:{anio}"
        
    if art is not None:
        cid += f":art:{art.lower()}"
        if par is not None:
            cid += f":par:{par.lower()}"
    return cid


def validate_canonical_id(cid: str) -> bool:
    """Check if a canonical ID matches the grammar."""
    return CANONICAL_ID_PATTERN.match(cid) is not None


def parse_canonical_id(cid: str) -> dict[str, str | None] | None:
    """Parse a canonical ID into its components. Returns None if invalid."""
    m = CANONICAL_ID_PATTERN.match(cid)
    if not m:
        return None
    return {
        "tipo": m.group("tipo"),
        "corte": m.group("corte"),
        "sala": m.group("sala"),
        "numero": m.group("numero"),
        "anio": m.group("anio"),
        "art": m.group("art"),
        "par": m.group("par"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Article number normalization
# ═══════════════════════════════════════════════════════════════════════════

# Regex to extract article number from SUIN text like "Artículo 5°." or
# "Artículo Transitorio 1." or "Artículo 5A."
_ART_NUM_RE = re.compile(
    r"Art[ií]culo\s+"
    r"(?:(?P<trans>Transitorio)\s+)?"
    r"(?P<num>\d+)"
    r"(?:[°ºo]\.)?"  # ordinal markers (°, º, o) followed by period
    r"(?P<letter>[A-NP-Za-np-z])?"  # letter suffix (excluding 'o'/'O' which is ordinal)
    r"[.]?",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# SUIN UI noise stripping
# ═══════════════════════════════════════════════════════════════════════════

# Toggle labels and UI leftovers SUIN bakes into the document text. They add
# nothing legal and pollute both the displayed chunks and the embeddings.
_SUIN_NOISE_RE = re.compile(
    r"\[\s*(?:Mostrar|Ocultar)\s*\]"
    r"|TEXTO\s+CORRESPONDIENTE\s+A[^\n]*"
    r"|Afecta\s+la\s+vigencia\s+de\s*:?[ \t]*"
    r"|Legislaci[oó]n\s+Anterior[ \t]*",
    re.IGNORECASE,
)


def strip_suin_ui_noise(text: str) -> str:
    """Remove SUIN toggle/UI artifacts from document text."""
    if not text:
        return text
    text = _SUIN_NOISE_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_article_number(raw_text: str) -> str | None:
    """Extract normalized article ref from raw text like 'Artículo 5°.'

    Returns strings like: '5', '5a', 'trans:1'
    """
    m = _ART_NUM_RE.search(raw_text)
    if not m:
        return None
    num = m.group("num")
    letter = (m.group("letter") or "").lower()
    if m.group("trans"):
        return f"trans:{num}"
    return f"{num}{letter}" if letter else num


# ═══════════════════════════════════════════════════════════════════════════
# Affectation Types (enum + raw mapping)
# ═══════════════════════════════════════════════════════════════════════════


class AffectationType(str, Enum):
    """Controlled vocabulary for norm affectations."""

    MODIFICA = "MODIFICA"
    DEROGA_TOTAL = "DEROGA_TOTAL"
    DEROGA_PARCIAL = "DEROGA_PARCIAL"
    ADICIONA = "ADICIONA"
    CORRIGE_YERRO = "CORRIGE_YERRO"
    EXEQUIBLE = "EXEQUIBLE"
    INEXEQUIBLE = "INEXEQUIBLE"
    EXEQUIBLE_CONDICIONADA = "EXEQUIBLE_CONDICIONADA"
    REGLAMENTA = "REGLAMENTA"
    COMPILA = "COMPILA"
    SUSTITUYE = "SUSTITUYE"
    SUSPENDE = "SUSPENDE"
    PRORROGA = "PRORROGA"
    ACLARA = "ACLARA"
    COMPLEMENTA = "COMPLEMENTA"
    INTERPRETA = "INTERPRETA"
    UNKNOWN = "UNKNOWN"


# Map raw strings (lowercased, stripped) from SUIN HTML → enum
_AFFECTATION_RAW_MAP: dict[str, AffectationType] = {
    "modificado": AffectationType.MODIFICA,
    "modificado parcialmente": AffectationType.MODIFICA,
    "modifica": AffectationType.MODIFICA,
    "derogado": AffectationType.DEROGA_TOTAL,
    "deroga": AffectationType.DEROGA_TOTAL,
    "derogado totalmente": AffectationType.DEROGA_TOTAL,
    "derogado tácitamente": AffectationType.DEROGA_TOTAL,
    "derogado parcialmente": AffectationType.DEROGA_PARCIAL,
    "deroga parcialmente": AffectationType.DEROGA_PARCIAL,
    "adicionado": AffectationType.ADICIONA,
    "adiciona": AffectationType.ADICIONA,
    "corregido yerro": AffectationType.CORRIGE_YERRO,
    "corrección de yerro": AffectationType.CORRIGE_YERRO,
    "declarado exequible": AffectationType.EXEQUIBLE,
    "exequible": AffectationType.EXEQUIBLE,
    "declarado inexequible": AffectationType.INEXEQUIBLE,
    "inexequible": AffectationType.INEXEQUIBLE,
    "declarado condicionalmente exequible": AffectationType.EXEQUIBLE_CONDICIONADA,
    "exequible condicionado": AffectationType.EXEQUIBLE_CONDICIONADA,
    "exequible condicionada": AffectationType.EXEQUIBLE_CONDICIONADA,
    "condicionalmente exequible": AffectationType.EXEQUIBLE_CONDICIONADA,
    "reglamentado": AffectationType.REGLAMENTA,
    "reglamentado parcialmente": AffectationType.REGLAMENTA,
    "reglamenta": AffectationType.REGLAMENTA,
    "compilado": AffectationType.COMPILA,
    "compila": AffectationType.COMPILA,
    "sustituido": AffectationType.SUSTITUYE,
    "sustituye": AffectationType.SUSTITUYE,
    "suspendido": AffectationType.SUSPENDE,
    "suspende": AffectationType.SUSPENDE,
    "prorrogado": AffectationType.PRORROGA,
    "prorroga": AffectationType.PRORROGA,
    "aclarado": AffectationType.ACLARA,
    "aclara": AffectationType.ACLARA,
    "complementa": AffectationType.COMPLEMENTA,
    "complementado": AffectationType.COMPLEMENTA,
    "interpreta": AffectationType.INTERPRETA,
    "interpretado": AffectationType.INTERPRETA,
}


def normalize_affectation_type(raw: str) -> tuple[AffectationType, bool]:
    """Normalize a raw affectation string to the controlled enum.

    Returns:
        (AffectationType, mapped) — mapped=False means it fell through to UNKNOWN
        and should be logged to unmapped_affectations.
    """
    key = raw.strip().lower()
    # Remove trailing whitespace, parens, etc.
    key = re.sub(r"\s+", " ", key).strip()
    if key in _AFFECTATION_RAW_MAP:
        return _AFFECTATION_RAW_MAP[key], True
    # Try prefix match (e.g. "declarado exequible (some note)")
    for pattern, atype in _AFFECTATION_RAW_MAP.items():
        if key.startswith(pattern):
            return atype, True
    return AffectationType.UNKNOWN, False


# ═══════════════════════════════════════════════════════════════════════════
# Parsed document structures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Affectation:
    """A single affectation (modification or jurisprudence) from SUIN."""

    article_affected: str  # "5", "14", "Documento completo", etc.
    raw_type: str  # Raw string from SUIN (for audit)
    normalized_type: AffectationType  # Controlled enum
    mapped: bool  # True if raw_type was successfully mapped
    source_text: str  # "Artículo 1 DECRETO 1494 de 2015"
    source_suin_id: str | None  # Extracted from href (e.g. "30019945")
    source_anchor: str | None  # Fragment (e.g. "ver_30059934")
    context: str | None = None  # Extra text in parens after the type


@dataclass
class TocEntry:
    """Table of contents entry."""

    level: str  # "titulo", "capitulo", "articulo"
    text: str  # "TÍTULO I", "Artículo [1]"
    anchor: str | None  # "ver_1687094"


@dataclass
class ParsedArticle:
    """A single article parsed from SUIN HTML."""

    art_id: str  # SUIN ID of the article (e.g. "1687094")
    number: str  # "1°", "5°", "Transitorio 1", etc.
    number_normalized: str | None  # "1", "5", "trans:1"
    title: str | None  # "Objeto", "Ámbito de aplicación"
    text: str  # Full text of the article
    canonical_id: str  # co:ley:1712:2014:art:1
    notes: list[str] = field(default_factory=list)  # Vigencia notes
    previous_versions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedNorm:
    """Complete parsed result from a SUIN document."""

    suin_id: str
    metadata: dict[str, str]  # All <span field="..."> values
    articles: list[ParsedArticle]
    modifications: list[Affectation]
    jurisprudence: list[Affectation]
    toc: list[TocEntry]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        import dataclasses

        return dataclasses.asdict(self)
@dataclass
class ParsedSentencia(ParsedNorm):
    """Specific structure for jurisprudence/sentences."""

    corte: str | None = None
    sala: str | None = None
    magistrado_ponente: str | None = None
    hechos: str | None = None
    consideraciones: str | None = None
    resuelve: str | None = None
    citaciones: list[str] = field(default_factory=list)
    # the 'articles' field from ParsedNorm can be left empty for sentencias
    # full raw text can be appended/stored as well.
    raw_text: str | None = None
