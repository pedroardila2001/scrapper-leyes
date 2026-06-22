"""Tests del OrganosControlDiscoverer (parsers puros, sin red).

Las fixtures son fragmentos basados en los patrones REALES verificados en
``docs/SPIKE_FUENTES.md`` y en URLs reales de cada portal (Procuraduría SIREL,
Contraloría blob ``$web``, CNDJ ``docs_relatoria``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.organos_control_discoverer import (
    SOURCE,
    OrganosControlDiscoverer,
)


# ════════════════════════════════════════════════════════════════════════
# Constructor
# ════════════════════════════════════════════════════════════════════════
def test_source_key():
    assert SOURCE == "organos_control"


def test_default_cubre_las_tres_entidades():
    d = OrganosControlDiscoverer()
    assert d.entidades == ("procuraduria", "contraloria", "cndj")


def test_subconjunto_de_entidades():
    d = OrganosControlDiscoverer(entidades=("contraloria",))
    assert d.entidades == ("contraloria",)


def test_entidad_invalida():
    with pytest.raises(ValueError):
        OrganosControlDiscoverer(entidades=("inventada",))


def test_sin_entidades():
    with pytest.raises(ValueError):
        OrganosControlDiscoverer(entidades=())


def test_dedup_entidades():
    d = OrganosControlDiscoverer(entidades=("cndj", "cndj", "contraloria"))
    assert d.entidades == ("cndj", "contraloria")


# ════════════════════════════════════════════════════════════════════════
# CONTRALORÍA — patrón de blob determinista
# ════════════════════════════════════════════════════════════════════════
def test_contraloria_concepto_url():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.blob.core.windows.net/$web/files/conceptos-juridicos/CGR-OJ-144-2025.pdf"
    s = d._seed_from_cgr_url(url)
    assert s is not None
    assert s.tipo == "CONCEPTO"
    assert s.numero == "144"
    assert s.anio == "2025"
    assert s.source == "organos_control"
    assert s.external_id == "CGR-OJ-144-2025"
    assert s.extra["entidad"] == "CONTRALORIA"
    assert s.extra["serie"] == "CGR-OJ"
    assert s.canonical_id == "co:concepto:144:2025"
    assert s.source_url == url


def test_contraloria_concepto_padding_y_extension_mayus():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.blob.core.windows.net/$web/files/conceptos-juridicos/CGR-OJ-001-2024.PDF"
    s = d._seed_from_cgr_url(url)
    assert s is not None
    assert s.numero == "1"  # padding eliminado del numero canonico
    assert s.external_id == "CGR-OJ-001-2024"  # external_id conserva el literal
    assert s.canonical_id == "co:concepto:1:2024"


def test_contraloria_resolucion_reg_eje():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.blob.core.windows.net/$web/files/resoluciones/REG-EJE-141-2024.pdf"
    s = d._seed_from_cgr_url(url)
    assert s is not None
    assert s.tipo == "RESOLUCION"
    assert s.numero == "141"
    assert s.anio == "2024"
    assert s.extra["serie"] == "REG-EJE"


def test_contraloria_resolucion_ogz():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.blob.core.windows.net/$web/files/resoluciones/OGZ-0812-2022.PDF"
    s = d._seed_from_cgr_url(url)
    assert s is not None
    assert s.tipo == "RESOLUCION"
    assert s.numero == "812"
    assert s.extra["serie"] == "OGZ"


def test_contraloria_url_no_matchea():
    d = OrganosControlDiscoverer()
    assert d._seed_from_cgr_url("https://relatoria.blob.core.windows.net/$web/index.html") is None


def test_contraloria_parse_index():
    d = OrganosControlDiscoverer()
    # Microsite/índice que enumera blobs como enlaces directos.
    index_html = """
    <ul>
      <li><a href="https://relatoria.blob.core.windows.net/$web/files/conceptos-juridicos/CGR-OJ-001-2025.pdf">Concepto 001</a></li>
      <li><a href="https://relatoria.blob.core.windows.net/$web/files/conceptos-juridicos/CGR-OJ-144-2025.pdf">Concepto 144</a></li>
      <li><a href="https://relatoria.blob.core.windows.net/$web/files/resoluciones/REG-EJE-141-2024.pdf">Res 141</a></li>
      <li><a href="https://otrositio.gov.co/algo.pdf">ruido (no blob)</a></li>
    </ul>
    """
    seeds = d._parse_contraloria_index(index_html)
    assert len(seeds) == 3
    by_id = {s.external_id: s for s in seeds}
    assert "CGR-OJ-001-2025" in by_id
    assert "CGR-OJ-144-2025" in by_id
    assert "REG-EJE-141-2024" in by_id
    assert by_id["CGR-OJ-144-2025"].tipo == "CONCEPTO"
    assert by_id["REG-EJE-141-2024"].tipo == "RESOLUCION"


def test_contraloria_candidate_urls():
    d = OrganosControlDiscoverer()
    urls = d._cgr_candidate_urls("CONCEPTO", 5, 2024)
    # variantes de padding (3/4) x extension (PDF/pdf)
    assert any(u.endswith("CGR-OJ-005-2024.PDF") for u in urls)
    assert any(u.endswith("CGR-OJ-0005-2024.pdf") for u in urls)
    assert all("conceptos-juridicos" in u for u in urls)


# ════════════════════════════════════════════════════════════════════════
# CNDJ — PDF en docs_relatoria/<rad+ADJUNTA+timestamp>.pdf
# ════════════════════════════════════════════════════════════════════════
def test_cndj_seed_from_url():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.cndj.gov.co/docs_relatoria/F52001250200020230046801ADJUNTA20240207110359.pdf"
    s = d._seed_from_cndj_url(url)
    assert s is not None
    assert s.tipo == "FALLO DISCIPLINARIO"
    assert s.source == "organos_control"
    assert s.extra["entidad"] == "CNDJ"
    assert s.extra["radicado"] == "F52001250200020230046801"
    assert s.extra["timestamp"] == "20240207110359"
    # año embebido en el radicado (2023)
    assert s.anio == "2023"
    assert s.external_id == "F52001250200020230046801ADJUNTA20240207110359"
    assert s.source_url == url


def test_cndj_anio_de_otro_radicado():
    d = OrganosControlDiscoverer()
    url = "https://relatoria.cndj.gov.co/docs_relatoria/F11001110200020200085701ADJUNTA20231026141206.pdf"
    s = d._seed_from_cndj_url(url)
    assert s is not None
    assert s.anio == "2020"  # radicado 2020, no el timestamp 2023


def test_cndj_url_no_matchea():
    d = OrganosControlDiscoverer()
    assert d._seed_from_cndj_url("https://relatoria.cndj.gov.co/index.html") is None


def test_cndj_parse_results_html():
    d = OrganosControlDiscoverer()
    results_html = """
    <table><tbody>
      <tr><td>A 13316</td><td><a href="https://relatoria.cndj.gov.co/docs_relatoria/F11001250200020230149001ADJUNTA20240823085120.pdf">Ver</a></td></tr>
      <tr><td>F 2195</td><td><a href="https://relatoria.cndj.gov.co/docs_relatoria/F11001010200020180044300ADJUNTA20211029171024.pdf">Ver</a></td></tr>
    </tbody></table>
    """
    seeds = d._parse_cndj_results(results_html)
    assert len(seeds) == 2
    anios = sorted(s.anio for s in seeds)
    assert anios == ["2018", "2023"]
    assert all(s.extra["entidad"] == "CNDJ" for s in seeds)


def test_cndj_parse_results_json():
    d = OrganosControlDiscoverer()
    # Respuesta XHR estilo JSON con nombres de archivo sueltos.
    payload = (
        '{"results":[{"rad":"A 9859","archivo":'
        '"F11001110200020200085701ADJUNTA20231026141206.pdf"}]}'
    )
    seeds = d._parse_cndj_results(payload)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.source_url.endswith("F11001110200020200085701ADJUNTA20231026141206.pdf")
    assert s.anio == "2020"


# ════════════════════════════════════════════════════════════════════════
# PROCURADURÍA — SIREL (PDF media/file + HTML docs/<rad>.html)
# ════════════════════════════════════════════════════════════════════════
def test_procuraduria_parse_results_pdf():
    d = OrganosControlDiscoverer()
    results_html = """
    <div class="resultado">
      <span class="tipo">Concepto</span> C-123 de 2022
      <a href="https://apps.procuraduria.gov.co/relatoria/media/file/C123-2022">Descargar PDF</a>
    </div>
    <div class="resultado">
      <span class="tipo">Fallo disciplinario</span> radicado IUS-2021-555 año 2021
      <a href="https://apps.procuraduria.gov.co/relatoria/media/file/IUS2021555">Descargar PDF</a>
    </div>
    """
    seeds = d._parse_procuraduria_results(results_html)
    assert len(seeds) == 2
    by_id = {s.external_id: s for s in seeds}
    assert "C123-2022" in by_id
    concepto = by_id["C123-2022"]
    assert concepto.tipo == "CONCEPTO"
    assert concepto.anio == "2022"
    assert concepto.source == "organos_control"
    assert concepto.extra["entidad"] == "PROCURADURIA"
    fallo = by_id["IUS2021555"]
    assert fallo.tipo == "FALLO DISCIPLINARIO"
    assert fallo.anio == "2021"


def test_procuraduria_parse_results_html_doc():
    d = OrganosControlDiscoverer()
    results_html = """
    <li>Concepto 0099 de 2020
      <a href="https://apps.procuraduria.gov.co/guia/relatoria/docs/c0099-2020.html">Ver HTML</a>
    </li>
    """
    seeds = d._parse_procuraduria_results(results_html)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.external_id == "c0099-2020"
    assert s.source_url.endswith("docs/c0099-2020.html")
    assert s.extra["entidad"] == "PROCURADURIA"


def test_procuraduria_parse_total():
    d = OrganosControlDiscoverer()
    assert d._parse_procuraduria_total('mostrando de 26.835 resultados') == 26835
    assert d._parse_procuraduria_total('"total_results": 1234') == 1234
    assert d._parse_procuraduria_total("nada relevante") == 0
