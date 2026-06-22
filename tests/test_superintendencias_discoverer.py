"""Tests del SuperintendenciasDiscoverer (parsing puro, sin red).

Recon 2026-06-19: la URL ``loader.jsf?...&id=`` de Superfinanciera y la URL del
repositorio Drupal de la SIC (``repositorio-de-normatividad?field_tipo_de_norma_value=5``)
fueron confirmadas. Superfinanciera está tras un WAF (uzdbm) → el parser se valida
offline con el formato del listado; el acceso en vivo es el caveat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.superintendencias_discoverer import (
    SuperintendenciasDiscoverer,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_entidad_desconocida():
    with pytest.raises(ValueError):
        SuperintendenciasDiscoverer(entidades=("inventada",))


def test_constructor_default():
    d = SuperintendenciasDiscoverer()
    assert d.entidades == ("financiera", "sic")


def test_parse_financiera():
    d = SuperintendenciasDiscoverer()
    seeds = d._parse_financiera(_load("superfinanciera_listado.html"))
    # 3 documentos válidos (el 4o enlace no tiene tipo de norma).
    assert len(seeds) == 3
    by_num = {s.numero: s for s in seeds}
    ce = by_num["029"]
    assert ce.tipo == "CIRCULAR EXTERNA"
    assert ce.anio == "2014"
    assert ce.source == "superintendencias"
    assert ce.external_id == "10086076"
    assert ce.extra["entidad"] == "SUPERFINANCIERA"
    assert ce.canonical_id == "co:circular_externa:029:2014"
    # Carta Circular se mapea a CIRCULAR EXTERNA.
    assert by_num["12"].tipo == "CIRCULAR EXTERNA"
    # Concepto preservado.
    assert by_num["2021098765"].tipo == "CONCEPTO"


def test_parse_sic():
    d = SuperintendenciasDiscoverer()
    seeds = d._parse_sic(_load("sic_repositorio.html"))
    assert len(seeds) == 3  # 2 circulares + 1 concepto; "Siguiente" se ignora
    by_num = {s.numero: s for s in seeds}
    assert by_num["004"].tipo == "CIRCULAR EXTERNA"
    assert by_num["004"].extra["entidad"] == "SIC"
    # href relativo → absolutizado al host SIC.
    assert by_num["004"].source_url.startswith("https://www.sic.gov.co/")
    # href absoluto se respeta.
    assert by_num["002"].source_url.startswith(
        "https://www.sic.gov.co/sites/default/files/"
    )
    assert by_num["16-123456"].tipo == "CONCEPTO"
    assert by_num["004"].canonical_id == "co:circular_externa:004:2024"


def test_dedup_sic():
    d = SuperintendenciasDiscoverer()
    html = (
        '<a href="/a.pdf">Circular Externa 004 de 2024</a>'
        '<a href="/b.pdf">Circular Externa 004 de 2024</a>'
    )
    assert len(d._parse_sic(html)) == 1
