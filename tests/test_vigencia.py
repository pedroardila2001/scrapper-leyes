"""Tests for the temporal vigencia resolver."""

from __future__ import annotations

from datetime import date

from scrapper_leyes.vigencia import (
    ESTADO_CONDICIONADA,
    ESTADO_DEROGADO,
    ESTADO_INEXEQUIBLE,
    ESTADO_MODIFICADO,
    ESTADO_VIGENTE,
    parse_date_range,
    parse_fecha,
    resolve,
)


def _norm(**over):
    base = {
        "tipo": "LEY", "numero": "100", "anio": "1993",
        "suin_id": "x", "canonical_id": "co:ley:100:1993",
        "vigencia": "Vigente", "suin_vigencia": "Vigente",
    }
    base.update(over)
    return base


def _art(num="5", text="Texto vigente.", **over):
    a = {
        "number_normalized": num,
        "number": f"Artículo {num}°.",
        "canonical_id": f"co:ley:100:1993:art:{num}",
        "text": text,
        "previous_versions": [],
    }
    a.update(over)
    return a


# ── parsing de fechas ─────────────────────────────────────────────────────────


def test_parse_fecha():
    assert parse_fecha("03/10/2007") == date(2007, 10, 3)
    assert parse_fecha("Vigente desde:\xa005/01/2011") == date(2011, 1, 5)
    assert parse_fecha("sin fecha") is None


def test_parse_date_range():
    d, h = parse_date_range("Vigente desde:\xa003/10/2007\xa0y hasta el:\xa005/01/2011")
    assert d == date(2007, 10, 3) and h == date(2011, 1, 5)
    d, h = parse_date_range("Vigente desde: 01/01/2020")
    assert d == date(2020, 1, 1) and h is None


# ── estado por afectaciones ───────────────────────────────────────────────────


def test_articulo_vigente():
    parsed = {"articles": [_art()], "modifications": [], "jurisprudence": []}
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.estado == ESTADO_VIGENTE and r.vigente


def test_articulo_derogado():
    parsed = {
        "articles": [_art()],
        "modifications": [{"article_affected": "Artículo 5", "normalized_type": "DEROGA_TOTAL",
                           "source_text": "Art 1 Ley 200 de 2020"}],
        "jurisprudence": [],
    }
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.estado == ESTADO_DEROGADO and not r.vigente


def test_inexequible_gana_sobre_todo():
    parsed = {
        "articles": [_art()],
        "modifications": [{"article_affected": "Artículo 5", "normalized_type": "MODIFICA",
                           "source_text": "x"}],
        "jurisprudence": [{"article_affected": "Artículo 5", "normalized_type": "INEXEQUIBLE",
                           "source_text": "Sentencia C-100 de 2010"}],
    }
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.estado == ESTADO_INEXEQUIBLE and not r.vigente
    assert "C-100" in r.motivo


def test_exequible_condicionada():
    parsed = {
        "articles": [_art()],
        "modifications": [],
        "jurisprudence": [{"article_affected": "Documento completo",
                           "normalized_type": "EXEQUIBLE_CONDICIONADA",
                           "source_text": "Sentencia C-555 de 2016"}],
    }
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.estado == ESTADO_CONDICIONADA and r.vigente
    assert "C-555" in r.motivo


def test_norma_derogada_cascada():
    parsed = {"articles": [_art()], "modifications": [], "jurisprudence": []}
    r = resolve(parsed, _norm(suin_vigencia="Derogada"), art_ref="5")
    assert r.estado == ESTADO_DEROGADO


def test_previous_versions_implica_modificado():
    art = _art(previous_versions=[{"text": "viejo", "date_range": "Vigente desde: 01/01/2000 y hasta el: 31/12/2010"}])
    parsed = {"articles": [art], "modifications": [], "jurisprudence": []}
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.estado == ESTADO_MODIFICADO


# ── texto temporal ────────────────────────────────────────────────────────────


def test_texto_a_una_fecha():
    art = _art(
        text="Texto nuevo.",
        previous_versions=[{"text": "Texto viejo.", "date_range": "Vigente desde: 01/01/2000 y hasta el: 31/12/2010"}],
    )
    parsed = {"articles": [art], "modifications": [], "jurisprudence": []}

    # Fecha dentro del rango antiguo → versión vieja.
    r = resolve(parsed, _norm(), art_ref="5", fecha="15/06/2005")
    assert r.texto_aplicable == "Texto viejo." and not r.texto_es_vigente

    # Fecha reciente → versión actual.
    r = resolve(parsed, _norm(), art_ref="5", fecha="2024-01-01")
    assert r.texto_aplicable == "Texto nuevo." and r.texto_es_vigente

    # Sin fecha → versión actual.
    r = resolve(parsed, _norm(), art_ref="5")
    assert r.texto_aplicable == "Texto nuevo."


def test_norma_completa_con_jurisprudencia():
    parsed = {
        "articles": [_art()],
        "modifications": [],
        "jurisprudence": [{"article_affected": "Documento completo", "normalized_type": "EXEQUIBLE",
                           "source_text": "Sentencia C-1 de 2008"}],
    }
    r = resolve(parsed, _norm())
    assert r.nivel == "norma"
    assert len(r.jurisprudencia) == 1


def test_articulo_inexistente_devuelve_none():
    parsed = {"articles": [_art()], "modifications": [], "jurisprudence": []}
    assert resolve(parsed, _norm(), art_ref="999") is None
