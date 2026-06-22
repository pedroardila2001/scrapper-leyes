"""Tests del GacetaCongresoDiscoverer (parsing de listado + URL, sin red).

El patrón de URL de descarga ``index2.xhtml?ent=&fec=&num=`` fue confirmado en vivo
el 2026-06-19 (num=399 descargó gaceta_399.pdf). El fixture de índice usa enlaces
reales observados (Senado/Cámara, varias fechas).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.gaceta_congreso_discoverer import (
    GacetaCongresoDiscoverer,
    gaceta_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_gaceta_url_confirmada():
    assert gaceta_url("Senado", "27-4-2023", 399) == (
        "https://svrpubindc.imprenta.gov.co/senado/index2.xhtml"
        "?ent=Senado&fec=27-4-2023&num=399"
    )


def test_parse_index_real():
    d = GacetaCongresoDiscoverer()
    seeds = d._parse_index(_load("gaceta_congreso_index.html"))
    # 4 únicas (la 5a fila es duplicado exacto de la 399 → dedup).
    assert len(seeds) == 4
    nums = {s.numero for s in seeds}
    assert nums == {"399", "1061", "697", "12"}
    for s in seeds:
        assert s.source == "gaceta_congreso"
        assert s.tipo == "GACETA"
        assert s.external_id == s.numero
        assert s.source_url.startswith(
            "https://svrpubindc.imprenta.gov.co/senado/index2.xhtml"
        )


def test_parse_index_metadatos():
    d = GacetaCongresoDiscoverer()
    seeds = {s.numero: s for s in d._parse_index(_load("gaceta_congreso_index.html"))}
    g399 = seeds["399"]
    assert g399.anio == "2023"
    assert g399.extra["entidad"] == "SENADO"
    assert g399.extra["fecha"] == "27-4-2023"
    g697 = seeds["697"]
    assert g697.extra["entidad"] == "CAMARA"
    assert g697.anio == "2020"


def test_tipo_proyecto_ley_configurable():
    d = GacetaCongresoDiscoverer(tipo="PROYECTO_LEY")
    seeds = d._parse_index(_load("gaceta_congreso_index.html"))
    assert all(s.tipo == "PROYECTO_LEY" for s in seeds)


def test_seed_from_link_directo():
    d = GacetaCongresoDiscoverer()
    s = d._seed_from_link("Senado", "27-1-2025", "12")
    assert s.numero == "12"
    assert s.anio == "2025"
    assert s.source_url.endswith("num=12")
