"""Tools de acceso al knowledge legal para LLMs — fuente ÚNICA.

Las mismas 3 funciones se exponen por dos vías (decisión del usuario: "ambos"):
  * **MCP** (``scrapper_leyes.mcp_server``) → clientes MCP (Claude, etc.).
  * **HTTP/OpenAPI** (rutas ``/api/tools/*`` en la API) → function-calling de
    cualquier LLM.

Cada función devuelve un dict JSON-serializable, con ``error`` legible en vez de
lanzar, para que el LLM reciba algo accionable. Reusa los cores ya construidos:
``search.SemanticSearcher``, ``vigencia_graph.resolve_graph`` y el grafo Neo4j.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from scrapper_leyes.config import Settings

logger = logging.getLogger(__name__)


# ── Contexto perezoso (compartido entre llamadas) ────────────────────────────
@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _driver():
    from neo4j import GraphDatabase

    s = _settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))


@lru_cache(maxsize=1)
def _db():
    from scrapper_leyes.storage.database import Database

    return Database(_settings().catalog_db_path)


@lru_cache(maxsize=1)
def _cache():
    from scrapper_leyes.storage.cache import ProvenanceCache

    return ProvenanceCache(_settings())


# ── Tool 1: búsqueda semántica ───────────────────────────────────────────────
def buscar_normas(
    query: str,
    *,
    limit: int = 10,
    tipo: str | None = None,
    anio: str | None = None,
    estado_vigencia: str | None = None,
    excluir_derogadas: bool = False,
) -> dict[str, Any]:
    """Búsqueda híbrida (densa+léxica) sobre el corpus legal colombiano.

    Devuelve fragmentos relevantes con ``canonical_id`` y ``estado_vigencia``
    para que el agente cite con precisión y NO use norma derogada. Filtros:
    ``tipo`` (LEY/DECRETO/SENTENCIA…), ``anio``, ``estado_vigencia``,
    ``excluir_derogadas``.
    """
    from scrapper_leyes.search import CollectionMissing, get_searcher

    try:
        hits = get_searcher().search(
            query, limit=limit, tipo=tipo, anio=anio,
            estado_vigencia=estado_vigencia, excluir_derogadas=excluir_derogadas,
        )
    except CollectionMissing as e:
        return {"query": query, "error": str(e), "total": 0, "resultados": []}
    except Exception as e:  # noqa: BLE001 — devolver error legible al LLM
        logger.exception("buscar_normas falló")
        return {"query": query, "error": f"búsqueda falló: {e}", "total": 0, "resultados": []}
    return {"query": query, "total": len(hits), "resultados": [h.to_dict() for h in hits]}


# ── Tool 2: texto vigente a una fecha ────────────────────────────────────────
def texto_vigente(
    canonical_id: str,
    *,
    fecha: str | None = None,
    articulo: str | None = None,
) -> dict[str, Any]:
    """Estado de vigencia y texto operante de una norma/artículo a una fecha.

    ``canonical_id``: id co:… de la norma. ``articulo``: número normalizado
    (p.ej. '5', 'trans:1'); si se omite, resuelve la norma completa. ``fecha``:
    'DD/MM/YYYY' o 'YYYY-MM-DD'; si se omite, estado/texto actual. Resuelve desde
    el GRAFO (afectaciones cross-documento) con respaldo a parsed.json.
    """
    from scrapper_leyes.models import build_canonical_id
    from scrapper_leyes.storage.database import source_for
    from scrapper_leyes.vigencia import resolve
    from scrapper_leyes.vigencia_graph import resolve_graph

    # `buscar_normas` devuelve ids a nivel de ARTÍCULO (…:art:N). Si llega uno,
    # se deriva la norma + el artículo, para que el encadenamiento sea transparente.
    norm_cid = canonical_id
    art = articulo
    if ":art:" in canonical_id:
        norm_cid, _, art_suffix = canonical_id.partition(":art:")
        art = articulo or art_suffix

    db = _db()
    row = db.conn.execute(
        "SELECT * FROM catalog WHERE canonical_id = ?", (norm_cid,)
    ).fetchone()
    if not row:
        return {"canonical_id": canonical_id, "error": "norma no encontrada en el catálogo"}
    rd = dict(row)
    tipo, numero, anio = rd.get("tipo", ""), str(rd.get("numero", "")), str(rd.get("anio", ""))
    norm_vig = rd.get("suin_vigencia") or rd.get("vigencia")
    art_cid = build_canonical_id(tipo, numero, anio, art=art) if art else None

    try:
        report = resolve_graph(
            _driver(), norm_cid=norm_cid, art_cid=art_cid,
            norm_vigencia=norm_vig, fecha=fecha,
        )
        fuente = "grafo"
        if report is None:
            suin_id = rd.get("suin_id")
            parsed = _cache().load_parsed(source_for(tipo, rd.get("corte")), tipo, suin_id) if suin_id else None
            if not parsed:
                return {"canonical_id": canonical_id,
                        "error": "sin datos para resolver (norma no exportada ni con texto)"}
            report = resolve(parsed, rd, art_ref=art, fecha=fecha)
            fuente = "parsed_json"
        if report is None:
            return {"canonical_id": canonical_id,
                    "error": f"artículo '{art}' no encontrado en la norma"}
    except Exception as e:  # noqa: BLE001
        logger.exception("texto_vigente falló")
        return {"canonical_id": canonical_id, "error": f"resolución falló: {e}"}

    out = report.to_dict()
    out["fuente_resolucion"] = fuente
    return out


# ── Tool 3: consulta al grafo de conocimiento ────────────────────────────────
# Relaciones que el LLM puede pedir (None = todas).
_REL_TYPES = (
    "CITA_A", "MODIFICA", "DEROGA", "PERTENECE_A", "SIMILAR_A",
    "DECLARA_INEXEQUIBLE", "DECLARA_EXEQUIBLE", "EXEQUIBLE_CONDICIONADA",
    "FUE_PONENTE_DE",
)


def consulta_grafo(
    canonical_id: str,
    *,
    relacion: str | None = None,
    direccion: str = "ambas",
    limit: int = 50,
) -> dict[str, Any]:
    """Vecindario de un nodo en el grafo de conocimiento legal.

    Devuelve las relaciones de la norma/sentencia ``canonical_id`` agrupadas por
    tipo (CITA_A, MODIFICA, DEROGA, DECLARA_*, SIMILAR_A, PERTENECE_A…). Útil
    para trazar qué cita una norma, qué la afecta, o qué se le parece.
    ``relacion``: filtra a un tipo. ``direccion``: 'salientes' | 'entrantes' | 'ambas'.
    """
    if relacion and relacion.upper() not in _REL_TYPES:
        return {"canonical_id": canonical_id,
                "error": f"relación desconocida '{relacion}'. Opciones: {', '.join(_REL_TYPES)}"}
    rel_filter = f":`{relacion.upper()}`" if relacion else ""

    queries = []
    if direccion in ("salientes", "ambas"):
        queries.append(("saliente",
            f"MATCH (n {{id:$cid}})-[r{rel_filter}]->(m) "
            f"RETURN type(r) AS rel, m.id AS nid, "
            f"coalesce(m.nombre, m.numero, m.id) AS name, labels(m) AS labels LIMIT $lim"))
    if direccion in ("entrantes", "ambas"):
        queries.append(("entrante",
            f"MATCH (n {{id:$cid}})<-[r{rel_filter}]-(m) "
            f"RETURN type(r) AS rel, m.id AS nid, "
            f"coalesce(m.nombre, m.numero, m.id) AS name, labels(m) AS labels LIMIT $lim"))

    try:
        with _driver().session() as s:
            exists = s.run("MATCH (n {id:$cid}) RETURN coalesce(n.nombre, n.id) AS name, "
                           "labels(n) AS labels LIMIT 1", cid=canonical_id).single()
            if not exists:
                return {"canonical_id": canonical_id,
                        "error": "nodo no encontrado en el grafo (¿norma no exportada aún?)"}
            relaciones: dict[str, list[dict[str, Any]]] = {}
            total = 0
            for dir_label, q in queries:
                for rec in s.run(q, cid=canonical_id, lim=limit):
                    rel = rec["rel"]
                    relaciones.setdefault(rel, []).append({
                        "canonical_id": rec["nid"],
                        "nombre": rec["name"],
                        "tipo_nodo": next((l for l in rec["labels"] if l != "UNIQUE IMPORT LABEL"), None),
                        "direccion": dir_label,
                    })
                    total += 1
    except Exception as e:  # noqa: BLE001
        logger.exception("consulta_grafo falló")
        return {"canonical_id": canonical_id, "error": f"consulta al grafo falló: {e}"}

    return {
        "canonical_id": canonical_id,
        "nombre": exists["name"],
        "tipo_nodo": next((l for l in exists["labels"] if l != "UNIQUE IMPORT LABEL"), None),
        "total_relaciones": total,
        "relaciones": relaciones,
    }


# ── Tool 4: jurimetría (estadística jurisprudencial) ─────────────────────────
# Tipos de fallo (aristas de resultado en el grafo, del parser del 'resuelve').
_SENTIDO_FALLO = (
    "DECLARA_INEXEQUIBLE", "DECLARA_EXEQUIBLE", "DECLARA_EXEQUIBLE_CONDICIONADA",
    "INEXEQUIBLE", "EXEQUIBLE", "EXEQUIBLE_CONDICIONADA",
)


def estadistica_jurisprudencial(
    *,
    corte: str | None = None,
    materia: str | None = None,
    magistrado: str | None = None,
    anio_desde: int | None = None,
    anio_hasta: int | None = None,
    tipo: str | None = "SENTENCIA",
    top: int = 15,
) -> dict[str, Any]:
    """Jurimetría: distribuciones agregadas sobre el corpus jurisprudencial.

    Cuenta por **corte**, **año**, **materia** y **magistrado** (del catálogo) y
    el **sentido del fallo** (aristas DECLARA_* del grafo, del parser del
    'resuelve'). Filtros opcionales. Devuelve siempre el N y una nota
    metodológica: la jurimetría tiene sesgo de selección (no todo se publica),
    N puede ser pequeño y hay confusores → NO inferir 'probabilidades' sin
    contexto. Pensada para describir el corpus, no para predecir.
    """
    db = _db()
    where = ["1=1"]
    params: list[Any] = []
    if tipo:
        where.append("tipo = ?"); params.append(tipo)
    if corte:
        where.append("corte = ?"); params.append(corte)
    if materia:
        where.append("materia LIKE ?"); params.append(f"%{materia}%")
    if magistrado:
        where.append("magistrado_ponente LIKE ?"); params.append(f"%{magistrado}%")
    if anio_desde is not None:
        where.append("CAST(anio AS INTEGER) >= ?"); params.append(int(anio_desde))
    if anio_hasta is not None:
        where.append("CAST(anio AS INTEGER) <= ?"); params.append(int(anio_hasta))
    w = " AND ".join(where)

    try:
        total = db.conn.execute(f"SELECT COUNT(*) FROM catalog WHERE {w}", params).fetchone()[0]
        con_texto = db.conn.execute(
            f"SELECT COUNT(*) FROM catalog WHERE {w} AND scrape_status='done'", params
        ).fetchone()[0]

        def grupo(col: str, order_by_value: bool = False) -> list[dict[str, Any]]:
            order = f"{col} DESC" if order_by_value else "n DESC"
            rows = db.conn.execute(
                f"SELECT {col} AS valor, COUNT(*) AS n FROM catalog "
                f"WHERE {w} AND {col} IS NOT NULL AND {col} != '' "
                f"GROUP BY {col} ORDER BY {order} LIMIT ?",
                params + [top],
            ).fetchall()
            return [{"valor": r["valor"], "n": r["n"]} for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.exception("estadistica_jurisprudencial (catálogo) falló")
        return {"error": f"agregación falló: {e}"}

    # Sentido del fallo desde el grafo (solo docs con 'resuelve' parseado).
    sentido: list[dict[str, Any]] = []
    cobertura_fallo = 0
    try:
        with _driver().session() as s:
            for rec in s.run(
                "MATCH ()-[d]->() WHERE type(d) IN $tipos "
                "RETURN type(d) AS sentido, count(d) AS n ORDER BY n DESC",
                tipos=list(_SENTIDO_FALLO),
            ):
                sentido.append({"sentido": rec["sentido"], "n": rec["n"]})
            cobertura_fallo = sum(x["n"] for x in sentido)
    except Exception as e:  # noqa: BLE001
        logger.warning("sentido del fallo (grafo) no disponible: %s", e)

    return {
        "filtros": {"corte": corte, "materia": materia, "magistrado": magistrado,
                    "anio_desde": anio_desde, "anio_hasta": anio_hasta, "tipo": tipo},
        "total_catalogadas": total,
        "con_texto_ingerido": con_texto,
        "por_corte": grupo("corte"),
        "por_anio": grupo("anio", order_by_value=True),
        "por_materia": grupo("materia"),
        "por_magistrado": grupo("magistrado_ponente"),
        "sentido_del_fallo": sentido,
        "cobertura": {
            "fallos_tipificados": cobertura_fallo,
            "nota": "El sentido del fallo solo cubre documentos con 'resuelve' "
                    "parseado y exportado al grafo (hoy, control constitucional).",
        },
        "nota_metodologica": (
            f"N={total} catalogadas ({con_texto} con texto). Jurimetría DESCRIPTIVA: "
            "hay sesgo de selección (no todo se publica/cataloga), el N por celda "
            "puede ser pequeño y existen confusores. No inferir probabilidades de "
            "fallo sin análisis causal. El magistrado depende de que el seed/parse "
            "lo haya capturado."
        ),
    }


# Catálogo de tools (descripción para MCP/OpenAPI).
TOOLS = (buscar_normas, texto_vigente, consulta_grafo, estadistica_jurisprudencial)
