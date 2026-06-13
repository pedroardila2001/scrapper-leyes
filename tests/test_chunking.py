"""Tests for the structural chunking layer (pure logic, no heavy deps)."""

from __future__ import annotations

from scrapper_leyes.chunking import (
    DEFAULT_MAX_CHARS,
    chunk_document,
    split_text,
)


# ── split_text ──────────────────────────────────────────────────────────────


def test_split_text_short_returns_single_piece():
    assert split_text("texto corto") == ["texto corto"]


def test_split_text_empty_returns_nothing():
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_text_never_truncates_content():
    # A long body must be fully represented across pieces (no silent [:2000]).
    paras = [f"Parágrafo {i}. " + ("palabra " * 40) for i in range(20)]
    body = "\n\n".join(paras)
    pieces = split_text(body, max_chars=500, overlap=50)
    assert len(pieces) > 1
    # Every source paragraph marker survives somewhere in the output.
    joined = " ".join(pieces)
    for i in range(20):
        assert f"Parágrafo {i}." in joined


def test_split_text_respects_max_chars_with_overlap_slack():
    body = "\n\n".join("oración. " * 30 for _ in range(10))
    pieces = split_text(body, max_chars=400, overlap=60)
    assert all(len(p) <= 400 + 60 for p in pieces)


def test_split_text_handles_single_oversized_paragraph():
    body = "x" * 5000  # unsplittable blob → hard window
    pieces = split_text(body, max_chars=1000, overlap=100)
    assert len(pieces) >= 5
    assert all(len(p) <= 1000 for p in pieces)


# ── chunk_document: norms ────────────────────────────────────────────────────


def _law_catalog(**over):
    base = {
        "tipo": "LEY",
        "numero": "1712",
        "anio": "2014",
        "suin_id": "1687091",
        "suin_vigencia": "Vigente",
    }
    base.update(over)
    return base


def test_article_chunk_has_context_header_and_canonical_id():
    parsed = {
        "suin_id": "1687091",
        "articles": [
            {
                "art_id": "111",
                "number": "Artículo 1°.",
                "number_normalized": "1",
                "title": "Objeto",
                "text": "El objeto de la presente ley es regular X.",
                "notes": [],
                "previous_versions": [],
            }
        ],
        "modifications": [],
    }
    chunks = chunk_document(parsed, _law_catalog())
    assert len(chunks) == 1
    c = chunks[0]
    assert c.canonical_id == "co:ley:1712:2014:art:1"
    # Context header prepended, no doubled "Artículo Artículo".
    assert c.text.startswith("Ley 1712 de 2014 · Artículo 1 — Objeto:")
    assert "Artículo Artículo" not in c.text
    assert c.payload["estado_vigencia"] == "vigente"
    assert c.payload["derogado"] is False


def test_modified_article_is_flagged():
    parsed = {
        "suin_id": "1687091",
        "articles": [
            {
                "art_id": "a3",
                "number": "Artículo 3°.",
                "number_normalized": "3",
                "title": None,
                "text": "Texto del artículo tres.",
                "notes": ["Modificado..."],
                "previous_versions": [],
            }
        ],
        "modifications": [
            {
                "article_affected": "Artículo 3",
                "normalized_type": "MODIFICA",
                "raw_type": "Modificado",
                "source_text": "Artículo 3 LEY 2517 de 2025",
                "source_suin_id": "30055367",
            }
        ],
    }
    c = chunk_document(parsed, _law_catalog())[0]
    assert c.payload["modificado"] is True
    assert c.payload["estado_vigencia"] == "modificado"
    assert c.payload["afectaciones"][0]["tipo"] == "MODIFICA"


def test_derogated_norm_cascades_to_articles():
    parsed = {
        "suin_id": "x",
        "articles": [
            {
                "art_id": "1",
                "number": "Artículo 1°.",
                "number_normalized": "1",
                "title": None,
                "text": "Algo.",
                "notes": [],
                "previous_versions": [],
            }
        ],
        "modifications": [],
    }
    c = chunk_document(parsed, _law_catalog(suin_vigencia="Derogada"))[0]
    assert c.payload["derogado"] is True
    assert c.payload["estado_vigencia"] == "derogado"


def test_long_article_is_split_not_truncated():
    big = "Parágrafo. " + ("contenido legal " * 400)  # ~6800 chars
    parsed = {
        "suin_id": "x",
        "articles": [
            {
                "art_id": "1",
                "number": "Artículo 1°.",
                "number_normalized": "1",
                "title": "Largo",
                "text": big,
                "notes": [],
                "previous_versions": [],
            }
        ],
        "modifications": [],
    }
    chunks = chunk_document(parsed, _law_catalog())
    assert len(chunks) > 1
    assert all(c.n_chunks == len(chunks) for c in chunks)
    assert "(1/" in chunks[0].section
    # All chunks share the article canonical_id but have distinct uids.
    assert len({c.uid for c in chunks}) == len(chunks)
    assert all(c.canonical_id == "co:ley:1712:2014:art:1" for c in chunks)


def test_duplicate_canonical_articles_get_distinct_uids():
    art = {
        "art_id": "AAA",
        "number": "Artículo 1°.",
        "number_normalized": "1",
        "title": None,
        "text": "uno",
        "notes": [],
        "previous_versions": [],
    }
    art2 = dict(art, art_id="BBB", text="uno bis")
    parsed = {"suin_id": "x", "articles": [art, art2], "modifications": []}
    chunks = chunk_document(parsed, _law_catalog())
    assert len(chunks) == 2
    assert chunks[0].uid != chunks[1].uid


# ── chunk_document: sentencias ───────────────────────────────────────────────


def test_sentencia_chunks_include_resuelve():
    parsed = {
        "suin_id": "C-377-10",
        "corte": "cc",
        "sala": "plena",
        "hechos": "Los hechos.",
        "consideraciones": "Las consideraciones.",
        "resuelve": "Declarar exequible.",
    }
    cat = {
        "tipo": "SENTENCIA",
        "numero": "C-377-10",
        "anio": "2010",
        "suin_id": "C-377-10",
        "corte": "cc",
    }
    chunks = chunk_document(parsed, cat)
    sections = {c.title for c in chunks}
    assert sections == {"Hechos", "Consideraciones", "Resuelve"}
    resuelve = next(c for c in chunks if c.title == "Resuelve")
    assert "exequible" in resuelve.body.lower()
    assert resuelve.text.startswith("Sentencia C-377-10 de 2010 (CC) · Resuelve:")


def test_deterministic_uids_are_stable_across_calls():
    parsed = {
        "suin_id": "x",
        "articles": [
            {
                "art_id": "1",
                "number": "Artículo 1°.",
                "number_normalized": "1",
                "title": None,
                "text": "estable",
                "notes": [],
                "previous_versions": [],
            }
        ],
        "modifications": [],
    }
    a = chunk_document(parsed, _law_catalog())[0].uid
    b = chunk_document(parsed, _law_catalog())[0].uid
    assert a == b
