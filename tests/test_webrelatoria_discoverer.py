"""Tests del WebRelatoriaDiscoverer (parsing de filas JSF, sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.webrelatoria_discoverer import WebRelatoriaDiscoverer

# Fragmento real de la tabla de resultados (2 filas, con tags como vienen).
_SAMPLE = """
<tr><td>SALA DE CASACIÓN LABORAL TUTELA <span>ID:</span> 961712
NÚMERO DE PROCESO: T 76001220500020260015501
NÚMERO DE PROVIDENCIA: AHL070-2026
CLASE DE ACTUACIÓN: SOLICITUD DE NULIDAD
TIPO DE PROVIDENCIA: AUTO
FECHA: 21/05/2026
PONENTE: LUIS BENEDICTO HERRERA DÍAZ
TEMA: ACCIÓN DE HABEAS CORPUS</td></tr>
<tr><td>SALA DE CASACIÓN PENAL ASUNTO ID: 953249
NÚMERO DE PROCESO: 11001020400020260012300
NÚMERO DE PROVIDENCIA: AP1234-2025
CLASE DE ACTUACIÓN: CASACIÓN
TIPO DE PROVIDENCIA: SENTENCIA
FECHA: 03/12/2025
PONENTE: GERSON CHAVERRA CASTRO
TEMA: NULIDAD</td></tr>
"""


def test_source_invalido():
    with pytest.raises(ValueError):
        WebRelatoriaDiscoverer("inventada")


def test_parse_filas():
    d = WebRelatoriaDiscoverer("csj")
    seeds = d._parse_rows(_SAMPLE)
    assert len(seeds) == 2
    s = {x.external_id: x for x in seeds}

    a = s["961712"]
    assert a.tipo == "SENTENCIA" and a.corte == "csj"
    assert a.numero == "AHL070-2026"
    assert a.anio == "2026"
    assert a.subtipo == "AUTO"
    assert "HERRERA" in a.magistrado_ponente
    assert a.extra["radicado"].startswith("T 76001")
    assert a.extra["sala"] == "Sala De Casación Laboral"
    assert a.canonical_id == "co:sentencia:csj:laboral:ahl070-2026:2026"
    assert a.source_url.endswith("FileReferenceServlet?corp=csj&ext=pdf&file=961712")

    b = s["953249"]
    assert b.canonical_id == "co:sentencia:csj:penal:ap1234-2025:2025"
    assert b.subtipo == "SENTENCIA"


def test_ce_usa_corte_ce():
    d = WebRelatoriaDiscoverer("consejo_estado")
    assert d.corte == "ce"
    assert "ce/FileReferenceServlet" in d.servlet
