"""Tests for decision→graph-edge wiring (Neo4jExporter._export_decision_orders).

Uses a fake session that records the Cypher relationship type and the UNWIND
items, so we verify the typed-edge logic (tipo mapping, article-vs-norm target,
vigencia-consumable props) without a live Neo4j.
"""

from __future__ import annotations

import re

from scrapper_leyes.export_neo4j import Neo4jExporter


class FakeSession:
    def __init__(self):
        self.calls = []  # list of (query, params)

    def run(self, query, **params):
        self.calls.append((query, params))
        return None


def _export(parsed):
    # Bypass __init__ (needs db/cache/driver); the method only uses class attrs.
    exporter = Neo4jExporter.__new__(Neo4jExporter)
    sess = FakeSession()
    exporter._export_decision_orders(sess, "co:sentencia:cc:plena:c-274:2013", parsed)
    return sess.calls


PARSED = {
    "metadata": {"tipo": "SENTENCIA", "numero": "C-274", "anio": "2013"},
    "orders": [
        {
            "order_number": 1,
            "decision_type": "INEXEQUIBLE",
            "scope": "",
            "condicion": "",
            "targets": [
                {"type": "ley", "numero": "1712", "anio": "2014", "articulo": "18", "raw": "artículo 18 de la Ley 1712 de 2014"},
            ],
        },
        {
            "order_number": 2,
            "decision_type": "EXEQUIBLE_CONDICIONADA",
            "scope": "por los cargos analizados",
            "condicion": "la reserva no aplica a violaciones de DD.HH.",
            "targets": [
                {"type": "ley", "numero": "1712", "anio": "2014", "articulo": "", "raw": "Ley 1712 de 2014"},
            ],
        },
    ],
}


def _rel_of(query):
    m = re.search(r"MERGE \(s\)-\[r:(\w+)\]->", query)
    return m.group(1) if m else None


def test_emits_one_call_per_rel_label_group():
    calls = _export(PARSED)
    rels = {_rel_of(q) for q, _ in calls}
    assert "DECLARA_INEXEQUIBLE" in rels
    assert "DECLARA_EXEQUIBLE_CONDICIONADA" in rels


def test_article_target_builds_article_node_id_and_parent_link():
    calls = _export(PARSED)
    inex = [(q, p) for q, p in calls if _rel_of(q) == "DECLARA_INEXEQUIBLE"][0]
    q, p = inex
    item = p["items"][0]
    assert item["tid"] == "co:ley:1712:2014:art:18"
    assert item["articulo"] == "18"
    assert item["tipo"] == "INEXEQUIBLE"        # vigencia consumes this
    assert item["anio"] == "2013"               # the sentencia's year
    assert "PERTENECE_A" in q                    # article linked to its norm
    assert item["norm_id"] == "co:ley:1712:2014"


def test_norm_level_target_when_no_article():
    calls = _export(PARSED)
    cond = [(q, p) for q, p in calls if _rel_of(q) == "DECLARA_EXEQUIBLE_CONDICIONADA"][0]
    q, p = cond
    item = p["items"][0]
    assert item["tid"] == "co:ley:1712:2014"
    assert "PERTENECE_A" not in q                # norm target, no parent link
    assert item["tipo"] == "EXEQUIBLE_CONDICIONADA"
    assert item["condicion"].startswith("la reserva")
    assert "cargos analizados" in item["scope"]


def test_source_is_resuelve_for_all_edges():
    calls = _export(PARSED)
    for q, p in calls:
        assert "r.source = 'resuelve'" in q


def test_no_orders_no_calls():
    assert _export({"metadata": {}, "orders": []}) == []


def test_unanchored_target_skipped():
    parsed = {
        "metadata": {"anio": "2013"},
        "orders": [{"order_number": 1, "decision_type": "INEXEQUIBLE",
                    "targets": [{"type": "constitucion", "numero": "", "anio": "", "raw": "la Constitución"}]}],
    }
    assert _export(parsed) == []
