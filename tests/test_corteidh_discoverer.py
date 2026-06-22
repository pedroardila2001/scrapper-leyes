"""Tests del CorteIDHDiscoverer (parsing de listado y ficha, sin red).

Los fixtures replican la forma REAL de corteidh.or.cr (verificada en vivo
2026-06-19): el listado de ``casos_en_supervision_por_pais.cfm`` enlaza fichas
por ``nId_Ficha``, y la ficha técnica lista el Nº de Serie C POR FASE como
"<etiqueta>: <N>" (cada fase = un PDF ``seriec_<N>_esp.pdf`` independiente).
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.corteidh_discoverer import CorteIDHDiscoverer

# Anclas reales del listado de supervisión por país.
_LISTING = """
<table>
  <tr><td><a href="ver_ficha_tecnica.cfm?nId_Ficha=259&lang=es" target="_blank">Caballero Delgado y Santana</a></td></tr>
  <tr><td><a href="ver_ficha_tecnica.cfm?nId_Ficha=309&lang=es" target="_blank">19 Comerciantes</a></td></tr>
  <tr><td><a href="ver_ficha_tecnica.cfm?nId_Ficha=145&lang=es" target="_blank">Velásquez Rodríguez</a></td></tr>
</table>
"""

# Ficha real (caso colombiano) con dos fases: el Nº de Serie C va por etiqueta y
# el año de fondo aparece en la sumilla.
_FICHA_COLOMBIA = """
<table>
  <tr><td>Estado Demandado:</td><td>Colombia</td></tr>
  <tr><td>Sumilla:</td><td>El caso se refiere a ... la Sentencia de fondo del 8 de diciembre de 1995, a la Sentencia de reparaciones y costas del ...</td></tr>
  <tr><td>Excepciones Preliminares:</td><td>18</td></tr>
  <tr><td>Sentencia de Fondo:</td><td>55</td></tr>
</table>
"""

# Ficha de otro país: debe descartarse cuando pais=Colombia.
_FICHA_HONDURAS = """
<table>
  <tr><td>Estado Demandado:</td><td>Honduras</td></tr>
  <tr><td>Sentencia de Fondo:</td><td>4</td></tr>
</table>
"""


def test_source_invalido():
    with pytest.raises(ValueError):
        CorteIDHDiscoverer("inventada")


def test_parse_listing_extrae_ids_y_nombres():
    d = CorteIDHDiscoverer()
    pares = dict(d._parse_listing(_LISTING))
    assert set(pares) == {"259", "309", "145"}
    assert pares["259"] == "Caballero Delgado y Santana"


def test_ficha_colombia_genera_una_seed_por_fase():
    d = CorteIDHDiscoverer(pais="Colombia")
    seeds = {s.numero: s for s in d._seeds_from_ficha(_FICHA_COLOMBIA, "259", "Caballero Delgado y Santana")}
    assert set(seeds) == {"18", "55"}

    fondo = seeds["55"]
    assert fondo.tipo == "SENTENCIA" and fondo.corte == "idh"
    assert fondo.subtipo == "fondo"
    assert fondo.anio == "1995"  # de "Sentencia de fondo del 8 de diciembre de 1995"
    assert fondo.external_id == "259-55"
    assert fondo.source_url.endswith("/docs/casos/articulos/seriec_55_esp.pdf")
    assert fondo.canonical_id == "co:sentencia:idh:fondo:55:1995"
    assert fondo.extra["pais"] == "Colombia"
    assert fondo.extra["caso"] == "Caballero Delgado y Santana"

    exc = seeds["18"]
    assert exc.subtipo == "excepciones_preliminares"
    assert exc.source_url.endswith("/seriec_18_esp.pdf")


def test_ficha_otro_pais_se_descarta():
    d = CorteIDHDiscoverer(pais="Colombia")
    assert d._seeds_from_ficha(_FICHA_HONDURAS, "145") == []


def test_filtro_por_otro_pais():
    d = CorteIDHDiscoverer(pais="Honduras")
    seeds = d._seeds_from_ficha(_FICHA_HONDURAS, "145")
    assert len(seeds) == 1
    assert seeds[0].numero == "4"
    assert seeds[0].extra["pais"] == "Honduras"


def test_seed_opinion_consultiva():
    d = CorteIDHDiscoverer()
    s = d._seed_opinion("23", anio="2017")
    assert s.tipo == "OPINION_CONSULTIVA"
    assert s.numero == "23"
    assert s.source_url.endswith("/docs/opiniones/seriea_23_esp.pdf")
    assert s.extra["serie"] == "A"
