"""FastAPI backend for the Legal AI Dashboard.

Serves catalog data from SQLite, parsed documents from the file cache,
vector chunk previews, and graph neighborhood data.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase
from pydantic import BaseModel, Field

# ── App setup ────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cerebro Legal Colombia API",
    description="API para el sistema de IA jurídica colombiana – Bodega Legal",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "catalog.db"
RAW_DIR = DATA_DIR / "raw"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD") or "password"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
NEO4J_CONTAINER = os.environ.get("NEO4J_CONTAINER", "legal_neo4j")

# Caché en memoria para el recuento de vacíos del endpoint /monitor (ver abajo).
# Los vacíos no cambian segundo a segundo, así que se refresca cada 5 min.
_MONITOR_CALIDAD_CACHE: dict[str, Any] = {}

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _source_for_tipo(tipo: str, corte: str | None = None) -> str:
    """Determine the cache source directory based on the norm type and corte."""
    if tipo == "SENTENCIA":
        if corte == "csj":
            return "csj"
        elif corte == "ce":
            return "consejo_estado"
        return "corte_constitucional"
    return "suin"


def _find_parsed(suin_id: str, tipo: str, corte: str | None = None) -> dict[str, Any] | None:
    """Locate and load parsed.json for a given norm."""
    source = _source_for_tipo(tipo, corte)
    path = RAW_DIR / source / tipo / suin_id / "parsed.json"
    if not path.exists():
        # Try other sources as fallback
        for alt_source in ["suin", "corte_constitucional", "csj", "consejo_estado"]:
            alt_path = RAW_DIR / alt_source / tipo / suin_id / "parsed.json"
            if alt_path.exists():
                return json.loads(alt_path.read_text(encoding="utf-8"))
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "Cerebro Legal API",
        "db_exists": DB_PATH.exists(),
        "data_dir": str(DATA_DIR),
    }


# ── Stats ────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    """Dashboard aggregate statistics."""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        by_tipo = [
            dict(r)
            for r in conn.execute(
                "SELECT tipo, COUNT(*) as count FROM catalog GROUP BY tipo ORDER BY count DESC"
            ).fetchall()
        ]
        by_scrape = [
            dict(r)
            for r in conn.execute(
                "SELECT scrape_status, COUNT(*) as count FROM catalog GROUP BY scrape_status"
            ).fetchall()
        ]
        by_resolve = [
            dict(r)
            for r in conn.execute(
                "SELECT resolve_status, COUNT(*) as count FROM catalog GROUP BY resolve_status"
            ).fetchall()
        ]
        sentencias = conn.execute(
            "SELECT COUNT(*) FROM catalog WHERE tipo='SENTENCIA'"
        ).fetchone()[0]
        leyes = conn.execute(
            "SELECT COUNT(*) FROM catalog WHERE tipo='LEY'"
        ).fetchone()[0]
        decretos = conn.execute(
            "SELECT COUNT(*) FROM catalog WHERE tipo='DECRETO'"
        ).fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM catalog WHERE scrape_status='done'"
        ).fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM catalog WHERE scrape_status='error'"
        ).fetchone()[0]

        # Unmapped affectations count
        unmapped = 0
        try:
            unmapped = conn.execute(
                "SELECT COUNT(*) FROM unmapped_affectations"
            ).fetchone()[0]
        except Exception:
            pass

        # Vigencia discrepancies
        discrepancies = 0
        try:
            discrepancies = conn.execute(
                "SELECT COUNT(*) FROM vigencia_discrepancies"
            ).fetchone()[0]
        except Exception:
            pass

        return {
            "total_norms": total,
            "leyes": leyes,
            "decretos": decretos,
            "sentencias": sentencias,
            "scraped_done": done,
            "scraped_errors": errors,
            "unmapped_affectations": unmapped,
            "vigencia_discrepancies": discrepancies,
            "by_tipo": by_tipo,
            "by_scrape_status": by_scrape,
            "by_resolve_status": by_resolve,
        }
    finally:
        conn.close()


# ── Catalog ──────────────────────────────────────────────────────────────

@app.get("/api/catalog")
def get_catalog(
    tipo: Optional[str] = None,
    search: Optional[str] = None,
    scrape_status: Optional[str] = None,
    vigencia: Optional[str] = None,
    rama: Optional[str] = None,
    cabeza: Optional[str] = None,
    entidad_norm: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
):
    """Paginated catalog with filtering (incl. biblioteca entity filters)."""
    conn = _get_conn()
    try:
        where_clauses = ["1=1"]
        params: list[Any] = []

        if tipo:
            where_clauses.append("tipo = ?")
            params.append(tipo)
        if scrape_status:
            where_clauses.append("scrape_status = ?")
            params.append(scrape_status)
        if vigencia:
            where_clauses.append("vigencia LIKE ?")
            params.append(f"%{vigencia}%")
        if rama:
            where_clauses.append("rama = ?")
            params.append(rama)
        if cabeza:
            where_clauses.append("cabeza = ?")
            params.append(cabeza)
        if entidad_norm:
            where_clauses.append("entidad_norm = ?")
            params.append(entidad_norm)
        if search:
            where_clauses.append(
                "(numero LIKE ? OR anio LIKE ? OR entidad LIKE ? OR suin_id LIKE ? OR materia LIKE ?)"
            )
            s = f"%{search}%"
            params.extend([s, s, s, s, s])

        where = " AND ".join(where_clauses)

        total = conn.execute(
            f"SELECT COUNT(*) FROM catalog WHERE {where}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT * FROM catalog WHERE {where} ORDER BY anio DESC, numero DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# ── Catalog Types (for filter dropdown) ──────────────────────────────────

@app.get("/api/catalog/types")
def get_catalog_types():
    """Return all distinct types for filter dropdowns."""
    conn = _get_conn()
    try:
        tipos = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT tipo FROM catalog ORDER BY tipo"
            ).fetchall()
        ]
        return {"types": tipos}
    finally:
        conn.close()


# ── Biblioteca (entity taxonomy) ──────────────────────────────────────────

@app.get("/api/biblioteca")
def get_biblioteca():
    """Entity taxonomy tree (Rama → cabeza → entidad) with document counts.

    Built from the catalog so it reflects exactly what we have ingested.
    """
    from scrapper_leyes.taxonomia import build_library_tree

    conn = _get_conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT tipo, sector, entidad, corte FROM catalog"
            ).fetchall()
        ]
        return build_library_tree(rows)
    finally:
        conn.close()


# ── Fuentes / cobertura del sistema legal ─────────────────────────────────

@app.get("/api/sources")
def get_sources():
    """Mapa de fuentes del ordenamiento jurídico colombiano.

    Por cada fuente reporta el volumen disponible para descubrir —distinguiendo
    si está **medido** (conteo contra la fuente real) o **estimado** (cifra de
    spike/muestreo cuando el discoverer existe pero no se ha corrido el conteo
    completo)— y lo ya **ingerido** en el catálogo. Así el dashboard refleja el
    universo COMPLETO mapeado, no solo lo medido.
    """
    from scrapper_leyes.sources import (
        CAPA_LABEL, EST_PENDIENTE, all_sources, volumen_total_de,
    )

    # Ingerido (con texto) por fuente, desde el catálogo.
    conn = _get_conn()
    try:
        ingerido = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM catalog WHERE scrape_status='done' GROUP BY source"
            ).fetchall()
        }
    finally:
        conn.close()

    total_medido = total_estimado = 0
    capas: dict[str, dict[str, Any]] = {}
    for s in all_sources():
        vol, calidad = volumen_total_de(s.key)
        # Todo lo que no esté 'pendiente' tiene un discoverer/conector cableado.
        tiene_conector = s.estado != EST_PENDIENTE
        node = capas.setdefault(
            s.capa, {"capa": s.capa, "label": CAPA_LABEL.get(s.capa, s.capa),
                     "fuentes": [], "volumen": 0}
        )
        node["fuentes"].append({
            "key": s.key, "nombre": s.nombre, "modo": s.modo, "estado": s.estado,
            "prioridad": s.prioridad, "volumen_disponible": vol,
            "volumen_calidad": calidad, "tiene_conector": tiene_conector,
            "ingerido": ingerido.get(s.key, 0),
        })
        node["volumen"] += vol or 0
        if calidad == "medido":
            total_medido += vol or 0
        elif calidad == "estimado":
            total_estimado += vol or 0

    srcs = all_sources()
    return {
        "capas": sorted(capas.values(), key=lambda c: c["capa"]),
        "total_medido": total_medido,
        "total_estimado": total_estimado,
        "total_disponible": total_medido + total_estimado,  # universo mapeado completo
        "total_ingerido": sum(ingerido.values()),
        "total_fuentes": len(srcs),
        "fuentes_con_conector": sum(1 for s in srcs if s.estado != EST_PENDIENTE),
        "fuentes_operativas": sum(1 for s in srcs if s.implementado),
    }


# ── Document text ────────────────────────────────────────────────────────

@app.get("/api/norms/{suin_id}/text")
def get_norm_text(suin_id: str):
    """Return the full parsed document (articles, sections, metadata)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM catalog WHERE suin_id = ?", (suin_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Norm not found in catalog")

        row_dict = dict(row)
        parsed = _find_parsed(suin_id, row["tipo"], row_dict.get("corte"))
        if not parsed:
            raise HTTPException(404, "Parsed text not available for this norm")

        # Enrich with catalog metadata
        parsed["_catalog"] = dict(row)
        return parsed
    finally:
        conn.close()


# ── Vectors / Chunks ────────────────────────────────────────────────────

@app.get("/api/norms/{suin_id}/vectors")
def get_norm_vectors(suin_id: str):
    """Return text chunks that would be / are stored in the vector DB.

    We build them from the parsed data so the user can see exactly
    what gets embedded.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM catalog WHERE suin_id = ?", (suin_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Norm not found")
        row_dict = dict(row)

        parsed = _find_parsed(suin_id, row["tipo"], row_dict.get("corte"))
        if not parsed:
            raise HTTPException(404, "No parsed data")

        # Use the SAME chunker the vector exporter uses, so the dashboard shows
        # exactly what the deep-agent retrieves (coherent chunks + vigencia).
        from scrapper_leyes.chunking import chunk_document

        produced = chunk_document(parsed, row_dict)
        chunks = [c.to_api_dict(i + 1) for i, c in enumerate(produced)]

        # Citations as metadata
        citaciones = parsed.get("citaciones", [])

        return {
            "suin_id": suin_id,
            "total_chunks": len(chunks),
            "chunks": chunks,
            "citaciones": citaciones,
        }
    finally:
        conn.close()


# ── Vigencia temporal ─────────────────────────────────────────────────────

@app.get("/api/norms/{suin_id}/vigencia")
def get_norm_vigencia(
    suin_id: str,
    art: Optional[str] = None,
    fecha: Optional[str] = None,
):
    """Resuelve el estado de vigencia de una norma o artículo a una fecha.

    - ``art``: número normalizado de artículo (p.ej. '5', '5a', 'trans:1'). Si
      se omite, resuelve la norma completa.
    - ``fecha``: 'DD/MM/YYYY' o 'YYYY-MM-DD'. Si se omite, estado/texto actual.
    """
    from scrapper_leyes.models import build_canonical_id
    from scrapper_leyes.vigencia import resolve
    from scrapper_leyes.vigencia_graph import resolve_graph

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM catalog WHERE suin_id = ?", (suin_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Norm not found")
        row_dict = dict(row)
        tipo = row_dict.get("tipo", "")
        numero = str(row_dict.get("numero", ""))
        anio = str(row_dict.get("anio", ""))
        norm_vig = row_dict.get("suin_vigencia") or row_dict.get("vigencia")
        norm_cid = row_dict.get("canonical_id") or build_canonical_id(tipo, numero, anio)
        art_cid = build_canonical_id(tipo, numero, anio, art=art) if art else None

        # 1) Fuente única de verdad: el grafo (afectaciones entrantes cross-doc).
        report = resolve_graph(
            neo4j_driver, norm_cid=norm_cid, art_cid=art_cid,
            norm_vigencia=norm_vig, fecha=fecha,
        )
        # 2) Fallback al resolver basado en parsed.json si el nodo no está en el
        #    grafo todavía (p.ej. norma no exportada aún).
        if report is None:
            parsed = _find_parsed(suin_id, tipo, row_dict.get("corte"))
            if not parsed:
                raise HTTPException(404, "No parsed data")
            report = resolve(parsed, row_dict, art_ref=art, fecha=fecha)
            if report is None:
                raise HTTPException(404, f"Artículo {art} no encontrado en la norma")
            out = report.to_dict()
            out["fuente_resolucion"] = "parsed_json"
            return out

        out = report.to_dict()
        out["fuente_resolucion"] = "grafo"
        return out
    finally:
        conn.close()


# ── Knowledge Graph ─────────────────────────────────────────────────────

@app.get("/api/norms/{suin_id}/graph")
def get_norm_graph(suin_id: str):
    """Build the local knowledge-graph neighbourhood of a norm.

    Nodes: the norm itself, its articles, cited norms, sentencias that
    affect it, and the magistrado ponente.
    Links: PERTENECE_A, CITA_A, DECLARA_*, FUE_PONENTE_DE.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM catalog WHERE suin_id = ?", (suin_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Norm not found")

        nodes: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        tipo = row["tipo"]
        norm_label = f"{tipo} {row['numero']} de {row['anio']}"

        # Central node
        nodes.append({
            "id": suin_id,
            "name": norm_label,
            "group": "norma",
            "val": 10,
        })
        seen_ids.add(suin_id)

        row_dict = dict(row)
        parsed = _find_parsed(suin_id, tipo, row_dict.get("corte"))

        if parsed:
            # Articles / TOC Hierarchy
            if tipo == "SENTENCIA":
                for sec in ["hechos", "consideraciones", "resuelve"]:
                    if parsed.get(sec):
                        sid = f"{suin_id}#{sec}"
                        nodes.append({
                            "id": sid,
                            "name": sec.replace("_", " ").title(),
                            "group": "seccion",
                            "val": 5,
                        })
                        seen_ids.add(sid)
                        links.append({"source": sid, "target": suin_id, "label": "CONTIENE"})
            else:
                toc = parsed.get("toc", [])
                if toc:
                    parent_stack = [(suin_id, "norma")]
                    for item in toc:
                        level = item.get("level", "articulo")
                        text = item.get("text", "")
                        anchor = item.get("anchor", "")
                        
                        if level == "division":
                            group = "titulo" if "titulo" in text.lower() else "capitulo"
                        else:
                            group = "articulo"
                            
                        nid = f"{suin_id}#{anchor}" if anchor else f"{suin_id}#{text.replace(' ', '_')}"
                        if nid not in seen_ids:
                            nodes.append({
                                "id": nid,
                                "name": text[:50],
                                "group": group,
                                "val": 6 if level == "division" else 3,
                            })
                            seen_ids.add(nid)
                            
                        if level == "division":
                            if "titulo" in text.lower():
                                parent_stack = [(suin_id, "norma"), (nid, group)]
                                links.append({"source": nid, "target": suin_id, "label": "CONTIENE"})
                            else:
                                if len(parent_stack) > 1 and parent_stack[-1][1] == "titulo":
                                    links.append({"source": nid, "target": parent_stack[-1][0], "label": "CONTIENE"})
                                    parent_stack = [(suin_id, "norma"), parent_stack[-1], (nid, group)]
                                else:
                                    links.append({"source": nid, "target": suin_id, "label": "CONTIENE"})
                                    parent_stack = [(suin_id, "norma"), (nid, group)]
                        else:
                            parent_id = parent_stack[-1][0]
                            links.append({"source": nid, "target": parent_id, "label": "CONTIENE"})
                else:
                    for art in parsed.get("articles", []):
                        aid = art.get(
                            "canonical_id",
                            f"{suin_id}#art_{art.get('number', '?')}",
                        )
                        if aid not in seen_ids:
                            nodes.append({
                                "id": aid,
                                "name": f"Art. {art.get('number', '?')}",
                                "group": "articulo",
                                "val": 3,
                            })
                            seen_ids.add(aid)
                        links.append({"source": aid, "target": suin_id, "label": "PERTENECE_A"})

            # Modifications (norms that modify this one — INCOMING)
            for mod in parsed.get("modifications", []):
                src_text = mod.get("source_text", "")
                src_id = mod.get("source_suin_id", src_text[:30])
                if src_id and src_id not in seen_ids:
                    nodes.append({
                        "id": src_id,
                        "name": src_text[:50],
                        "group": "modificacion",
                        "val": 5,
                    })
                    seen_ids.add(src_id)
                if src_id:
                    links.append({
                        "source": src_id,
                        "target": suin_id,
                        "label": mod.get("normalized_type", "MODIFICA"),
                    })

            # Outgoing affectations (what THIS norm derogates/modifies of others)
            seen_affects: set[str] = set()
            for art in parsed.get("articles", []):
                for aff in art.get("affects", []):
                    tgt_text = aff.get("target_text", "")
                    tgt_id = aff.get("target_suin_id") or tgt_text[:30]
                    if not tgt_id:
                        continue
                    edge_key = f"{aff.get('normalized_type')}|{tgt_id}"
                    if edge_key in seen_affects:
                        continue
                    seen_affects.add(edge_key)
                    if tgt_id not in seen_ids:
                        nodes.append({
                            "id": tgt_id,
                            "name": tgt_text[:50],
                            "group": "afecta",
                            "val": 5,
                        })
                        seen_ids.add(tgt_id)
                    links.append({
                        "source": suin_id,
                        "target": tgt_id,
                        "label": aff.get("normalized_type", "AFECTA"),
                    })

            # Jurisprudence (sentencias that affect this norm)
            for jur in parsed.get("jurisprudence", []):
                src_text = jur.get("source_text", "")
                src_id = jur.get("source_suin_id", src_text[:30])
                if src_id and src_id not in seen_ids:
                    nodes.append({
                        "id": src_id,
                        "name": src_text[:50],
                        "group": "sentencia",
                        "val": 6,
                    })
                    seen_ids.add(src_id)
                if src_id:
                    links.append({
                        "source": src_id,
                        "target": suin_id,
                        "label": jur.get("normalized_type", "EXEQUIBLE"),
                    })

            # Citations (extracted by NER). Dedupe and cap so heavily-citing
            # sentencias don't render as an unreadable hairball.
            MAX_CITAS = 30
            citas_unicas: list[str] = []
            vistas: set[str] = set()
            for cita in parsed.get("citaciones", []):
                cita_id = cita.strip() if isinstance(cita, str) else str(cita)
                if cita_id and cita_id not in vistas:
                    vistas.add(cita_id)
                    citas_unicas.append(cita_id)

            for cita_id in citas_unicas[:MAX_CITAS]:
                # Distinguish cited sentencias from cited norms for coloring.
                grupo = "sentencia_citada" if "sentencia" in cita_id.lower() else "citacion"
                if cita_id not in seen_ids:
                    nodes.append({"id": cita_id, "name": cita_id, "group": grupo, "val": 4})
                    seen_ids.add(cita_id)
                links.append({"source": suin_id, "target": cita_id, "label": "CITA_A"})

            extra = len(citas_unicas) - MAX_CITAS
            if extra > 0:
                more_id = f"{suin_id}__mas_citas"
                nodes.append({
                    "id": more_id,
                    "name": f"+{extra} citaciones más",
                    "group": "resumen",
                    "val": 6,
                })
                links.append({"source": suin_id, "target": more_id, "label": "CITA_A"})

            # Magistrado Ponente (for sentencias)
            mp = parsed.get("magistrado_ponente")
            if mp and mp.strip():
                mp_clean = mp.strip().replace(":", "").strip()[:60]
                mp_id = f"mag_{mp_clean.replace(' ', '_').lower()[:30]}"
                if mp_id not in seen_ids:
                    nodes.append({
                        "id": mp_id,
                        "name": mp_clean,
                        "group": "magistrado",
                        "val": 5,
                    })
                    seen_ids.add(mp_id)
                links.append({
                    "source": mp_id,
                    "target": suin_id,
                    "label": "FUE_PONENTE_DE",
                })

        return {
            "suin_id": suin_id,
            "nodes": nodes,
            "links": links,
        }
    finally:
        conn.close()


# ── Search across parsed text ────────────────────────────────────────────

@app.get("/api/search")
def search_norms(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, le=100),
    tipo: Optional[str] = None,
    anio: Optional[str] = None,
    estado_vigencia: Optional[str] = None,
    excluir_derogadas: bool = False,
):
    """Búsqueda semántica híbrida (dense+sparse) sobre Qdrant — la herramienta 1
    del deep-agent. Filtrable por tipo/año/vigencia.

    Si la colección de vectores aún no existe (no se ha corrido `export vector`),
    cae con elegancia al keyword search de SQLite para no romper el dashboard.
    """
    from scrapper_leyes.search import CollectionMissing, get_searcher

    try:
        searcher = get_searcher()
        hits = searcher.search(
            q,
            limit=limit,
            tipo=tipo,
            anio=anio,
            estado_vigencia=estado_vigencia,
            excluir_derogadas=excluir_derogadas,
        )
        return {
            "query": q,
            "modo": "semantica",
            "total": len(hits),
            "results": [h.to_dict() for h in hits],
        }
    except CollectionMissing:
        out = _keyword_search(q, limit)
        out["modo"] = "keyword_fallback"
        out["aviso"] = "Índice vectorial vacío; corre `export vector`. Resultados por coincidencia léxica."
        return out
    except Exception as e:  # pragma: no cover - resiliencia del endpoint
        logging.getLogger(__name__).warning("Búsqueda semántica falló (%s); fallback keyword", e)
        out = _keyword_search(q, limit)
        out["modo"] = "keyword_fallback"
        return out


def _keyword_search(q: str, limit: int) -> dict[str, Any]:
    """Fallback léxico sobre el catálogo (el comportamiento previo)."""
    conn = _get_conn()
    try:
        s = f"%{q}%"
        rows = conn.execute(
            "SELECT * FROM catalog WHERE "
            "(numero LIKE ? OR entidad LIKE ? OR materia LIKE ? OR suin_id LIKE ?) "
            "ORDER BY anio DESC LIMIT ?",
            (s, s, s, s, limit),
        ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            parsed = _find_parsed(row["suin_id"], row["tipo"], row.get("corte")) if row["suin_id"] else None
            if parsed:
                raw = parsed.get("raw_text", "") or ""
                idx = raw.lower().find(q.lower())
                if idx >= 0:
                    start = max(0, idx - 100)
                    end = min(len(raw), idx + len(q) + 100)
                    item["snippet"] = f"...{raw[start:end]}..."
            results.append(item)

        return {"query": q, "total": len(results), "results": results}
    finally:
        conn.close()

# ── Global Knowledge Graph ──────────────────────────────────────────────

def _graph_node_dict(node, degree: int = 0) -> dict[str, Any]:
    """Normaliza un nodo Norma/Sentencia para el frontend.

    Incluye nivel_jerarquico y rama_poder para visualización jerárquica.
    """
    es_sentencia = "Sentencia" in node.labels
    ingerido = bool(node.get("suin_id"))
    nivel = node.get("nivel_jerarquico")
    rama = node.get("rama_poder")

    # Si no tiene nivel asignado, inferirlo del tipo
    if nivel is None:
        tipo_raw = (node.get("tipo") or "").upper()
        if "CONSTIT" in tipo_raw or "ACTO LEGISLATIVO" in tipo_raw:
            nivel = 1
        elif "TRATADO" in tipo_raw:
            nivel = 2
        elif "LEY" in tipo_raw:
            nivel = 3
        elif "DECRETO" in tipo_raw:
            nivel = 4
        elif es_sentencia:
            nivel = 6
        elif "ACUERDO" in tipo_raw or "ORDENANZA" in tipo_raw:
            nivel = 7
        else:
            nivel = 5

    if not ingerido:
        group = "fantasma"
        val = min(4 + degree, 22)
    else:
        group = "sentencia" if es_sentencia else "norma"
        val = 10 if es_sentencia else 8

    # El nivel determina el color y posición Y en el grafo
    nivel_colores = {
        1: "#1a237e", 2: "#283593", 3: "#1565c0", 4: "#0277bd",
        5: "#00838f", 6: "#2e7d32", 7: "#558b2f",
    }

    tipo_str = "Sentencia" if es_sentencia else (node.get("tipo") or "Norma")
    label = node.get("nombre") or f"{tipo_str} {node.get('numero', '')} de {node.get('anio', '')}"
    return {
        "id": node.get("id"),
        "suin_id": node.get("suin_id"),
        "name": label.strip(),
        "group": group,
        "ingerido": ingerido,
        "val": val,
        "nivel": nivel,
        "rama": rama or "",
        "color": nivel_colores.get(nivel, "#757575"),
        "tipo": tipo_str,
        "numero": node.get("numero"),
        "anio": node.get("anio"),
    }


@app.get("/api/graph/global")
def get_global_graph(
    limit: int = Query(default=2000, le=8000),
    incluir_fantasmas: bool = True,
):
    """Vista global del grafo de conocimiento desde Neo4j.

    Por defecto incluye nodos *fantasma* (normas/sentencias referenciadas pero no
    ingeridas) para que la red se vea conectada en torno a sus hubs reales (la
    Constitución, códigos, leyes muy citadas). Pasa ``incluir_fantasmas=false``
    para ver solo lo ingerido.
    """
    # Solo Norma/Sentencia (no Articulos, que saturarían). Incluimos aristas a
    # stubs salvo que se pidan ocultar. Excluimos jerarquía/ponencia del grafo.
    ghost_clause = "" if incluir_fantasmas else "AND m.suin_id IS NOT NULL"
    query = f"""
    MATCH (n)
    WHERE (n:Norma OR n:Sentencia) AND n.suin_id IS NOT NULL
    OPTIONAL MATCH (n)-[r]-(m)
    WHERE NOT type(r) IN ['PERTENECE_A', 'FUE_PONENTE_DE']
      AND (m:Norma OR m:Sentencia) {ghost_clause}
    RETURN n, r, m
    LIMIT {limit}
    """

    nodes: list[dict[str, Any]] = []
    links = []
    node_idx: dict[str, int] = {}
    seen_links = set()
    degree: dict[str, int] = {}

    with neo4j_driver.session() as session:
        records = list(session.run(query))

        # Primer paso: grado de cada nodo (para dimensionar fantasmas/hubs).
        for record in records:
            n, m, r = record.get("n"), record.get("m"), record.get("r")
            if r and n and m:
                degree[n.get("id")] = degree.get(n.get("id"), 0) + 1
                degree[m.get("id")] = degree.get(m.get("id"), 0) + 1

        def _add(node):
            nid = node.get("id")
            if nid in node_idx:
                return
            node_idx[nid] = len(nodes)
            nodes.append(_graph_node_dict(node, degree.get(nid, 0)))

        for record in records:
            n, m, r = record.get("n"), record.get("m"), record.get("r")
            if n:
                _add(n)
            if m:
                _add(m)
            if r and n and m:
                pair = "|".join(sorted([str(n.get("id")), str(m.get("id"))]))
                link_id = f"{pair}-{r.type}"
                if link_id not in seen_links:
                    links.append({
                        "source": n.get("id"),
                        "target": m.get("id"),
                        "label": r.type,
                    })
                    seen_links.add(link_id)

    return {
        "nodes": nodes,
        "links": links,
        "stats": {
            "total": len(nodes),
            "ingeridas": sum(1 for x in nodes if x["ingerido"]),
            "fantasmas": sum(1 for x in nodes if not x["ingerido"]),
            "aristas": len(links),
        },
    }


# ── Monitor de ingesta en tiempo real ────────────────────────────────────

# Objetivos estimados del universo documental colombiano (~660k docs).
# Mismas claves `source` que guarda el catálogo. Sirve para mostrar cuánto
# falta por descubrir/ingerir de cada fuente.
_FUENTES_660K: dict[str, dict[str, Any]] = {
    "suin":                 {"objetivo": 89_000,  "nombre": "SUIN – Justicia Ordinaria"},
    "funcion_publica":      {"objetivo": 261_000, "nombre": "Función Pública – Normativa"},
    "regimen_bogota":       {"objetivo": 188_000, "nombre": "Régimen Distrital – Bogotá"},
    "csj":                  {"objetivo": 50_000,  "nombre": "Corte Suprema de Justicia"},
    "consejo_estado":       {"objetivo": 100_000, "nombre": "Consejo de Estado"},
    "corte_constitucional": {"objetivo": 29_000,  "nombre": "Corte Constitucional"},
    "dian":                 {"objetivo": 23_000,  "nombre": "DIAN – Doctrina Tributaria"},
    "jep":                  {"objetivo": 15_000,  "nombre": "JEP – Jurisdicción Especial"},
    "creg":                 {"objetivo": 7_000,   "nombre": "CREG – Regulación"},
    "cra":                  {"objetivo": 4_000,   "nombre": "CRA – Agua"},
    "crc":                  {"objetivo": 3_000,   "nombre": "CRC – Comunicaciones"},
    "tratados":             {"objetivo": 1_000,   "nombre": "Tratados Internacionales"},
    "senado":               {"objetivo": 5_000,   "nombre": "Senado – Proyectos de Ley"},
    "diario_oficial":       {"objetivo": 200_000, "nombre": "Diario Oficial"},
    "organos_control":      {"objetivo": 500,     "nombre": "Órganos de Control"},
    "corte_idh":            {"objetivo": 73,      "nombre": "Corte IDH"},
    "banco_republica":      {"objetivo": 15,      "nombre": "Banco de la República"},
    "cne":                  {"objetivo": 7,       "nombre": "CNE – Consejo Nacional Electoral"},
}


def _qdrant_count(collection: str) -> Optional[int]:
    """Point count exacto de una colección Qdrant; None si no existe/error."""
    try:
        r = requests.post(
            f"{QDRANT_URL}/collections/{collection}/points/count",
            json={"exact": True},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("result", {}).get("count")
        return None
    except Exception:
        return None


def _neo4j_counts() -> dict[str, Any]:
    """Node count por label y relationship count.

    Ruta preferida: el driver de Neo4j ya conectado (rápido, funciona dentro
    del contenedor Docker donde la API corre). Fallback: ``docker exec ... cypher-shell``
    para entornos donde la API corre en el host sin driver.
    """
    out: dict[str, Any] = {"nodes_by_label": {}, "relationships_total": 0, "ok": False}
    # ── Ruta 1: driver de Neo4j ──
    try:
        with neo4j_driver.session() as session:
            labels = session.run(
                "CALL db.labels() YIELD label RETURN collect(label) AS labels"
            ).single()["labels"]
            for lb in labels:
                rec = session.run(f"MATCH (n:`{lb}`) RETURN count(n) AS c").single()
                out["nodes_by_label"][lb] = rec["c"] if rec else 0
            rec = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
            out["relationships_total"] = rec["c"] if rec else 0
            out["ok"] = True
            return out
    except Exception as e:
        logging.getLogger(__name__).debug("Neo4j driver falló (%s); intento docker exec", e)
    # ── Ruta 2: docker exec cypher-shell (fallback host) ──
    try:
        labels = subprocess.run(
            ["docker", "exec", NEO4J_CONTAINER,
             "cypher-shell", "-u", "neo4j", "-p", NEO4J_PASSWORD,
             "CALL db.labels() YIELD label RETURN label", "--format", "plain"],
            capture_output=True, text=True, timeout=10,
        )
        if labels.returncode != 0:
            out["error"] = (labels.stderr or "").strip()[:200]
            return out
        for line in labels.stdout.splitlines():
            label = line.strip().strip('"')
            if not label:
                continue
            cnt = subprocess.run(
                ["docker", "exec", NEO4J_CONTAINER,
                 "cypher-shell", "-u", "neo4j", "-p", NEO4J_PASSWORD,
                 f"MATCH (n:`{label}`) RETURN count(n) AS c", "--format", "plain"],
                capture_output=True, text=True, timeout=10,
            )
            try:
                out["nodes_by_label"][label] = int(cnt.stdout.strip().splitlines()[-1])
            except Exception:
                out["nodes_by_label"][label] = 0
        rels = subprocess.run(
            ["docker", "exec", NEO4J_CONTAINER,
             "cypher-shell", "-u", "neo4j", "-p", NEO4J_PASSWORD,
             "MATCH ()-[r]->() RETURN count(r) AS c", "--format", "plain"],
            capture_output=True, text=True, timeout=10,
        )
        try:
            out["relationships_total"] = int(rels.stdout.strip().splitlines()[-1])
        except Exception:
            out["relationships_total"] = 0
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def _vm_resources() -> dict[str, Any]:
    """Memoria, disco y load average del host.

    Usa ``/proc`` (universal, funciona dentro de contenedores) como ruta
    preferida, con ``free``/``df`` como fallback.
    """
    res: dict[str, Any] = {}
    # RAM — /proc/meminfo (siempre disponible en Linux, incl. dentro de Docker)
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
        if "MemTotal" in info:
            total_kb = info["MemTotal"]
            avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
            used_kb = total_kb - avail_kb
            res["mem_total_mb"] = total_kb // 1024
            res["mem_used_mb"] = used_kb // 1024
            res["mem_available_mb"] = avail_kb // 1024
            res["mem_pct"] = round(used_kb / total_kb * 100, 1) if total_kb else 0
    except Exception:
        pass
    if "mem_total_mb" not in res:
        # Fallback: free -m (host con util-linux)
        try:
            out = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5).stdout
            lines = out.splitlines()
            if len(lines) >= 2:
                parts = lines[1].split()
                total, used, avail = int(parts[1]), int(parts[2]), int(parts[-1])
                res["mem_total_mb"] = total
                res["mem_used_mb"] = used
                res["mem_available_mb"] = avail
                res["mem_pct"] = round(used / total * 100, 1) if total else 0
        except Exception:
            pass
    # Disco (raíz)
    try:
        out = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5).stdout
        lines = out.splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            res["disk_total"] = parts[1]
            res["disk_used"] = parts[2]
            res["disk_avail"] = parts[3]
            res["disk_pct"] = int(parts[4].rstrip("%")) if parts[4].endswith("%") else 0
    except Exception:
        pass
    # Load average — /proc/loadavg (universal)
    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
            res["load_1"] = float(p[0])
            res["load_5"] = float(p[1])
            res["load_15"] = float(p[2])
    except Exception:
        pass
    return res


@app.get("/api/monitor")
def get_monitor():
    """Monitoreo en tiempo real de la ingesta del sistema legal colombiano.

    Agrega en una sola llamada: estado del catálogo por fuente/tipo, progreso y
    ETA, conteos de Qdrant y Neo4j, mapeo 660k (actual vs objetivo), calidad
    del corpus (parsed.json vacíos) y recursos de la VM.
    """
    log = logging.getLogger(__name__)
    conn = _get_conn()
    try:
        # ── Catálogo: totales y desglose por fuente/tipo ──
        total_docs = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        by_source_status = conn.execute(
            "SELECT source, scrape_status, COUNT(*) AS n "
            "FROM catalog GROUP BY source, scrape_status"
        ).fetchall()
        by_tipo_status = conn.execute(
            "SELECT tipo, scrape_status, COUNT(*) AS n "
            "FROM catalog GROUP BY tipo, scrape_status"
        ).fetchall()

        # Aplana a {fuente: {done,pending,error,total}} y análogamente por tipo.
        catalog_by_source: dict[str, dict[str, int]] = {}
        catalog_by_tipo: dict[str, dict[str, int]] = {}
        totals = {"done": 0, "pending": 0, "error": 0, "skipped": 0, "other": 0}
        for r in by_source_status:
            src, st, n = r["source"] or "desconocido", r["scrape_status"], r["n"]
            bucket = catalog_by_source.setdefault(src, {"done": 0, "pending": 0, "error": 0, "total": 0})
            if st in bucket:
                bucket[st] += n
            bucket["total"] += n
            if st in totals:
                totals[st] += n
            else:
                totals["other"] += n
        for r in by_tipo_status:
            tp, st, n = r["tipo"], r["scrape_status"], r["n"]
            bucket = catalog_by_tipo.setdefault(tp, {"done": 0, "pending": 0, "error": 0, "total": 0})
            if st in bucket:
                bucket[st] += n
            bucket["total"] += n

        done = totals["done"]
        pending = totals["pending"]
        error = totals["error"]

        # ── Progreso + ETA basado en tasa de los últimos minutos ──
        # Cuenta filas marcadas done en los últimos N minutos para estimar tasa.
        rate_window_min = 10
        try:
            recent = conn.execute(
                "SELECT COUNT(*) FROM catalog "
                "WHERE scrape_status='done' AND updated_at >= datetime('now', ?)",
                (f"-{rate_window_min} minutes",),
            ).fetchone()[0]
        except Exception:
            recent = 0
        rate_per_min = recent / rate_window_min if recent else 0
        remaining = pending
        eta_seconds: Optional[int] = None
        if rate_per_min > 0:
            eta_seconds = int(remaining / rate_per_min * 60)
        pct = round(done / total_docs * 100, 2) if total_docs else 0.0

        # ── Qdrant ──
        qdrant = {
            "legal_corpus": _qdrant_count("legal_corpus"),
            "legal_corpus__docreps": _qdrant_count("legal_corpus__docreps"),
        }

        # ── Neo4j ──
        neo4j_stats = _neo4j_counts()

        # ── Mapeo 660k: actual (ingerido) vs objetivo ──
        ingerido_by_source = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM catalog GROUP BY source"
            ).fetchall()
        }
        mapeo_660k = []
        for key, meta in _FUENTES_660K.items():
            actual = ingerido_by_source.get(key, 0)
            objetivo = meta["objetivo"]
            mapeo_660k.append({
                "fuente": key,
                "nombre": meta["nombre"],
                "actual": actual,
                "objetivo": objetivo,
                "faltante": max(objetivo - actual, 0),
                "pct_objetivo": round(actual / objetivo * 100, 2) if objetivo else 0.0,
            })
        mapeo_660k.sort(key=lambda x: x["objetivo"], reverse=True)

        # ── Calidad: parsed.json con 0 articles Y 0 raw_text (corpus vacío) ──
        # Escanear 139k+ JSONs en cada poll de 10s es inviable. Estrategia:
        #   · Un parsed.json "vacío" (0 articles Y 0 raw_text) serializa a <2KB
        #     (verificado empíricamente: ~90% de los <2KB son vacíos reales y
        #      <0.4% de los ≥2KB lo son). Así que solo abrimos los candidatos
        #      pequeños y aplicamos la verificación ground-truth ahí; los demás
        #      cuentan para el denominador (total parseados) vía stat rápido.
        #   · El recuento se cachea en memoria 5 min porque los vacíos no cambian
        #     segundo a segundo —solo cuando corre el scraper/reparser.
        vacios = 0
        revisados = 0
        cache = _MONITOR_CALIDAD_CACHE
        now_ts = time.time()
        if cache.get("ts") and (now_ts - cache["ts"]) < 300:
            vacios = cache["vacios"]
            revisados = cache["revisados"]
        else:
            try:
                for src_dir in RAW_DIR.iterdir():
                    if not src_dir.is_dir():
                        continue
                    for parsed_path in src_dir.rglob("parsed.json"):
                        revisados += 1
                        try:
                            if parsed_path.stat().st_size >= 2048:
                                continue  # casi seguro tiene contenido
                            d = json.loads(
                                parsed_path.read_text(encoding="utf-8")
                            )
                            n_art = len(d.get("articles", []) or [])
                            raw_len = len(d.get("raw_text", "") or "")
                            if n_art == 0 and raw_len == 0:
                                vacios += 1
                        except Exception:
                            vacios += 1  # corrupto = inservible, contamos como vacío
                cache["vacios"] = vacios
                cache["revisados"] = revisados
                cache["ts"] = now_ts
            except FileNotFoundError:
                log.warning("RAW_DIR no existe: %s", RAW_DIR)
        pct_vacios = round(vacios / revisados * 100, 2) if revisados else 0.0

        # ── Recursos VM ──
        vm = _vm_resources()

        return {
            "catalog": {
                "total_docs": total_docs,
                "done": done,
                "pending": pending,
                "error": error,
                "by_source": catalog_by_source,
                "by_tipo": catalog_by_tipo,
            },
            "progress": {
                "pct_scrapeado": pct,
                "rate_per_min": round(rate_per_min, 2),
                "rate_window_min": rate_window_min,
                "recent_done": recent,
                "remaining": remaining,
                "eta_seconds": eta_seconds,
            },
            "qdrant": qdrant,
            "neo4j": neo4j_stats,
            "mapeo_660k": {
                "fuentes": mapeo_660k,
                "objetivo_total": sum(m["objetivo"] for m in _FUENTES_660K.values()),
                "actual_total": sum(m["actual"] for m in mapeo_660k),
            },
            "calidad": {
                "parsed_revisados": revisados,
                "vacios": vacios,
                "pct_vacios": pct_vacios,
            },
            "vm": vm,
            "generated_at": conn.execute("SELECT datetime('now')").fetchone()[0],
        }
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Tools para LLMs (HTTP/OpenAPI) — mismas funciones que el servidor MCP.
# Esquema limpio en /docs para function-calling de cualquier LLM.
# ═══════════════════════════════════════════════════════════════════════════


class BuscarRequest(BaseModel):
    query: str = Field(..., description="Consulta en lenguaje natural")
    limit: int = Field(10, ge=1, le=50)
    tipo: Optional[str] = Field(None, description="LEY, DECRETO, SENTENCIA, …")
    anio: Optional[str] = None
    estado_vigencia: Optional[str] = Field(None, description="vigente, derogado, …")
    excluir_derogadas: bool = False


class VigenciaRequest(BaseModel):
    canonical_id: str = Field(..., description="id co:… de la norma")
    fecha: Optional[str] = Field(None, description="DD/MM/YYYY o YYYY-MM-DD")
    articulo: Optional[str] = Field(None, description="nº de artículo normalizado")


class GrafoRequest(BaseModel):
    canonical_id: str
    relacion: Optional[str] = Field(None, description="CITA_A, MODIFICA, DEROGA, …")
    direccion: str = Field("ambas", description="salientes | entrantes | ambas")
    limit: int = Field(50, ge=1, le=200)


@app.post("/api/tools/buscar", tags=["tools"])
def tool_buscar(req: BuscarRequest):
    """Búsqueda semántica híbrida sobre el corpus legal colombiano."""
    from scrapper_leyes.tools import buscar_normas
    return buscar_normas(
        req.query, limit=req.limit, tipo=req.tipo, anio=req.anio,
        estado_vigencia=req.estado_vigencia, excluir_derogadas=req.excluir_derogadas,
    )


@app.post("/api/tools/texto-vigente", tags=["tools"])
def tool_texto_vigente(req: VigenciaRequest):
    """Estado de vigencia y texto operante de una norma/artículo a una fecha."""
    from scrapper_leyes.tools import texto_vigente
    return texto_vigente(req.canonical_id, fecha=req.fecha, articulo=req.articulo)


@app.post("/api/tools/grafo", tags=["tools"])
def tool_grafo(req: GrafoRequest):
    """Vecindario de un nodo en el grafo de conocimiento legal."""
    from scrapper_leyes.tools import consulta_grafo
    return consulta_grafo(
        req.canonical_id, relacion=req.relacion, direccion=req.direccion, limit=req.limit,
    )


class EstadisticaRequest(BaseModel):
    corte: Optional[str] = None
    materia: Optional[str] = None
    magistrado: Optional[str] = None
    anio_desde: Optional[int] = None
    anio_hasta: Optional[int] = None
    tipo: Optional[str] = "SENTENCIA"
    top: int = Field(15, ge=1, le=50)


@app.post("/api/tools/estadistica", tags=["tools"])
def tool_estadistica(req: EstadisticaRequest):
    """Jurimetría: distribuciones del corpus jurisprudencial + sentido del fallo."""
    from scrapper_leyes.tools import estadistica_jurisprudencial
    return estadistica_jurisprudencial(
        corte=req.corte, materia=req.materia, magistrado=req.magistrado,
        anio_desde=req.anio_desde, anio_hasta=req.anio_hasta, tipo=req.tipo, top=req.top,
    )


# GET de conveniencia para el dashboard (mismos datos, query params).
@app.get("/api/jurimetria", tags=["tools"])
def get_jurimetria(
    corte: Optional[str] = None,
    materia: Optional[str] = None,
    magistrado: Optional[str] = None,
    anio_desde: Optional[int] = None,
    anio_hasta: Optional[int] = None,
    tipo: Optional[str] = "SENTENCIA",
    top: int = Query(15, ge=1, le=50),
):
    """Jurimetría para la vista del dashboard."""
    from scrapper_leyes.tools import estadistica_jurisprudencial
    return estadistica_jurisprudencial(
        corte=corte, materia=materia, magistrado=magistrado,
        anio_desde=anio_desde, anio_hasta=anio_hasta, tipo=tipo, top=top,
    )


@app.get("/api/graph/hierarchy", tags=["graph"])
def get_hierarchy_graph(limit: int = Query(500, ge=10, le=5000)):
    """Grafo jerárquico: Constitución → Tratados → Leyes → Decretos → Actos admin → Jurisprudencia → Territorial."""
    cypher = (
        "MATCH (n) WHERE n.nivel_jerarquico IS NOT NULL "
        "WITH n ORDER BY n.nivel_jerarquico, n.tipo LIMIT $limit "
        "RETURN collect({"
        "id: n.id, tipo: n.tipo, numero: n.numero, anio: n.anio, "
        "nivel: n.nivel_jerarquico, rama: n.rama_poder, suin_id: n.suin_id, "
        "name: coalesce(n.tipo,'') + ' ' + coalesce(n.numero,'') + ' de ' + coalesce(n.anio,'')"
        "}) AS nodes"
    )
    nodes = []
    if neo4j_driver:
        with neo4j_driver.session() as session:
            for r in session.run(cypher, limit=limit):
                nodes = r["nodes"]
                break

    # Counts reales por nivel (sin limit)
    counts_by_level = {}
    rama_counts = {}
    if neo4j_driver:
        with neo4j_driver.session() as session:
            for r in session.run(
                "MATCH (n) WHERE n.nivel_jerarquico IS NOT NULL "
                "RETURN n.nivel_jerarquico AS nivel, count(*) AS c ORDER BY nivel"
            ):
                counts_by_level[r["nivel"]] = r["c"]
            # Counts por rama
            rama_counts = {}
            for r in session.run(
                "MATCH (n) WHERE n.rama_poder IS NOT NULL AND n.nivel_jerarquico IS NOT NULL "
                "RETURN n.rama_poder AS rama, count(*) AS c ORDER BY c DESC"
            ):
                rama_counts[r["rama"]] = r["c"]
    nivel_info = {
        1: {"nombre": "Constitución", "color": "#1a237e", "descripcion": "Constitución Política y Actos Legislativos"},
        2: {"nombre": "Bloque Constitucional", "color": "#283593", "descripcion": "Tratados Internacionales de DDHH"},
        3: {"nombre": "Leyes", "color": "#1565c0", "descripcion": "Leyes, Leyes Estatutarias y Orgánicas"},
        4: {"nombre": "Decretos", "color": "#0277bd", "descripcion": "Decretos reglamentarios y ley"},
        5: {"nombre": "Actos Administrativos", "color": "#00838f", "descripcion": "Resoluciones, Conceptos, Circulares"},
        6: {"nombre": "Jurisprudencia", "color": "#2e7d32", "descripcion": "Sentencias de Altas Cortes"},
        7: {"nombre": "Normativa Territorial", "color": "#558b2f", "descripcion": "Acuerdos y ordenanzas territoriales"},
    }

    # Niveles para la visualización
    niveles = [
        {"nivel": k, "nombre": v["nombre"], "color": v["color"],
         "descripcion": v["descripcion"], "count": counts_by_level.get(k, 0)}
        for k, v in sorted(nivel_info.items())
    ]

    # Relaciones jerárquicas (conexiones entre niveles)
    links = []
    for i in range(1, 7):
        links.append({"source_nivel": i, "target_nivel": i + 1, "type": "jerarquia"})

    return {
        "niveles": niveles,
        "nodes": nodes,
        "links_jerarquia": links,
        "total_nodos": len(nodes),
        "rama_poder": rama_counts,
    }
