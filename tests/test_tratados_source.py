"""Test del conector de Tratados (Socrata fdir-hk5z) — transform sin red."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.catalog.socrata_client import CATALOG_SOURCES, TRATADOS


def test_tratados_registrado():
    assert "tratados" in CATALOG_SOURCES
    assert TRATADOS.dataset_id == "fdir-hk5z"


def test_transform_fila_real():
    # Fila con la forma real del dataset (verificada en el spike).
    raw = {
        ":id": "row-abc123",
        "nombretratado": "CONVENCIÓN AMERICANA SOBRE DERECHOS HUMANOS",
        "fechaadopcion": "22/11/1969",
        "vigente": "SI",
        "numeroleyaprobatoria": "16 DE 1972",
        "sentencianumero": "C-225/95",
        "naturalezatratado": "TRATADO SOLEMNE",
        "temas": "DERECHOS HUMANOS",
        "bilateral": "NO",
    }
    row = TRATADOS.clean_row(raw)

    assert row["tipo"] == "TRATADO"
    assert row["anio"] == "1969"
    assert row["vigencia"] == "Vigente"
    assert row["numero"] == "convencion-americana-sobre-derechos-humanos"
    assert row["canonical_id"] == "co:tratado:convencion-americana-sobre-derechos-humanos:1969"
    assert row["external_id"] == "row-abc123"
    # La ley aprobatoria y la sentencia de control quedan trazadas en materia.
    assert "16 DE 1972" in row["materia"]
    assert "C-225/95" in row["materia"]
    assert row["source"] == "tratados"


def test_no_vigente_y_sin_fecha():
    raw = {"nombretratado": "ACUERDO X", "vigente": "NO", "fechaadopcion": ""}
    row = TRATADOS.clean_row(raw)
    assert row["vigencia"] == "No vigente"
    assert row["anio"] is None
    assert row["canonical_id"] is None  # sin año no se construye id canónico


def test_taxonomia_tratado_internacional():
    from scrapper_leyes.taxonomia import classify

    rama, cabeza, _ = classify("TRATADO", "DERECHOS HUMANOS", "Ministerio de Relaciones Exteriores")
    assert rama == "Internacional"
    assert cabeza == "Tratados y derecho internacional"
