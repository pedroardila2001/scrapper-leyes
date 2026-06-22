"""Tests del JEPDiscoverer (parsing de items Jurinfo, sin red).

El fixture son ítems REALES capturados de la API Buscar.ashx de Jurinfo
(jurinfo.jep.gov.co/normograma/buscador) el 2026-06-19, con el campo `texto`
recortado (era enorme y no se usa).
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.jep_discoverer import JEPDiscoverer

# Ítems reales (un origen-JEP de sala, un acuerdo, una resolución, una ley, y un
# espejo de Corte Constitucional que debe OMITIRSE).
_ITEMS = [
    {"nombre": "Auto SRVR 009 ADHC CAS0 04 28 noviembre 2025", "texto": "...",
     "link": "Auto_SRVR-009-ADHC-CAS0-04_28-noviembre-2025.htm",
     "entidad": "Sala de Reconocimiento de Verdad, de Responsabilidad - Autos",
     "tipo": "JEP - Salas de Justicia", "year": "2025", "numero": None},
    {"nombre": "Acuerdo 15 de 2023 JEP", "texto": "...",
     "link": "acuerdo_jep_0015aog_2023.htm",
     "entidad": "JEP - Jurisdicción Especial para la Paz",
     "tipo": "Acuerdos", "year": "2023", "numero": "15"},
    {"nombre": "Resolución 235 de 2026 JEP", "texto": "...",
     "link": "resolucion_jep_0235_2026.htm",
     "entidad": "JEP - Jurisdicción Especial para la Paz",
     "tipo": "Resoluciones", "year": "2026", "numero": "235"},
    {"nombre": "Ley 1957 de 2019", "texto": "...", "link": "ley_1957_2019.htm",
     "entidad": "Congreso de la República", "tipo": "Leyes",
     "year": "2019", "numero": "1957"},
    {"nombre": "Sentencia C-080 de 2018 de la Corte Constitucional - Control",
     "texto": "...", "link": "C-080_2018.htm",
     "entidad": "Corte Constitucional - Control de Constitucionalidad",
     "tipo": "Corte Constitucional - Autos y sentencias",
     "year": "2018", "numero": "080"},
]


def test_omite_espejos_y_leyes():
    d = JEPDiscoverer()
    seeds = d._seeds_from_payload(_ITEMS)
    # Origen JEP: Auto, Acuerdo, Resolución (3). Se omiten el espejo de la Corte
    # (C-080/2018) y la Ley 1957/2019 — su fuente canónica es la Corte / SUIN,
    # para no duplicar nodos del grafo.
    assert "C-080_2018.htm" not in seeds
    assert "ley_1957_2019.htm" not in seeds
    assert len(seeds) == 3


def test_auto_propio_de_sala():
    d = JEPDiscoverer()
    s = d._seeds_from_payload(_ITEMS)["Auto_SRVR-009-ADHC-CAS0-04_28-noviembre-2025.htm"]
    assert s.tipo == "AUTO"
    assert s.source == "jep" and s.corte == "jep"
    assert s.anio == "2025"
    # numero vacío en la API → radicado tomado del link.
    assert "SRVR-009" in s.numero
    assert s.source_url.endswith("docs/Auto_SRVR-009-ADHC-CAS0-04_28-noviembre-2025.htm")


def test_acuerdo_y_resolucion():
    d = JEPDiscoverer()
    seeds = d._seeds_from_payload(_ITEMS)
    ac = seeds["acuerdo_jep_0015aog_2023.htm"]
    assert ac.tipo == "ACUERDO" and ac.numero == "15" and ac.anio == "2023"
    res = seeds["resolucion_jep_0235_2026.htm"]
    assert res.tipo == "RESOLUCION" and res.numero == "235" and res.anio == "2026"


def test_incluir_espejos_capta_ley_como_mirror():
    # Con incluir_espejos=True la Ley estatutaria sí aparece (tipo LEY).
    d = JEPDiscoverer(incluir_espejos=True)
    seeds = d._seeds_from_payload(_ITEMS)
    ley = seeds["ley_1957_2019.htm"]
    assert ley.tipo == "LEY" and ley.numero == "1957" and ley.anio == "2019"
    assert ley.canonical_id == "co:ley:1957:2019"


def test_incluir_espejos_flag():
    d = JEPDiscoverer(incluir_espejos=True)
    seeds = d._seeds_from_payload(_ITEMS)
    assert "C-080_2018.htm" in seeds
    assert seeds["C-080_2018.htm"].tipo == "SENTENCIA"
