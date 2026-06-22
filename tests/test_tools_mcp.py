"""Tests de las tools para LLMs y del servidor MCP (sin red a los almacenes)."""

from __future__ import annotations

import pytest

from scrapper_leyes import tools


def test_tools_registradas():
    assert len(tools.TOOLS) == 4
    assert all(callable(t) for t in tools.TOOLS)


def test_consulta_grafo_rechaza_relacion_invalida():
    # Validación pura: retorna error ANTES de tocar Neo4j.
    out = tools.consulta_grafo("co:ley:1:2020", relacion="INVENTADA")
    assert "error" in out
    assert "INVENTADA" in out["error"]


def test_mcp_server_registra_tres_tools():
    pytest.importorskip("mcp")
    import asyncio

    from scrapper_leyes.mcp_server import build_server

    srv = build_server()
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert names == {"buscar_normas", "texto_vigente", "consulta_grafo",
                     "estadistica_jurisprudencial"}
