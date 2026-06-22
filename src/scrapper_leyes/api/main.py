"""FastAPI backend for the Legal AI Dashboard.

Serves catalog data from SQLite, parsed documents from the file cache,
vector chunk previews, and graph neighborhood data.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

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
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")

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

    Distingue 3 grupos: ``sentencia``, ``norma`` (ingeridas, con suin_id) y
    ``fantasma`` (referenciadas por una cita/afectación pero aún no ingeridas —
    p.ej. la Constitución o leyes citadas). Los fantasmas se dimensionan por
    grado para que los hubs reales (Constitución, códigos) se vean.
    """
    es_sentencia = "Sentencia" in node.labels
    ingerido = bool(node.get("suin_id"))
    if not ingerido:
        group = "fantasma"
        val = min(4 + degree, 22)
    else:
        group = "sentencia" if es_sentencia else "norma"
        val = 10 if es_sentencia else 8
    tipo_str = "Sentencia" if es_sentencia else (node.get("tipo") or "Norma")
    label = node.get("nombre") or f"{tipo_str} {node.get('numero', '')} de {node.get('anio', '')}"
    return {
        "id": node.get("id"),
        "suin_id": node.get("suin_id"),
        "name": label.strip(),
        "group": group,
        "ingerido": ingerido,
        "val": val,
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
