"""Tests del WebRelatoriaDiscoverer (parsing de filas JSF, sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.webrelatoria_discoverer import WebRelatoriaDiscoverer

# Fragmento real de la tabla de resultados CSJ (cada fila empieza en data-rk;
# el id se repite como "ID:" dentro de la celda descrip).
_SAMPLE = """
<tr data-ri="0" data-rk="961712"><td>SALA DE CASACIÓN LABORAL TUTELA ID: 961712
NÚMERO DE PROCESO: T 76001220500020260015501
NÚMERO DE PROVIDENCIA: AHL070-2026
CLASE DE ACTUACIÓN: SOLICITUD DE NULIDAD
TIPO DE PROVIDENCIA: AUTO
FECHA: 21/05/2026
PONENTE: LUIS BENEDICTO HERRERA DÍAZ
TEMA: ACCIÓN DE HABEAS CORPUS</td></tr>
<tr data-ri="1" data-rk="953249"><td>SALA DE CASACIÓN PENAL ASUNTO ID: 953249
NÚMERO DE PROCESO: 11001020400020260012300
NÚMERO DE PROVIDENCIA: AP1234-2025
CLASE DE ACTUACIÓN: CASACIÓN
TIPO DE PROVIDENCIA: SENTENCIA
FECHA: 03/12/2025
PONENTE: GERSON CHAVERRA CASTRO
TEMA: NULIDAD</td></tr>
"""

# Fragmento real de la tabla del Consejo de Estado (formato distinto: NR, SECCION,
# ponente seguido de ACTOR, tipo suelto tras el radicado).
_SAMPLE_CE = """
<tr data-ri="0" data-rk="2333536"><td>CONSEJO DE ESTADO NR: 2333536 18001-23-33-000-2015-00179-01 AUTO INTERLOCUTORIO SUSTENTO NORMATIVO : LEY 1437 DE 2011 NORMA DEMANDADA : FECHA : 05/02/2024 SECCION : SECCION TERCERA SUBSECCIÓN A PONENTE : JOSE ROBERTO SACHICA MENDEZ ACTOR : YULIETH DIAZ DEMANDADO : POLICIA NACIONAL DECISION : RECHAZA TEMA : PRUEBAS</td></tr>
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

    # El TEMA/descrip completo se captura (lo materializa WebRelatoriaScraper
    # como raw_text indexable). Debe traer la tesis y NO basura de tags HTML.
    assert "ACCIÓN DE HABEAS CORPUS" in a.extra["descrip"]
    assert "class=" not in a.extra["descrip"]


def test_webrelatoria_scraper_en_factory():
    """CSJ/CE se sirven con el scraper de texto WebRelatoria (no el genérico)."""
    pytest.importorskip("httpx")
    from scrapper_leyes.config import Settings
    from scrapper_leyes.scraper.factory import ScraperFactory
    from scrapper_leyes.scraper.webrelatoria_discoverer import WebRelatoriaScraper

    f = ScraperFactory(Settings(), db=None, cache=None)
    assert isinstance(f.get_scraper("csj"), WebRelatoriaScraper)
    assert isinstance(f.get_scraper("consejo_estado"), WebRelatoriaScraper)


def test_ce_usa_corte_ce():
    d = WebRelatoriaDiscoverer("consejo_estado")
    assert d.corte == "ce"
    assert "ce/FileReferenceServlet" in d.servlet


def test_parse_filas_ce():
    """El parser unificado también lee el formato del Consejo de Estado."""
    d = WebRelatoriaDiscoverer("consejo_estado")
    seeds = d._parse_rows(_SAMPLE_CE)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.external_id == "2333536"
    assert s.corte == "ce"
    assert s.anio == "2024"
    assert "SACHICA" in s.magistrado_ponente
    assert s.subtipo == "Auto Interlocutorio"
    assert s.extra["radicado"].startswith("18001-23-33")
    assert "Tercera" in s.extra["sala"]
    assert s.source_url.endswith("ce&ext=pdf&file=2333536")
