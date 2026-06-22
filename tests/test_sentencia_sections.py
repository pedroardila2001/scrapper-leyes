"""Tests for the heading-driven sentencia sectionizer (pure logic)."""

from __future__ import annotations

from scrapper_leyes.sentencia_sections import (
    ANALISIS,
    ANTECEDENTES,
    CONCEPTO_PROCURADURIA,
    CONSIDERACIONES,
    COMPETENCIA,
    ENCABEZADO,
    INTERVENCIONES,
    NORMA_DEMANDADA,
    PARTE_RESOLUTIVA,
    PROBLEMA_JURIDICO,
    SALVAMENTO_DE_VOTO,
    classify_heading,
    clean_heading,
    content_type_for,
    derive_legacy_buckets,
    split_sections,
)

# A compact but realistic CC ruling skeleton (Roman-numbered top headings,
# arabic-numbered subsections) — the exact shape that broke the old regex.
SENTENCIA_MD = """\
Sentencia C-274 de 2013
Magistrado Ponente: Dr. María Victoria Calle Correa

# I. ANTECEDENTES

El ciudadano demandó los artículos 18 y 19 de la Ley 1712 de 2014.

# II. TEXTO DE LAS NORMAS DEMANDADAS

Artículo 18. Excepciones...

# III. INTERVENCIONES DE AUTORIDADES

## 1. Intervención del Ministerio de Justicia

El Ministerio solicita exequibilidad.

## 2. Intervención del Ministerio de Hacienda

Coadyuva la demanda.

# VI. CONCEPTO DEL PROCURADOR GENERAL DE LA NACIÓN

El Procurador solicita estarse a lo resuelto.

# VII. CONSIDERACIONES Y FUNDAMENTOS

## 1. Competencia

La Corte es competente.

## 2. Problema jurídico

¿Vulnera la norma el derecho de acceso a la información?

## 3. Análisis material.

La Sala considera que la reserva es desproporcionada.

# VIII. DECISIÓN

**RESUELVE:**

**Primero.-** Declarar EXEQUIBLE el artículo 18.

Notifíquese, comuníquese y cúmplase.

# Salvamento parcial de voto

Me aparto de la decisión mayoritaria.
"""


# ── clean_heading / classify_heading ─────────────────────────────────────────


def test_clean_heading_strips_numbering_and_emphasis():
    assert clean_heading("# I. ANTECEDENTES") == "ANTECEDENTES"
    assert clean_heading("## **2.2.2.** Análisis material.") == "Análisis material"
    assert clean_heading("VIII. DECISIÓN") == "DECISIÓN"


def test_classify_decision_with_roman_numeral_prefix():
    # The exact case the old regex missed: keyword not adjacent to '#'.
    assert classify_heading("VIII. DECISIÓN") == PARTE_RESOLUTIVA
    assert classify_heading("# VIII. DECISIÓN") == PARTE_RESOLUTIVA
    assert classify_heading("RESUELVE") == PARTE_RESOLUTIVA


def test_classify_specific_before_generic():
    assert classify_heading("Salvamento parcial de voto") == SALVAMENTO_DE_VOTO
    assert classify_heading("Problema jurídico") == PROBLEMA_JURIDICO
    assert classify_heading("Concepto del Procurador General") == CONCEPTO_PROCURADURIA
    assert classify_heading("Competencia") == COMPETENCIA


def test_classify_unknown_returns_none():
    assert classify_heading("Lorem ipsum dolor") is None
    assert classify_heading("") is None


# ── split_sections ───────────────────────────────────────────────────────────


def _by_norm(sections):
    out = {}
    for s in sections:
        out.setdefault(s.normalized_section, []).append(s)
    return out


def test_split_sections_captures_preamble_as_encabezado():
    secs = split_sections(SENTENCIA_MD)
    assert secs[0].normalized_section == ENCABEZADO
    assert "Magistrado Ponente" in secs[0].text


def test_split_sections_normalizes_all_top_level_parts():
    secs = split_sections(SENTENCIA_MD)
    present = {s.normalized_section for s in secs}
    for expected in (
        ANTECEDENTES,
        NORMA_DEMANDADA,
        INTERVENCIONES,
        CONCEPTO_PROCURADURIA,
        CONSIDERACIONES,
        COMPETENCIA,
        PROBLEMA_JURIDICO,
        ANALISIS,
        PARTE_RESOLUTIVA,
        SALVAMENTO_DE_VOTO,
    ):
        assert expected in present, f"missing {expected}"


def test_subsections_inherit_parent_when_unclassified():
    secs = split_sections(SENTENCIA_MD)
    # "## 1. Intervención del Ministerio de Justicia" has no keyword → inherits
    # INTERVENCIONES from its "# III. INTERVENCIONES DE AUTORIDADES" parent.
    inter = [s for s in secs if "Ministerio de Justicia" in s.original_heading]
    assert inter and inter[0].normalized_section == INTERVENCIONES


def test_decision_section_is_separated_not_dumped_in_consideraciones():
    secs = split_sections(SENTENCIA_MD)
    resolutiva = [s for s in secs if s.normalized_section == PARTE_RESOLUTIVA]
    assert resolutiva
    assert "EXEQUIBLE" in resolutiva[0].text
    # And it must NOT have leaked into consideraciones.
    consid = [s for s in secs if s.normalized_section == CONSIDERACIONES]
    assert all("EXEQUIBLE" not in s.text for s in consid)


def test_section_seqs_are_unique_and_ordered():
    secs = split_sections(SENTENCIA_MD)
    seqs = [s.seq for s in secs]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def test_empty_input_returns_empty():
    assert split_sections("") == []
    assert split_sections("   \n  ") == []


# ── derived legacy buckets ────────────────────────────────────────────────────


def test_derive_legacy_buckets_routes_sections_correctly():
    secs = split_sections(SENTENCIA_MD)
    buckets = derive_legacy_buckets(secs)
    assert "ciudadano demandó" in buckets["hechos"]
    assert "desproporcionada" in buckets["consideraciones"]
    assert "EXEQUIBLE" in buckets["resuelve"]
    # Separate opinions never pollute the legacy buckets.
    assert "aparto de la decisión" not in buckets["consideraciones"]
    assert "aparto de la decisión" not in buckets["resuelve"]


# ── content_type ──────────────────────────────────────────────────────────────


def test_content_type_mapping():
    assert content_type_for(PARTE_RESOLUTIVA) == "OPERATIVE_ORDERS"
    assert content_type_for(SALVAMENTO_DE_VOTO) == "SEPARATE_OPINION"
    assert content_type_for(PROBLEMA_JURIDICO) == "LEGAL_ISSUE"
    assert content_type_for(CONSIDERACIONES) == "REASONING"
