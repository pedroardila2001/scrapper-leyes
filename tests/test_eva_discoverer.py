"""Tests del EVADiscoverer — parsing del índice normasfp.php (sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.eva_discoverer import EVADiscoverer

# Fragmento representativo de un índice normasfp.php de EVA (Función Pública).
# Cada norma es un <a href="norma.php?i=<ID>">Tipo <num> de <año></a>. Incluye
# enlaces de paginación normasfp.php?pag=N y un enlace de menú que NO es norma.
INDEX_HTML = """
<html><body>
<div class="resultados">
  <ul>
    <li><a href="norma.php?i=49981">Ley 1437 de 2011</a> &ndash; CPACA</li>
    <li><a href="/eva/gestornormativo/norma.php?i=62866">Decreto &Uacute;nico 1083 de 2015</a></li>
    <li><a href='norma.php?i=87764'>Resoluci&oacute;n No. 0312 de 2019</a></li>
    <li><a href="norma.php?i=125305">Concepto 123456 de 2021</a></li>
    <li><a href="norma.php?i=30019">Acuerdo 565 de 2016</a></li>
    <li><a href="norma.php?i=49981">Ley 1437 de 2011</a></li>  <!-- duplicado -->
  </ul>
</div>
<div class="paginador">
  <a href="normasfp.php?pag=2">Siguiente</a>
  <a href="normasfp.php?anio=2020">2020</a>
</div>
<a href="https://www.funcionpublica.gov.co/eva/inicio">Inicio</a>
</body></html>
"""


def test_parse_index_basic():
    d = EVADiscoverer()
    seeds = d._parse_index(INDEX_HTML)
    # 5 normas únicas (el duplicado i=49981 se colapsa).
    assert len(seeds) == 5
    by_id = {s.external_id: s for s in seeds}
    assert set(by_id) == {"49981", "62866", "87764", "125305", "30019"}


def test_parse_ley_canonical_and_url():
    d = EVADiscoverer()
    s = {x.external_id: x for x in d._parse_index(INDEX_HTML)}["49981"]
    assert (s.tipo, s.numero, s.anio) == ("LEY", "1437", "2011")
    assert s.source == "funcion_publica"
    assert s.canonical_id == "co:ley:1437:2011"
    assert s.source_url == (
        "https://www.funcionpublica.gov.co/eva/gestornormativo/norma_pdf.php?i=49981"
    )
    assert s.extra["vista_html"].endswith("norma.php?i=49981")


def test_parse_decreto_unico_entities_decoded():
    d = EVADiscoverer()
    s = {x.external_id: x for x in d._parse_index(INDEX_HTML)}["62866"]
    # "Decreto Único 1083 de 2015" → tipo DECRETO, número y año correctos.
    assert s.tipo == "DECRETO"
    assert (s.numero, s.anio) == ("1083", "2015")
    assert s.canonical_id == "co:decreto:1083:2015"


def test_parse_resolucion_con_ceros_y_No():
    d = EVADiscoverer()
    s = {x.external_id: x for x in d._parse_index(INDEX_HTML)}["87764"]
    assert s.tipo == "RESOLUCION"
    assert s.numero == "0312"  # ceros a la izquierda preservados
    assert s.anio == "2019"


def test_parse_concepto():
    d = EVADiscoverer()
    s = {x.external_id: x for x in d._parse_index(INDEX_HTML)}["125305"]
    assert s.tipo == "CONCEPTO"
    assert (s.numero, s.anio) == ("123456", "2021")
    assert s.canonical_id == "co:concepto:123456:2021"


def test_index_links_harvested():
    d = EVADiscoverer()
    links = d._index_links(
        INDEX_HTML, "https://www.funcionpublica.gov.co/eva/gestornormativo/normasfp.php"
    )
    assert any("pag=2" in u for u in links)
    assert any("anio=2020" in u for u in links)
    # No incluye el enlace de menú /eva/inicio.
    assert all("normasfp.php" in u for u in links)


def test_non_norma_links_ignored():
    d = EVADiscoverer()
    seeds = d._parse_index('<a href="https://x/eva/inicio">Inicio</a>')
    assert seeds == []
