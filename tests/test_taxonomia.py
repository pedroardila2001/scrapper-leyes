"""Tests for the entity taxonomy (biblioteca)."""

from __future__ import annotations

from scrapper_leyes.taxonomia import (
    OTROS,
    RAMA_EJECUTIVA,
    RAMA_JUDICIAL,
    RAMA_LEGISLATIVA,
    build_library_tree,
    classify,
    repair_text,
)


def test_repair_mojibake():
    assert repair_text("Hacienda y CrÃ©dito PÃºblico") == "Hacienda y Crédito Público"
    assert repair_text("EstadÃ\xadstica") == "Estadística"
    assert repair_text("normal text") == "normal text"


def test_law_is_classified_by_issuer_not_sector():
    # A LEY has sector="Hacienda" (subject) but is issued by Congreso → Legislativa.
    rama, cabeza, ent = classify("LEY", "Hacienda y Crédito Público", "CONGRESO DE LA REPÚBLICA")
    assert rama == RAMA_LEGISLATIVA
    assert cabeza == "Congreso de la República"
    assert ent == "Congreso de la República"


def test_congreso_variants_merge():
    for raw in [
        "CONGRESO DE LA REPÚBLICA",
        "CONGRESO DE LA REPUBLICA",
        "CONGRESO DE COLOMBIA",
        "PODER LEGISLATIVO",
        "CONGRESO NACIONAL DE LOS ESTADOS UNIDOS DE COLOMBIA",
    ]:
        rama, _, ent = classify("LEY", None, raw)
        assert rama == RAMA_LEGISLATIVA
        assert ent == "Congreso de la República"


def test_executive_agency_grouped_by_sector():
    # Una agencia ejecutiva común se agrupa bajo su sector administrativo.
    rama, cabeza, ent = classify(
        "RESOLUCION", "Minas y Energía", "AGENCIA NACIONAL DE HIDROCARBUROS"
    )
    assert rama == RAMA_EJECUTIVA
    assert cabeza == "Minas y Energía"


def test_comision_regulacion_es_cabeza_propia():
    # Las Comisiones de Regulación (CREG/CRC/CRA) NO se diluyen en su sector:
    # son cabeza propia (regulación con fuerza normativa, ≠ Superintendencias).
    rama, cabeza, _ = classify(
        "RESOLUCION", "Minas y Energía", "COMISION DE REGULACION DE ENERGIA Y GAS"
    )
    assert rama == RAMA_EJECUTIVA
    assert cabeza == "Comisiones de Regulación"


def test_ministry_grouped_under_its_sector():
    rama, cabeza, _ = classify("DECRETO", "Hacienda y Crédito Público", "MINISTERIO DE HACIENDA Y CRÉDITO PÚBLICO")
    assert rama == RAMA_EJECUTIVA
    assert cabeza == "Hacienda y Crédito Público"


def test_sentencia_goes_to_judicial_by_corte():
    rama, cabeza, ent = classify("SENTENCIA", None, None, corte="cc")
    assert rama == RAMA_JUDICIAL
    assert cabeza == "Corte Constitucional"
    rama2, cabeza2, _ = classify("SENTENCIA", None, None, corte="ce")
    assert cabeza2 == "Consejo de Estado"


def test_mojibake_sector_still_maps():
    rama, cabeza, _ = classify("DECRETO", "Hacienda y CrÃ©dito PÃºblico", "MINISTERIO DE HACIENDA")
    assert rama == RAMA_EJECUTIVA
    assert cabeza == "Hacienda y Crédito Público"


def test_control_and_autonomous_bodies():
    assert classify("RESOLUCION", None, "CONTRALORIA GENERAL DE LA REPUBLICA")[0] == "Organismos de Control"
    assert classify("RESOLUCION", None, "BANCO DE LA REPUBLICA")[0] == "Órgano Autónomo"


def test_build_tree_structure_and_accent_merge():
    rows = [
        {"tipo": "LEY", "sector": "Hacienda y Crédito Público", "entidad": "CONGRESO DE LA REPÚBLICA"},
        {"tipo": "LEY", "sector": None, "entidad": "CONGRESO DE LA REPUBLICA"},
        {"tipo": "DECRETO", "sector": "Hacienda y Crédito Público", "entidad": "MINISTERIO DE HACIENDA Y CRÉDITO PÚBLICO"},
        {"tipo": "DECRETO", "sector": "Hacienda y Crédito Público", "entidad": "MINISTERIO DE HACIENDA Y CREDITO PUBLICO"},
        {"tipo": "SENTENCIA", "sector": None, "entidad": None, "corte": "cc"},
    ]
    tree = build_library_tree(rows)
    assert tree["total"] == 5
    ramas = {r["nombre"]: r for r in tree["ramas"]}
    assert ramas[RAMA_LEGISLATIVA]["total"] == 2
    assert ramas[RAMA_JUDICIAL]["total"] == 1

    # The two ministry spellings collapse into one entity node.
    ej = ramas[RAMA_EJECUTIVA]
    hacienda = next(s for s in ej["sectores"] if s["nombre"] == "Hacienda y Crédito Público")
    min_nodes = [e for e in hacienda["entidades"] if "Hacienda" in e["nombre"]]
    assert len(min_nodes) == 1
    assert min_nodes[0]["total"] == 2
    assert min_nodes[0]["nombre"] == "Ministerio de Hacienda y Crédito Público"  # accented wins


def test_otros_is_small_or_absent_on_clean_data():
    rows = [{"tipo": "LEY", "sector": "Interior", "entidad": "CONGRESO DE LA REPÚBLICA"}]
    tree = build_library_tree(rows)
    assert all(r["nombre"] != OTROS for r in tree["ramas"])
