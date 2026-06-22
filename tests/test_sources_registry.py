"""Tests del motor de fuentes: registro, taxonomía y tipos canónicos nuevos."""

from __future__ import annotations

import pytest

from scrapper_leyes.models import build_canonical_id, validate_canonical_id
from scrapper_leyes.sources import (
    SOURCE_REGISTRY,
    all_sources,
    get_source,
    pending_sources,
)
from scrapper_leyes.taxonomia import classify


# ── Registro ─────────────────────────────────────────────────────────────────


def test_registro_cubre_las_fuentes_que_faltaban():
    # Las 6 fuentes de la tabla del usuario están registradas.
    for key in (
        "corte_idh", "creg", "crc", "cra", "banco_republica",
        "organos_control", "cne", "can",
    ):
        assert key in SOURCE_REGISTRY, key
        assert get_source(key).spike, f"{key} sin spike definido"


def test_conectores_construidos_y_sin_pendientes_puros():
    from scrapper_leyes.sources import EST_ANDAMIAJE, EST_PENDIENTE

    assert get_source("suin").implementado
    # Conectores construidos y verificados en vivo que descubren docs reales (parcial).
    for k in ("jep", "can", "banco_republica", "organos_control", "regimen_bogota",
              "creg", "corte_idh", "cne", "funcion_publica"):
        assert get_source(k).implementado, k
    # Andamiaje: discoverer escrito pero el buscador real es JSF/AJAX/WAF (pendiente
    # de capturar el endpoint). Honesto, no falso positivo.
    assert get_source("diario_oficial").estado == EST_ANDAMIAJE
    assert get_source("diario_oficial") in pending_sources()  # andamiaje ⊂ no-implementado
    # Ya NINGUNA fuente queda como 'pendiente' pura: todas tienen conector cableado.
    assert [s.key for s in all_sources() if s.estado == EST_PENDIENTE] == []


def test_specs_bien_formados():
    for s in all_sources():
        assert s.capa in ("A", "B", "C", "D")
        assert s.modo in ("catalogo", "crawl")
        assert s.tipos, f"{s.key} sin tipos"


# ── Factory: error accionable para fuentes sin conector ──────────────────────


def test_factory_error_accionable():
    pytest.importorskip("httpx")
    from scrapper_leyes.config import Settings
    from scrapper_leyes.scraper.factory import ScraperFactory

    f = ScraperFactory(Settings(), db=None, cache=None)
    # corte_idh ya tiene discoverer Y ahora scraper de TEXTO genérico (UrlScraper):
    # baja su source_url y guarda el texto. Las fuentes registradas son scrapeables.
    from scrapper_leyes.scraper.url_scraper import UrlScraper
    assert isinstance(f.get_scraper("corte_idh"), UrlScraper)
    # Una fuente desconocida sigue siendo un ValueError accionable.
    with pytest.raises(ValueError):
        f.get_scraper("fuente_inventada")


# ── Taxonomía de las fuentes nuevas ──────────────────────────────────────────


def test_corte_idh_es_internacional():
    rama, cabeza, _ = classify("SENTENCIA", None, None, corte="idh")
    assert rama == "Internacional"
    assert cabeza == "Sistema Interamericano"


def test_comisiones_regulacion_cabeza_propia():
    rama, cabeza, _ = classify("RESOLUCION", "Minas y Energía", "Comisión de Regulación de Energía y Gas")
    assert rama == "Rama Ejecutiva"
    assert cabeza == "Comisiones de Regulación"


def test_organos_control_y_electoral():
    assert classify("RESOLUCION", None, "Procuraduría General de la Nación")[0] == "Organismos de Control"
    assert classify("RESOLUCION", None, "Consejo Nacional Electoral")[0] == "Órgano Electoral"


def test_can_internacional():
    assert classify("SENTENCIA", None, None, corte="can")[0] == "Internacional"


# ── IDs canónicos de tipos nuevos ────────────────────────────────────────────


def test_canonical_ids_tipos_nuevos():
    assert build_canonical_id("TRATADO", "espurio", "1969") == "co:tratado:espurio:1969"
    assert build_canonical_id("CONCEPTO", "1234", "2022") == "co:concepto:1234:2022"
    assert build_canonical_id("DECISION CAN", "486", "2000") == "co:decision_can:486:2000"
    # Sentencia Corte IDH con corte/sala.
    cid = build_canonical_id("SENTENCIA", "gelman", "2011", corte="idh", sala="fondo")
    assert cid == "co:sentencia:idh:fondo:gelman:2011"
    assert validate_canonical_id("co:concepto:1234:2022")
