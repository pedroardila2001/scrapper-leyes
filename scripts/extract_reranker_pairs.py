#!/usr/bin/env python3
"""Extrae pares de entrenamiento para fine-tuning de un reranker legal colombiano.

Genera un dataset JSONL con pares (query, positive_doc, negative_docs) usando:
1. Aristas CITA_A del grafo Neo4j (sentencia cita norma = par positivo)
2. Aristas SIMILAR_A (documentos temáticamente relacionados)
3. Estructura de artículos (contexto circundante = query, artículo = positivo)
4. Hard negatives vía BM25/Qdrant (docs que parecen relevantes pero no lo son)

Output: data/reranker_train.jsonl
Formato: {"query": "...", "positive": "...", "negatives": ["...", "..."]}
"""
import json
import sys
import random
import sqlite3
from pathlib import Path
from neo4j import GraphDatabase
import httpx
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/catalog.db"
NEO4J_URI = "bolt://neo4j:7687"
NEO4J_AUTH = ("neo4j", "password")
QDRANT_URL = "http://qdrant:6333"
OUTPUT = "/app/data/reranker_train.jsonl"

random.seed(42)


def load_catalog():
    """Carga metadatos del catálogo para construir queries."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT canonical_id, tipo, numero, anio, suin_id, source, scrape_status
        FROM catalog 
        WHERE scrape_status = 'done' AND canonical_id IS NOT NULL
    """).fetchall()
    conn.close()
    return {r["canonical_id"]: dict(r) for r in rows}


def load_parsed_text(catalog_entry):
    """Carga el texto de un documento desde parsed.json."""
    source = catalog_entry.get("source", "suin")
    tipo = catalog_entry.get("tipo", "")
    suin_id = catalog_entry.get("suin_id", "")
    if not suin_id:
        return None
    
    # Buscar el parsed.json
    base = Path(f"/app/data/raw/{source}/{tipo}/{suin_id}/parsed.json")
    if not base.exists():
        # Fallback: buscar en otras fuentes
        for alt in ["suin", "corte_constitucional", "csj"]:
            alt_path = Path(f"/app/data/raw/{alt}/{tipo}/{suin_id}/parsed.json")
            if alt_path.exists():
                base = alt_path
                break
        else:
            return None
    
    try:
        d = json.loads(base.read_text(encoding="utf-8"))
        # Combinar articles + raw_text
        texts = []
        for art in (d.get("articles") or []):
            t = art.get("text", "")
            if t:
                texts.append(t)
        rt = (d.get("raw_text") or "").strip()
        if rt:
            texts.append(rt[:5000])  # Limitar para no exceder contexto
        return " ".join(texts)[:8000]  # Max 8k chars
    except Exception:
        return None


def extract_citation_pairs(driver, catalog, limit=30000):
    """Extrae pares de citas: el contexto de la cita = query, la norma citada = positiva."""
    pairs = []
    with driver.session() as session:
        # Sentencias que citan normas
        result = session.run("""
            MATCH (s:Sentencia)-[r:CITA_A]->(n:Norma)
            WHERE s.id IS NOT NULL AND n.id IS NOT NULL
            RETURN s.id as sentencia_id, n.id as norma_id, 
                   r.score as score, r.context as context
            LIMIT $limit
        """, limit=limit)
        
        for record in result:
            sentencia_cid = record["sentencia_id"]
            norma_cid = record["norma_id"]
            
            if sentencia_cid not in catalog or norma_cid not in catalog:
                continue
            
            # El contexto de la cita sirve como query
            context = record.get("context", "")
            if not context or len(context) < 50:
                # Generar query sintética del título
                norm_entry = catalog[norma_cid]
                query = f"{norm_entry['tipo']} {norm_entry['numero']} de {norm_entry['anio']}"
            else:
                query = context[:500]
            
            # El texto de la norma citada es el documento positivo
            positive_text = load_parsed_text(catalog[norma_cid])
            if not positive_text or len(positive_text) < 100:
                continue
            
            pairs.append({
                "query": query,
                "positive": positive_text[:4000],
                "norma_cid": norma_cid,
                "tipo": "citation",
            })
    
    logger.info(f"Extraídos {len(pairs)} pares de citas")
    return pairs


def extract_similarity_pairs(driver, catalog, limit=20000):
    """Extrae pares de documentos similares (temáticamente relacionados)."""
    pairs = []
    with driver.session() as session:
        result = session.run("""
            MATCH (a)-[r:SIMILAR_A]-(b)
            WHERE a.id IS NOT NULL AND b.id IS NOT NULL
            AND r.score > 0.6
            RETURN a.id as id_a, b.id as id_b, r.score as score
            ORDER BY r.score DESC
            LIMIT $limit
        """, limit=limit)
        
        for record in result:
            cid_a = record["id_a"]
            cid_b = record["id_b"]
            
            if cid_a not in catalog or cid_b not in catalog:
                continue
            
            text_a = load_parsed_text(catalog[cid_a])
            text_b = load_parsed_text(catalog[cid_b])
            
            if not text_a or not text_b or len(text_a) < 200 or len(text_b) < 200:
                continue
            
            # Usar un fragmento de A como query, B como positivo
            query = text_a[:400]
            pairs.append({
                "query": query,
                "positive": text_b[:4000],
                "norma_cid": cid_b,
                "tipo": "similarity",
            })
    
    logger.info(f"Extraídos {len(pairs)} pares de similitud")
    return pairs


def extract_article_pairs(catalog, limit=10000):
    """Extrae pares de artículos: encabezado = query, artículo completo = positivo."""
    pairs = []
    sources = list(catalog.values())
    random.shuffle(sources)
    
    for entry in sources[:limit]:
        text = load_parsed_text(entry)
        if not text or len(text) < 500:
            continue
        
        # El primer párrafo o encabezado sirve como query
        paragraphs = text.split("\n")
        if len(paragraphs) < 3:
            continue
        
        query = paragraphs[0][:300] if len(paragraphs[0]) > 50 else " ".join(paragraphs[:2])[:300]
        positive = text[:4000]
        
        pairs.append({
            "query": query,
            "positive": positive,
            "norma_cid": entry.get("canonical_id"),
            "tipo": "article",
        })
    
    logger.info(f"Extraídos {len(pairs)} pares de artículos")
    return pairs


def mine_hard_negatives(query, positive_cid, qdrant_client, top_k=10):
    """Minera hard negatives: docs que Qdrant considera similares pero no son el positivo."""
    try:
        # Buscar en Qdrant
        result = qdrant_client.query_points(
            collection_name="legal_corpus",
            query=query,
            limit=top_k + 5,
            with_payload=True,
        )
        
        negatives = []
        for point in result.points:
            payload = point.payload or {}
            norm_cid = payload.get("norm_canonical_id") or payload.get("canonical_id")
            if norm_cid and norm_cid != positive_cid:
                text = payload.get("text", "")
                if text and len(text) > 100:
                    negatives.append(text[:2000])
        
        return random.sample(negatives, min(5, len(negatives))) if negatives else []
    except Exception as e:
        logger.debug(f"Hard negative mining failed: {e}")
        return []


def main():
    logger.info("=== EXTRACCIÓN DE PARES PARA RERANKER ===")
    
    # 1. Cargar catálogo
    catalog = load_catalog()
    logger.info(f"Catálogo cargado: {len(catalog)} documentos")
    
    # 2. Conectar a Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    
    # 3. Extraer pares de cada fuente
    all_pairs = []
    
    citation_pairs = extract_citation_pairs(driver, catalog, limit=20000)
    all_pairs.extend(citation_pairs)
    
    similarity_pairs = extract_similarity_pairs(driver, catalog, limit=15000)
    all_pairs.extend(similarity_pairs)
    
    article_pairs = extract_article_pairs(catalog, limit=8000)
    all_pairs.extend(article_pairs)
    
    driver.close()
    logger.info(f"Total pares antes de hard negatives: {len(all_pairs)}")
    
    # 4. Minerar hard negatives (subconjunto para no tardar)
    from qdrant_client import QdrantClient
    qdrant = QdrantClient(url=QDRANT_URL)
    
    random.shuffle(all_pairs)
    pairs_with_negs = []
    
    for i, pair in enumerate(all_pairs[:15000]):  # Limitar a 15k con hard negs
        if i % 1000 == 0:
            logger.info(f"Hard negatives: {i}/{min(len(all_pairs), 15000)}")
        
        negatives = mine_hard_negatives(
            pair["query"], pair["norma_cid"], qdrant, top_k=15
        )
        
        if negatives:
            pair["negatives"] = negatives
            pairs_with_negs.append(pair)
    
    logger.info(f"Pares con hard negatives: {len(pairs_with_negs)}")
    
    # 5. Guardar
    output_path = Path(OUTPUT)
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in pairs_with_negs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    
    logger.info(f"=== COMPLETO: {len(pairs_with_negs)} pares guardados en {OUTPUT} ===")
    
    # Stats
    tipos = {}
    for p in pairs_with_negs:
        tipos[p["tipo"]] = tipos.get(p["tipo"], 0) + 1
    logger.info(f"Distribución: {tipos}")


if __name__ == "__main__":
    main()
