"""Tests del DiarioOficialDiscoverer (parsing de la tabla PrimeFaces, sin red).

Fixtures = bytes HTML REALES capturados en vivo del buscador JSF
``https://svrpubindc.imprenta.gov.co/diario/`` el 2026-06-19.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.diario_oficial_discoverer import DiarioOficialDiscoverer


# Fila REAL de la tabla dtbDiariosOficiales_data (edición 53.526), capturada
# verbatim del DOM en vivo.
REAL_ROW = (
    '<tr data-ri="0" data-rk="53.526" class="ui-widget-content ui-datatable-even '
    'ui-datatable-selectable" role="row" aria-selected="false">'
    '<td role="gridcell" class="colTextoCorto">'
    '<label id="dtbDiariosOficiales:0:numeroDiario" class="ui-outputlabel ui-widget">53.526</label></td>'
    '<td role="gridcell" class="colTextoMediano">'
    '<label id="dtbDiariosOficiales:0:tipoEdicion" class="ui-outputlabel ui-widget">Ordinaria</label></td>'
    '<td role="gridcell" class="colTextoCorto">'
    '<label id="dtbDiariosOficiales:0:fechaDiario" class="ui-outputlabel ui-widget">18/06/2026</label></td>'
    '<td role="gridcell" class="colIconoAjustable">'
    '<button id="dtbDiariosOficiales:0:j_idt38" name="dtbDiariosOficiales:0:j_idt38" '
    'class="ui-button boton-icono" onclick="PF(\'statusDialog\').show();;" title="Ver Diario" '
    'type="submit" role="button"><span class="ui-icon ui-icon-search"></span>'
    '<span class="ui-button-text ui-c">ui-button</span></button></td></tr>'
)

# Varias ediciones recientes reales (mismo formato), para probar enumeración.
REAL_TABLE = "".join(
    REAL_ROW.replace('data-rk="53.526"', f'data-rk="{rk}"')
    .replace(">53.526<", f">{rk}<")
    .replace(">18/06/2026<", f">{fecha}<")
    .replace('data-ri="0"', f'data-ri="{i}"')
    .replace("dtbDiariosOficiales:0:", f"dtbDiariosOficiales:{i}:")
    for i, (rk, fecha) in enumerate(
        [("53.526", "18/06/2026"), ("53.525", "17/06/2026"), ("53.524", "16/06/2026")]
    )
)


def test_parse_fila_unica():
    d = DiarioOficialDiscoverer()
    seeds = d._parse_resultados(REAL_ROW)
    assert len(seeds) == 1
    s = seeds[0]
    assert s.source == "diario_oficial"
    assert s.tipo == "DIARIO OFICIAL"
    # número de miles "53.526" → "53526"
    assert s.numero == "53526"
    assert s.external_id == "53526"
    assert s.anio == "2026"


def test_fecha_promulgacion_en_extra():
    d = DiarioOficialDiscoverer()
    s = d._parse_resultados(REAL_ROW)[0]
    # El dato valioso: fecha de publicación ISO en extra.
    assert s.extra["fecha"] == "2026-06-18"
    assert s.extra["edicion"] == "53.526"
    assert s.extra["tipo_edicion"] == "ORDINARIA"
    assert s.extra["unidad"] == "EDICION_DIARIO_OFICIAL"


def test_source_url_es_pagina_detalle_no_pdf():
    d = DiarioOficialDiscoverer()
    s = d._parse_resultados(REAL_ROW)[0]
    # El PDF es session-bound (JSF); source_url apunta a la página de detalle.
    assert s.source_url.endswith("/diario/view/diarioficial/detallesPdf.xhtml")
    assert s.extra["descarga"] == "jsf_session_bound"
    # Una edición no es una norma individual → sin canonical_id.
    assert s.canonical_id is None


def test_enumera_varias_ediciones():
    d = DiarioOficialDiscoverer()
    seeds = d._parse_resultados(REAL_TABLE)
    numeros = sorted(s.numero for s in seeds)
    assert numeros == ["53524", "53525", "53526"]
    # Cada una con su fecha distinta.
    fechas = {s.numero: s.extra["fecha"] for s in seeds}
    assert fechas["53526"] == "2026-06-18"
    assert fechas["53524"] == "2026-06-16"


def test_fila_sin_numero_valido_se_ignora():
    bad = (
        '<tr data-rk="x"><td><label id="dtbDiariosOficiales:0:numeroDiario" '
        'class="ui-outputlabel">no-numero</label></td></tr>'
    )
    d = DiarioOficialDiscoverer()
    assert d._parse_resultados(bad) == []
