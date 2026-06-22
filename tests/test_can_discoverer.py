"""Tests del CANDiscoverer (parsing del listado WordPress, sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.can_discoverer import CANDiscoverer

# Fragmento del listado de Decisiones (WordPress de comunidadandina.org): anclas
# a los PDF DECISION<N>.pdf con título y año en el texto, más un proceso TJCAN.
_LISTING = """
<ul class="lista-decisiones">
  <li>
    <a href="https://www.comunidadandina.org/DocOficialesFiles/decisiones/DECISION486.pdf">
      Decisión 486 - Régimen Común sobre Propiedad Industrial (2000)
    </a>
  </li>
  <li>
    <a href="/DocOficialesFiles/decisiones/DECISION792.pdf">
      <span>Decisión 792</span> - Implementación de la Reingeniería del Sistema Andino de Integración (2013)
    </a>
  </li>
  <li>
    <a href="https://www.comunidadandina.org/DocOficialesFiles/Procesos/01-IP-2020.pdf">
      Proceso 01-IP-2020 Interpretación Prejudicial
    </a>
  </li>
</ul>
"""


def test_source_invalido():
    with pytest.raises(ValueError):
        CANDiscoverer("inventada")


def test_parse_listing_decisiones():
    d = CANDiscoverer()
    seeds = {s.numero: s for s in d._parse_listing(_LISTING) if s.tipo == "DECISION CAN"}
    assert set(seeds) == {"486", "792"}

    s486 = seeds["486"]
    assert s486.tipo == "DECISION CAN"
    assert s486.source == "can"
    assert s486.corte == "can"
    assert s486.external_id == "486"
    assert s486.anio == "2000"
    assert s486.source_url == (
        "https://www.comunidadandina.org/DocOficialesFiles/decisiones/DECISION486.pdf"
    )
    assert s486.canonical_id == "co:decision_can:486:2000"
    assert "Propiedad Industrial" in s486.extra["titulo"]

    s792 = seeds["792"]
    assert s792.anio == "2013"
    # Enlace relativo → se completa con el host base.
    assert s792.source_url.endswith("/DocOficialesFiles/decisiones/DECISION792.pdf")
    assert s792.source_url.startswith("https://www.comunidadandina.org")


def test_parse_listing_proceso_tribunal():
    d = CANDiscoverer(incluir_tribunal=True)
    procs = [s for s in d._parse_listing(_LISTING) if s.tipo == "SENTENCIA"]
    assert len(procs) == 1
    p = procs[0]
    assert p.external_id == "01-IP-2020"
    assert p.corte == "can"
    assert p.source_url.endswith("/DocOficialesFiles/Procesos/01-IP-2020.pdf")


def test_tribunal_excluible():
    d = CANDiscoverer(incluir_tribunal=False)
    procs = [s for s in d._parse_listing(_LISTING) if s.tipo == "SENTENCIA"]
    assert procs == []


def test_seed_from_decision_sin_anio():
    d = CANDiscoverer()
    s = d._seed_from_decision(900)
    assert s.numero == "900"
    assert s.anio is None
    assert s.canonical_id is None  # sin año no se construye id canónico
    assert s.source_url.endswith("/DocOficialesFiles/decisiones/DECISION900.pdf")
    assert s.extra["organismo"] == "Comunidad Andina"
