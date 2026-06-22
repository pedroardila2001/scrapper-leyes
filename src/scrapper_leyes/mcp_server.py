"""Servidor MCP del knowledge legal colombiano.

Expone las 3 tools de :mod:`scrapper_leyes.tools` por el protocolo **MCP**, para
que clientes como Claude las descubran y llamen nativamente. Las MISMAS funciones
se exponen también por HTTP/OpenAPI (rutas ``/api/tools/*``).

Transportes:
  * ``stdio``           — para clientes locales (Claude Desktop, IDEs).
  * ``sse`` / ``streamable-http`` — para acceso remoto desde el VPS (detrás de
    reverse proxy + TLS + API key).

Arranque: ``scrapper-leyes mcp --transport sse --host 0.0.0.0 --port 8765``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_server():
    """Construye el FastMCP con las 3 tools (import perezoso de `mcp`)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "Falta el paquete 'mcp'. Instala con: pip install 'mcp' "
            "(o reconstruye la imagen; ya está en dependencies)."
        ) from e

    from scrapper_leyes import tools

    server = FastMCP(
        name="scrapper-leyes-legal",
        instructions=(
            "Knowledge del ordenamiento jurídico colombiano (legislación, "
            "jurisprudencia, doctrina administrativa, supranacional). Usa "
            "`buscar_normas` para encontrar fragmentos relevantes, "
            "`texto_vigente` para saber si una norma/artículo está vigente a una "
            "fecha (NUNCA cites texto derogado o norma inexequible), y "
            "`consulta_grafo` para trazar citas, modificaciones y afectaciones. "
            "Cita siempre por canonical_id."
        ),
    )

    @server.tool()
    def buscar_normas(
        query: str,
        limit: int = 10,
        tipo: str | None = None,
        anio: str | None = None,
        estado_vigencia: str | None = None,
        excluir_derogadas: bool = False,
    ) -> dict:
        """Búsqueda híbrida (semántica + léxica) sobre el corpus legal colombiano.

        Devuelve fragmentos con canonical_id y estado_vigencia para citar con
        precisión. Filtra por tipo (LEY/DECRETO/SENTENCIA…), año, estado de
        vigencia, o excluyendo derogadas.
        """
        return tools.buscar_normas(
            query, limit=limit, tipo=tipo, anio=anio,
            estado_vigencia=estado_vigencia, excluir_derogadas=excluir_derogadas,
        )

    @server.tool()
    def texto_vigente(
        canonical_id: str,
        fecha: str | None = None,
        articulo: str | None = None,
    ) -> dict:
        """Estado de vigencia y texto operante de una norma/artículo a una fecha.

        canonical_id: id co:… de la norma. articulo: nº normalizado (opcional).
        fecha: DD/MM/YYYY o YYYY-MM-DD (opcional = hoy). Resuelve desde el grafo
        de afectaciones con respaldo a parsed.json.
        """
        return tools.texto_vigente(canonical_id, fecha=fecha, articulo=articulo)

    @server.tool()
    def consulta_grafo(
        canonical_id: str,
        relacion: str | None = None,
        direccion: str = "ambas",
        limit: int = 50,
    ) -> dict:
        """Relaciones de una norma/sentencia en el grafo de conocimiento.

        Agrupa por tipo (CITA_A, MODIFICA, DEROGA, DECLARA_*, SIMILAR_A,
        PERTENECE_A…). relacion: filtra a un tipo. direccion: salientes |
        entrantes | ambas.
        """
        return tools.consulta_grafo(
            canonical_id, relacion=relacion, direccion=direccion, limit=limit,
        )

    @server.tool()
    def estadistica_jurisprudencial(
        corte: str | None = None,
        materia: str | None = None,
        magistrado: str | None = None,
        anio_desde: int | None = None,
        anio_hasta: int | None = None,
        tipo: str | None = "SENTENCIA",
        top: int = 15,
    ) -> dict:
        """Jurimetría: distribuciones del corpus (por corte, año, materia,
        magistrado) y sentido del fallo (inexequible/exequible/condicionada).

        Es DESCRIPTIVA: devuelve N y nota metodológica. No infieras
        probabilidades de fallo sin contexto (sesgo de selección, N pequeño).
        """
        return tools.estadistica_jurisprudencial(
            corte=corte, materia=materia, magistrado=magistrado,
            anio_desde=anio_desde, anio_hasta=anio_hasta, tipo=tipo, top=top,
        )

    return server


def run(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8765) -> None:
    """Arranca el servidor MCP en el transporte indicado."""
    server = build_server()
    # FastMCP toma host/port de sus settings para sse/streamable-http.
    server.settings.host = host
    server.settings.port = port
    logger.info("MCP server '%s' arrancando (transport=%s, %s:%s)",
                server.name, transport, host, port)
    server.run(transport=transport)
