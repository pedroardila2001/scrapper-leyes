"""Tests del RegimenBogotaDiscoverer (parsing de la ficha sisjur, sin red).

Fixtures capturados EN VIVO el 2026-06-19 de
``https://www.alcaldiabogota.gov.co/sisjur/normas/Norma1.jsp?i=<ID>``:
  - ``sisjur_norma_13935.html`` → Decreto 190 de 2004 (POT consolidado).
  - ``sisjur_norma_empty.html``  → ficha vacía (i=1, stub) → debe devolver None.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from scrapper_leyes.scraper.regimen_bogota_discoverer import RegimenBogotaDiscoverer

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_ficha_decreto_real():
    d = RegimenBogotaDiscoverer()
    seed = d._parse_ficha(_load("sisjur_norma_13935.html"), "13935")
    assert seed is not None
    assert seed.tipo == "DECRETO"
    assert seed.numero == "190"
    assert seed.anio == "2004"
    assert seed.source == "regimen_bogota"
    assert seed.external_id == "13935"
    assert seed.source_url.endswith("Norma1.jsp?i=13935")
    assert seed.canonical_id == "co:decreto:190:2004"
    assert seed.extra["ambito"] == "Distrital"


def test_parse_ficha_extrae_metadatos():
    d = RegimenBogotaDiscoverer()
    seed = d._parse_ficha(_load("sisjur_norma_13935.html"), "13935")
    assert seed.extra["fecha_expedicion"] == "22/06/2004"
    assert seed.extra["fecha_vigencia"] == "22/06/2004"
    assert "Registro Distrital 3122" in seed.extra["medio_publicacion"]
    assert "Alcald" in seed.extra["entidad_emisora"]


def test_ficha_vacia_es_none():
    d = RegimenBogotaDiscoverer()
    assert d._parse_ficha(_load("sisjur_norma_empty.html"), "1") is None


def test_filtro_de_tipos():
    # Por defecto solo distritales; un decreto pasa.
    d = RegimenBogotaDiscoverer()
    assert d._parse_ficha(_load("sisjur_norma_13935.html"), "13935") is not None
    # Si limito a solo ACUERDO, el decreto se descarta.
    d2 = RegimenBogotaDiscoverer(tipos=("ACUERDO",))
    assert d2._parse_ficha(_load("sisjur_norma_13935.html"), "13935") is None


def test_parse_ficha_acuerdo_template():
    """El template de ficha es idéntico entre tipos; un Acuerdo distrital se parsea
    sobre la MISMA estructura real (solo cambian título/valores)."""
    html = _load("sisjur_norma_13935.html").replace(
        "Decreto  190 de 2004 Alcald&iacute;a Mayor de Bogot&aacute;, D.C.",
        "Acuerdo 257 de 2006 Concejo de Bogot&aacute;, D.C.",
    ).replace(
        "Decreto 190 de 2004 Alcald&iacute;a Mayor de Bogot&aacute;, D.C.",
        "Acuerdo 257 de 2006 Concejo de Bogot&aacute;, D.C.",
    )
    d = RegimenBogotaDiscoverer()
    seed = d._parse_ficha(html, "22307")
    assert seed is not None
    assert (seed.tipo, seed.numero, seed.anio) == ("ACUERDO", "257", "2006")
    assert seed.canonical_id == "co:acuerdo:257:2006"


def test_parse_index_cosecha_ids():
    """_parse_index cosecha enlaces Norma1.jsp?i=<ID> con texto tipo/número/año."""
    html = (
        '<ul>'
        '<a href="normas/Norma1.jsp?i=13935">Decreto 190 de 2004</a>'
        '<a href="normas/Norma1.jsp?i=22307">Acuerdo 257 de 2006</a>'
        '<a href="otra.jsp?x=1">ruido</a>'
        '</ul>'
    )
    d = RegimenBogotaDiscoverer()
    seeds = d._parse_index(html)
    assert len(seeds) == 2
    ids = {s.external_id for s in seeds}
    assert ids == {"13935", "22307"}
    by_id = {s.external_id: s for s in seeds}
    assert by_id["13935"].tipo == "DECRETO"
    assert by_id["22307"].canonical_id == "co:acuerdo:257:2006"
