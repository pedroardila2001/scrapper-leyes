"""Tests del BancoRepublicaDiscoverer (parsing del índice Drupal Views, sin red).

Fixtures = bytes HTML REALES capturados en vivo de
``https://www.banrep.gov.co/es/reglamentacion-temas/2153`` el 2026-06-19.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.banco_republica_discoverer import BancoRepublicaDiscoverer


# Fragmento REAL (página 0): una fila de Circular Reglamentaria Externa y una de
# Resolución Externa, dentro de <table class="cols-0">.
REAL_INDEX_HTML = """
<table class="cols-0">
  <tbody>
    <tr>
      <td class="views-field views-field-title"><b><a href="/es/bjd-17-2015" hreflang="es">Circular Reglamentaria Externa DODM-139</a></b>
<br>
<div class="icono-descarga-gray"><ul><li><span class="file file--mime-application-pdf file--application-pdf"><a href="/sites/default/files/reglamentacion/archivos/bjd_17_2015.pdf" type="application/pdf" title="bjd_17_2015.pdf" target="_blank">Boletín núm. 17</a></span></li></ul></div>          </td>
    </tr>
    <tr>
      <td class="views-field views-field-title"><b><a href="/es/node/36558" hreflang="es">Resolución Externa No. 10 de 2014 del 26 de Septiembre de 2014 "Por la cual se expiden regulaciones sobre los sistemas de compensación y liquidación de divisas y sus operadores"</a></b>
<br>
<div class="icono-descarga-gray"><ul><li><span class="file file--mime-application-pdf file--application-pdf"><a href="/sites/default/files/reglamentacion/archivos/bjd_37_2014.pdf" type="application/pdf" title="bjd_37_2014.pdf" target="_blank">Boletín núm. 37</a></span></li></ul></div>          </td>
    </tr>
  </tbody>
</table>
"""


def _by_url(seeds):
    return {s.source_url.rsplit("/", 1)[-1]: s for s in seeds}


def test_parse_index_devuelve_dos_seeds():
    d = BancoRepublicaDiscoverer()
    seeds = d._parse_index(REAL_INDEX_HTML)
    assert len(seeds) == 2
    assert all(s.source == "banco_republica" for s in seeds)


def test_circular_reglamentaria_externa():
    d = BancoRepublicaDiscoverer()
    seeds = _by_url(d._parse_index(REAL_INDEX_HTML))
    s = seeds["bjd_17_2015.pdf"]
    assert s.tipo == "CIRCULAR"
    assert s.subtipo == "CIRCULAR REGLAMENTARIA EXTERNA"
    assert s.numero == "139"
    assert s.anio == "2015"
    assert s.extra["dependencia"] == "DODM"
    assert s.extra["boletin"] == "17"
    assert s.source_url.endswith("/sites/default/files/reglamentacion/archivos/bjd_17_2015.pdf")
    assert s.source_url.startswith("https://www.banrep.gov.co")


def test_resolucion_externa():
    d = BancoRepublicaDiscoverer()
    seeds = _by_url(d._parse_index(REAL_INDEX_HTML))
    s = seeds["bjd_37_2014.pdf"]
    assert s.tipo == "RESOLUCION"
    assert s.subtipo == "RESOLUCION EXTERNA"
    assert s.numero == "10"
    assert s.anio == "2014"
    # canonical_id se construye desde el tipo base RESOLUCION (el subtipo
    # "RESOLUCION EXTERNA" queda en s.subtipo, no en el id).
    assert s.canonical_id == "co:resolucion:10:2014"
    # external_id = página de detalle (landing) resuelta absoluta.
    assert s.external_id == "https://www.banrep.gov.co/es/node/36558"
    assert "compensación" in s.extra["titulo"] or "compensaci" in s.extra["titulo"]


def test_fila_sin_pdf_se_ignora():
    html = """
    <table class="cols-0"><tbody><tr>
      <td class="views-field views-field-title"><b><a href="/es/algo" hreflang="es">Nota sin PDF</a></b></td>
    </tr></tbody></table>
    """
    d = BancoRepublicaDiscoverer()
    assert d._parse_index(html) == []


def test_canonical_id_circular_presente():
    d = BancoRepublicaDiscoverer()
    seeds = _by_url(d._parse_index(REAL_INDEX_HTML))
    s = seeds["bjd_17_2015.pdf"]
    # build_canonical_id no debe explotar; circular con numero/anio.
    assert s.canonical_id is not None
    assert s.canonical_id.startswith("co:circular:")
