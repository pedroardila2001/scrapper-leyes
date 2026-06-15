"""Tests del parser de citas a IDs de grafo (Neo4jExporter._citation_to_node_id).

Cubre el formato real de jurisprudencia de SUIN, donde el código de la sentencia
no va pegado a la palabra "Sentencia" (regresión que dejaba sin aristas de
jurisprudencia al grafo).
"""

from __future__ import annotations

import pytest

neo4j = pytest.importorskip("neo4j")  # el módulo importa neo4j al cargar

from scrapper_leyes.export_neo4j import Neo4jExporter

_cid = Neo4jExporter._citation_to_node_id


def test_sentencia_codigo_no_pegado():
    # Formato real de SUIN: el código va tras "Corte Constitucional".
    out = _cid("Sentencia de la Corte Constitucional  C-623 de 2007")
    assert out is not None
    node_id, nombre = out
    assert node_id == "co:sentencia:cc:plena:c-623:2007"
    assert nombre == "Sentencia C-623 de 2007"


def test_sentencia_tutela_y_su():
    assert _cid("Sentencia T-760 de 2008")[0] == "co:sentencia:cc:revision:t-760:2008"
    assert _cid("Sentencia SU-111 de 1997")[0] == "co:sentencia:cc:plena:su-111:1997"


def test_ley_y_decreto():
    assert _cid("Ley 1164 de 2007")[0] == "co:ley:1164:2007"
    assert _cid("Decreto 1281 de 2002")[0] == "co:decreto:1281:2002"
    assert _cid("Artículo 276 LEY 1450 de 2011")[0] == "co:ley:1450:2011"


def test_no_match():
    assert _cid("texto sin referencia") is None
