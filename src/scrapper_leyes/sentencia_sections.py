"""Heading-driven sectionizer for sentencias (court rulings).

Why this module exists
----------------------
A sentencia is NOT split by article/título like a law. Its real structure
lives in its markdown headings: Colombian high courts number the top-level
parts with Roman numerals ("# I. ANTECEDENTES", "# VII. CONSIDERACIONES Y
FUNDAMENTOS", "# VIII. DECISIÓN") and subsections with arabic numbers
("## 1. Competencia", "## 3. Análisis material.").

The old heuristic in ``legal_mapper`` collapsed everything into three buckets
(hechos / consideraciones / resuelve) with a fragile regex that required the
keyword to sit *immediately* after the ``#`` markers — so "# VIII. DECISIÓN"
never matched (the "VIII." sits in between) and the whole ruling (hundreds of
thousands of characters) was dumped into ``consideraciones``. That is the root
of the "Consideraciones (74/75)" problem: one giant undifferentiated bucket.

This module walks the markdown headings, normalizes each heading to a stable
section type, and returns an ordered list of :class:`Section` objects. It keeps
TWO signals per section, never conflating them:

  * ``original_heading`` — exactly what the court wrote ("VIII. DECISIÓN").
  * ``normalized_section`` — our stable enum (``PARTE_RESOLUTIVA``).

It is a pure function (no I/O, no heavy deps) so it is cheap to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Normalized section vocabulary ───────────────────────────────────────────
# Stable identifiers. New courts/heading wordings map onto these; we never
# invent a label on the fly (keeps the graph/chunk taxonomy closed & queryable).

ENCABEZADO = "ENCABEZADO"  # preamble before the first heading (court, M.P., exp.)
ANTECEDENTES = "ANTECEDENTES"
DEMANDA = "DEMANDA"
NORMA_DEMANDADA = "NORMA_DEMANDADA"
INTERVENCIONES = "INTERVENCIONES"
CONCEPTO_PROCURADURIA = "CONCEPTO_PROCURADURIA"
CONSIDERACIONES = "CONSIDERACIONES"
COMPETENCIA = "COMPETENCIA"
CUESTION_PREVIA = "CUESTION_PREVIA"
PROBLEMA_JURIDICO = "PROBLEMA_JURIDICO"
MARCO_NORMATIVO = "MARCO_NORMATIVO"
ANALISIS = "ANALISIS"
CASO_CONCRETO = "CASO_CONCRETO"
PARTE_RESOLUTIVA = "PARTE_RESOLUTIVA"
SALVAMENTO_DE_VOTO = "SALVAMENTO_DE_VOTO"
ACLARACION_DE_VOTO = "ACLARACION_DE_VOTO"
FIRMAS = "FIRMAS"
OTRO = "OTRO"

# Sections that, together, make up the legacy "hechos" (everything before the
# court's own reasoning) and "consideraciones" buckets — used to derive the
# backward-compatible fields the frontend still reads.
_HECHOS_SECTIONS = (
    ANTECEDENTES,
    DEMANDA,
    NORMA_DEMANDADA,
    INTERVENCIONES,
    CONCEPTO_PROCURADURIA,
)
_CONSIDERACIONES_SECTIONS = (
    CONSIDERACIONES,
    COMPETENCIA,
    CUESTION_PREVIA,
    PROBLEMA_JURIDICO,
    MARCO_NORMATIVO,
    ANALISIS,
    CASO_CONCRETO,
)
_SEPARATE_OPINION_SECTIONS = (SALVAMENTO_DE_VOTO, ACLARACION_DE_VOTO)

# Ordered keyword rules: the FIRST whose pattern is found in the cleaned heading
# wins. Order matters — more specific phrases come before broader ones
# (e.g. "salvamento de voto" before a bare "voto"; "problema jurídico" before
# "análisis"; "concepto del procurador" before a generic "concepto").
_HEADING_RULES: list[tuple[str, re.Pattern[str]]] = [
    (SALVAMENTO_DE_VOTO, re.compile(r"salvamento\s+(?:parcial\s+)?de\s+voto")),
    (ACLARACION_DE_VOTO, re.compile(r"aclaraci[oó]n\s+(?:parcial\s+)?de\s+voto")),
    (PARTE_RESOLUTIVA, re.compile(r"\b(decisi[oó]n|resuelve|resoluci[oó]n|falla|fallo|parte\s+resolutiva)\b")),
    (CONCEPTO_PROCURADURIA, re.compile(r"concepto\b.*procurad|ministerio\s+p[uú]blico|vista\s+fiscal|procurador")),
    (NORMA_DEMANDADA, re.compile(r"texto\b.*norma|norma[s]?\s+demandad|disposici[oó]n[es]*\s+demandad|texto\s+legal")),
    (INTERVENCIONES, re.compile(r"intervenci[oó]n|intervinient")),
    (DEMANDA, re.compile(r"\b(la\s+demanda|demanda\b|pretensiones|cargos\s+de\s+la\s+demanda)\b")),
    (ANTECEDENTES, re.compile(r"antecedent|hechos\b|s[ií]ntesis\s+de\s+los\s+hechos")),
    (PROBLEMA_JURIDICO, re.compile(r"problema[s]?\s+jur[ií]dico|cuesti[oó]n\s+a\s+resolver|asunto\s+bajo\s+revisi[oó]n")),
    (COMPETENCIA, re.compile(r"\bcompetencia\b")),
    (CUESTION_PREVIA, re.compile(r"cuesti[oó]n\s+previa|aptitud\s+de\s+la\s+demanda|aptitud\s+sustantiva|asunto[s]?\s+preliminar")),
    (CASO_CONCRETO, re.compile(r"caso\s+concreto|an[aá]lisis\s+del\s+caso|soluci[oó]n\s+del\s+caso")),
    (MARCO_NORMATIVO, re.compile(r"marco\s+(?:normativo|jur[ií]dico|constitucional)|precedente|reiteraci[oó]n\s+de\s+jurisprudencia|fundamento[s]?\s+normativo")),
    (ANALISIS, re.compile(r"an[aá]lisis|examen|estudio\b|consideraci[oó]n\s+de\s+la\s+(?:sala|corte)")),
    (CONSIDERACIONES, re.compile(r"consideraci[oó]n|fundamento")),
    (FIRMAS, re.compile(r"\bfirma[s]?\b|notif[ií]quese")),
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Strip leading enumeration: roman numerals, arabic numbers, letters, ordinal
# marks and surrounding punctuation — "VIII. ", "1. ", "2.2.2. ", "A) ", "1°.-".
_ENUM_PREFIX_RE = re.compile(
    r"^\s*(?:[*_#]+\s*)?"  # stray markdown emphasis/markers
    r"(?:[IVXLCDM]+|[0-9]+(?:\.[0-9]+)*|[A-Za-z])"  # roman | numbered | single letter
    # A separator is MANDATORY, else we'd strip the first letter of any word
    # ("RESUELVE" → "ESUELVE"). Accept punctuation (optionally + space) OR space.
    r"(?:[\.\)\-º°]+\s*|\s+)",
    re.IGNORECASE,
)
_MD_EMPHASIS_RE = re.compile(r"[*_`]+")


@dataclass
class Section:
    """One contiguous, heading-bounded part of a sentencia."""

    seq: int  # 0-based position in document order
    level: int  # markdown heading depth (1 = top-level "#"); 0 for preamble
    original_heading: str  # exactly as the court wrote it ("VIII. DECISIÓN")
    normalized_section: str  # stable enum (PARTE_RESOLUTIVA)
    text: str  # body of the section (heading line excluded)
    parent_seq: int | None = None  # nearest ancestor heading at a shallower level

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "level": self.level,
            "original_heading": self.original_heading,
            "normalized_section": self.normalized_section,
            "text": self.text,
            "parent_seq": self.parent_seq,
        }


def clean_heading(raw: str) -> str:
    """Strip markdown emphasis and leading enumeration from a heading line.

    "## **2.2.2.** Análisis material." → "Análisis material"
    """
    s = _MD_EMPHASIS_RE.sub("", raw or "").strip()
    s = _ENUM_PREFIX_RE.sub("", s).strip()
    return s.rstrip(".:;-").strip()


def classify_heading(raw: str) -> str | None:
    """Map a heading to a normalized section, or ``None`` if nothing matches.

    Matching is keyword-based on the *cleaned* heading and is intentionally
    forgiving (handles Roman/arabic numbering and bold markers). Returns
    ``None`` so the caller can decide to inherit the parent's section.
    """
    cleaned = clean_heading(raw).lower()
    if not cleaned:
        return None
    for label, pattern in _HEADING_RULES:
        if pattern.search(cleaned):
            return label
    return None


def split_sections(md_text: str) -> list[Section]:
    """Split a sentencia's markdown into ordered, normalized sections.

    Every markdown heading starts a new section. A heading that matches no
    keyword rule *inherits* the normalized section of its nearest shallower
    ancestor (so "## 7. Intervención del Ministerio…" stays ``INTERVENCIONES``
    and "## 3. Análisis material." resolves to ``ANALISIS``). Text before the
    first heading becomes an ``ENCABEZADO`` section.

    Returns ``[]`` for empty input.
    """
    text = (md_text or "").strip()
    if not text:
        return []

    lines = text.splitlines()
    # Collect (line_index, level, original_heading) for every heading line.
    heads: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2).strip()))

    sections: list[Section] = []
    seq = 0

    # Preamble: everything before the first heading (court, M.P., expediente).
    first_head_line = heads[0][0] if heads else len(lines)
    preamble = "\n".join(lines[:first_head_line]).strip()
    if preamble:
        sections.append(
            Section(seq=seq, level=0, original_heading="", normalized_section=ENCABEZADO, text=preamble)
        )
        seq += 1

    # Track ancestry to allow shallow→deep inheritance of normalized sections.
    # Stack of (level, normalized_section, section_seq).
    ancestry: list[tuple[int, str, int]] = []

    for i, (line_idx, level, heading) in enumerate(heads):
        next_line = heads[i + 1][0] if i + 1 < len(heads) else len(lines)
        body = "\n".join(lines[line_idx + 1 : next_line]).strip()

        # Pop ancestors that are not shallower than this heading.
        while ancestry and ancestry[-1][0] >= level:
            ancestry.pop()
        parent_seq = ancestry[-1][2] if ancestry else None

        norm = classify_heading(heading)
        if norm is None:
            # Inherit from the nearest ancestor; fall back to OTRO at top level.
            norm = ancestry[-1][1] if ancestry else OTRO

        sec = Section(
            seq=seq,
            level=level,
            original_heading=heading,
            normalized_section=norm,
            text=body,
            parent_seq=parent_seq,
        )
        sections.append(sec)
        ancestry.append((level, norm, seq))
        seq += 1

    return sections


# Coarse content_type (Capa 2 — semantic role) derived from the section type.
# This is the cheap, deterministic signal; the fine-grained per-numeral order
# parsing (OPERATIVE_ORDER with decision_type) is Fase 3, layered on top.
_CONTENT_TYPE_BY_SECTION = {
    ENCABEZADO: "METADATA",
    NORMA_DEMANDADA: "QUOTED_MATERIAL",
    PARTE_RESOLUTIVA: "OPERATIVE_ORDERS",
    SALVAMENTO_DE_VOTO: "SEPARATE_OPINION",
    ACLARACION_DE_VOTO: "SEPARATE_OPINION",
    FIRMAS: "SIGNATURE_BLOCK",
    PROBLEMA_JURIDICO: "LEGAL_ISSUE",
}


def content_type_for(normalized_section: str) -> str:
    """Coarse semantic role for a section (Capa 2). Default: ``REASONING``."""
    return _CONTENT_TYPE_BY_SECTION.get(normalized_section, "REASONING")


def is_separate_opinion(normalized_section: str) -> bool:
    """True for salvamentos/aclaraciones (distinct authorship & precedential value)."""
    return normalized_section in _SEPARATE_OPINION_SECTIONS


def derive_legacy_buckets(sections: list[Section]) -> dict[str, str]:
    """Recompose the legacy hechos/consideraciones/resuelve fields.

    Kept so existing consumers (frontend ``DocumentView``, old chunker
    fallback) keep working while the richer ``sections`` list is the new
    source of truth. Separate opinions are deliberately NOT folded into these
    buckets (they used to pollute consideraciones/resuelve).
    """
    hechos: list[str] = []
    consideraciones: list[str] = []
    resuelve: list[str] = []
    for sec in sections:
        if not sec.text:
            continue
        block = f"{sec.original_heading}\n\n{sec.text}".strip() if sec.original_heading else sec.text
        if sec.normalized_section in _HECHOS_SECTIONS:
            hechos.append(block)
        elif sec.normalized_section in _CONSIDERACIONES_SECTIONS:
            consideraciones.append(block)
        elif sec.normalized_section == PARTE_RESOLUTIVA:
            resuelve.append(block)
    return {
        "hechos": "\n\n".join(hechos).strip(),
        "consideraciones": "\n\n".join(consideraciones).strip(),
        "resuelve": "\n\n".join(resuelve).strip(),
    }
