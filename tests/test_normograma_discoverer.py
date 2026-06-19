"""Tests del NormogramaDiscoverer (parsing de nombres de documento, sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.normograma_discoverer import (
    NORMOGRAMA_SOURCES,
    NormogramaDiscoverer,
)


def test_fuentes_registradas():
    assert set(NORMOGRAMA_SOURCES) == {"cra", "creg", "crc", "dian"}


def test_fuente_desconocida():
    with pytest.raises(ValueError):
        NormogramaDiscoverer("inventada")


def test_parse_resolucion_cra():
    d = NormogramaDiscoverer("cra")
    s = d._seed_from_docurl("https://normas.cra.gov.co/gestor/docs/resolucion_cra_0768_2016.htm")
    assert s is not None
    assert (s.tipo, s.numero, s.anio) == ("RESOLUCION", "0768", "2016")
    assert s.canonical_id == "co:resolucion:0768:2016"
    assert s.source == "cra"
    assert s.extra["entidad"] == "CRA"


def test_parse_oficio_dian_es_concepto():
    d = NormogramaDiscoverer("dian")
    s = d._seed_from_docurl("https://normograma.dian.gov.co/dian/compilacion/docs/oficio_dian_2112_2024.htm")
    assert s.tipo == "CONCEPTO"  # oficio → concepto (doctrina)
    assert (s.numero, s.anio) == ("2112", "2024")


def test_parse_creg_serie():
    d = NormogramaDiscoverer("creg")
    s = d._seed_from_docurl("https://gestornormativo.creg.gov.co/gestor/entorno/docs/resolucion_creg_501-64_2024.htm")
    assert s.tipo == "RESOLUCION"
    assert s.numero == "501-64"  # serie-consecutivo preservado
    assert s.anio == "2024"


def test_url_no_documento_se_ignora():
    d = NormogramaDiscoverer("cra")
    assert d._seed_from_docurl("https://normas.cra.gov.co/gestor/index.html") is None
