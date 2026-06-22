"""Tests del CNEDiscoverer — parsing de la página por año (sin red)."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.cne_discoverer import CNEDiscoverer

# Fragmento representativo de https://www.cne.gov.co/resoluciones-cne-2023
# (Joomla). Cada resolución es un enlace con slug o PDF heterogéneo; el texto
# visible trae "Resolución No. <num> de <año>". Hay ruido (menú, anclas).
YEAR_HTML = """
<html><body>
<nav><a href="#content">Saltar al contenido</a>
     <a href="javascript:void(0)">Menú</a></nav>
<div class="article-list">
  <ul>
    <li><a href="/resoluciones-cne-2023/3456-resolucion-no-1234-de-2023">
        Resoluci&oacute;n No. 1234 de 2023</a></li>
    <li><a href="resoluciones-cne-2023/3457-acto-electoral">
        RESOLUCION 0456 DE 2023</a></li>
    <li><a href="https://www.cne.gov.co/media/resoluciones/Resolucion-7890-2023.pdf">
        Resolución No. 7890 de 2023 - texto completo</a></li>
    <li><a href="/conceptos/9-concepto-12-2023">Concepto 12 de 2023</a></li>
    <li><a href="/resoluciones-cne-2023/3456-resolucion-no-1234-de-2023">
        Resoluci&oacute;n No. 1234 de 2023</a></li>  <!-- duplicado -->
    <li><a href="/inicio">Volver al inicio</a></li>
  </ul>
</div>
</body></html>
"""


def test_parse_year_page_count():
    d = CNEDiscoverer()
    seeds = d._parse_year_page(YEAR_HTML, 2023)
    # 3 resoluciones únicas + 1 concepto = 4 (duplicado 1234 colapsado; menú ignorado).
    keys = {(s.tipo, s.numero) for s in seeds}
    assert ("RESOLUCION", "1234") in keys
    assert ("RESOLUCION", "0456") in keys
    assert ("RESOLUCION", "7890") in keys
    assert ("CONCEPTO", "12") in keys
    assert len(seeds) == 4


def test_resolucion_fields_and_canonical():
    d = CNEDiscoverer()
    seeds = {(s.tipo, s.numero): s for s in d._parse_year_page(YEAR_HTML, 2023)}
    s = seeds[("RESOLUCION", "1234")]
    assert s.source == "cne"
    assert s.corte == "cne"
    assert s.anio == "2023"
    assert s.canonical_id == "co:resolucion:cne:plena:1234:2023"
    assert s.source_url.endswith("3456-resolucion-no-1234-de-2023")
    assert s.extra["entidad"] == "CNE"


def test_resolucion_mayusculas_y_ceros():
    d = CNEDiscoverer()
    seeds = {(s.tipo, s.numero): s for s in d._parse_year_page(YEAR_HTML, 2023)}
    s = seeds[("RESOLUCION", "0456")]
    assert s.numero == "0456"  # ceros preservados; "DE" en mayúsculas parseado
    assert s.anio == "2023"


def test_pdf_url_absolute_and_flagged():
    d = CNEDiscoverer()
    seeds = {(s.tipo, s.numero): s for s in d._parse_year_page(YEAR_HTML, 2023)}
    s = seeds[("RESOLUCION", "7890")]
    assert s.source_url.endswith("Resolucion-7890-2023.pdf")
    assert s.extra["es_documento"] is True


def test_concepto_canonical_sin_corte_sala():
    d = CNEDiscoverer()
    seeds = {(s.tipo, s.numero): s for s in d._parse_year_page(YEAR_HTML, 2023)}
    s = seeds[("CONCEPTO", "12")]
    assert s.canonical_id == "co:concepto:12:2023"
    assert s.corte == "cne"  # el campo corte se mantiene en el seed


def test_relative_link_joined_to_year_base():
    d = CNEDiscoverer()
    seeds = {(s.tipo, s.numero): s for s in d._parse_year_page(YEAR_HTML, 2023)}
    s = seeds[("RESOLUCION", "0456")]
    assert s.source_url.startswith("https://www.cne.gov.co/")


# Caso REAL (verificado en vivo): el ancla dice solo "Documento" y el número/año
# están en el nombre de archivo de la URL de SharePoint.
SHAREPOINT_HTML = """
<div class="article">
  <a href="https://cnegovco-my.sharepoint.com/:b:/r/personal/prensacne_cne_gov_co/Documents/Attachments/Res.%2006772%20de%202024.pdf?csf=1&web=1&e=jlnwV7">Documento</a>
  <a href="https://cnegovco-my.sharepoint.com/:b:/r/personal/prensacne_cne_gov_co/Documents/Attachments/RES%2006623%20DE%202024%201.pdf?csf=1&web=1&e=O7OPcq">Documento</a>
</div>
"""


def test_sharepoint_filename_parsing():
    d = CNEDiscoverer()
    seeds = {s.numero: s for s in d._parse_year_page(SHAREPOINT_HTML, 2024)}
    assert set(seeds) == {"06772", "06623"}
    s = seeds["06772"]
    assert s.tipo == "RESOLUCION"
    assert s.anio == "2024"
    assert s.canonical_id == "co:resolucion:cne:plena:06772:2024"
    assert s.external_id == "06772-2024"
    assert "sharepoint" in s.source_url.lower()
    assert s.extra["es_documento"] is True
