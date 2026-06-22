"""Tests for the RESUELVE (operative part) parser."""

from __future__ import annotations

from scrapper_leyes.sentencia_decision import (
    EXEQUIBLE,
    EXEQUIBLE_CONDICIONADA,
    INEXEQUIBLE,
    INHIBIDA,
    ESTARSE_A_LO_RESUELTO,
    classify_decision,
    parse_operative_orders,
)

RESUELVE = """\
**RESUELVE:**

**Primero.-** Declararse INHIBIDA para decidir de fondo sobre el artículo 15 de la Ley 1527 de 2012, por ineptitud sustantiva de la demanda.

**Segundo.-** Declarar EXEQUIBLE, por los cargos analizados, el artículo 18 de la Ley 1712 de 2014.

**Tercero.-** Declarar EXEQUIBLE el artículo 19 de la Ley 1712 de 2014, en el entendido de que la reserva no aplica a información sobre violaciones de derechos humanos.

**Cuarto.-** Declarar INEXEQUIBLE el parágrafo del artículo 20 de la Ley 1712 de 2014.

Notifíquese, comuníquese y cúmplase.
"""


def test_parses_each_numeral_in_order():
    orders = parse_operative_orders(RESUELVE)
    assert [o.order_number for o in orders] == [1, 2, 3, 4]
    assert orders[0].ordinal_label == "Primero"


def test_decision_types_classified():
    orders = parse_operative_orders(RESUELVE)
    assert orders[0].decision_type == INHIBIDA
    assert orders[1].decision_type == EXEQUIBLE
    assert orders[2].decision_type == EXEQUIBLE_CONDICIONADA
    assert orders[3].decision_type == INEXEQUIBLE


def test_scope_extracted_for_exequible():
    orders = parse_operative_orders(RESUELVE)
    assert orders[1].scope is not None
    assert "cargos analizados" in orders[1].scope.lower()


def test_condicion_extracted_for_condicionada():
    orders = parse_operative_orders(RESUELVE)
    assert orders[2].condicion is not None
    assert "derechos humanos" in orders[2].condicion.lower()


def test_targets_resolved_to_structured_citations():
    orders = parse_operative_orders(RESUELVE)
    # Order 2 names "artículo 18 de la Ley 1712 de 2014".
    targets = orders[1].targets
    assert any(
        t.get("type") == "ley" and t.get("numero") == "1712" and t.get("anio") == "2014"
        for t in targets
    )


def test_condicionada_not_misread_as_plain_exequible():
    # "en el entendido de que" must win over a bare "exequible".
    assert classify_decision("Declarar EXEQUIBLE en el entendido de que X") == EXEQUIBLE_CONDICIONADA


def test_estarse_a_lo_resuelto():
    assert classify_decision("ESTARSE A LO RESUELTO en la sentencia C-123 de 2010") == ESTARSE_A_LO_RESUELTO


def test_empty_or_unnumbered_returns_empty():
    assert parse_operative_orders("") == []
    assert parse_operative_orders("Texto sin numerales reconocibles.") == []


def test_numeric_numerals_also_parse():
    txt = "1. Declarar EXEQUIBLE el artículo 1.\n2. Declarar INEXEQUIBLE el artículo 2."
    orders = parse_operative_orders(txt)
    assert [o.order_number for o in orders] == [1, 2]
    assert orders[1].decision_type == INEXEQUIBLE
