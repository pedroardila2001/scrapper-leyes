#!/usr/bin/env python3
"""Construye el sistema de jerarquías jurídicas en Neo4j.

Asigna propiedades ``nivel_jerarquico`` y ``rama_poder`` a todos los nodos
Norma/Sentencia/Articulo, crea un nodo raíz ``CONSTITUCION_1991`` con nodos
``NivelJerarquico`` por cada nivel, y materializa relaciones explícitas de
jerarquía entre documentos:

    (Ley)-[:DESARROLLA]->(Articulo de la Constitución)
    (Decreto)-[:REGLAMENTA]->(Ley/Decreto reglamentado)
    (Sentencia)-[:CONTROLA]->(Norma controlada)

Idempotente: usa ``MERGE`` en todo Neo4j, así que puede correrse cuantas veces
sea necesario sin duplicar nodos ni aristas.

Diseñado para ejecutarse dentro del contenedor ``scrapper-leyes-ingest``::

    docker run --rm --network scrapper-leyes_default --entrypoint scrapper-leyes \
        -v $PWD/data:/app/data -v $PWD/src:/app/src -v $PWD/scripts:/scripts \
        -e DATA_DIR=/app/data -e NEO4J_URI=bolt://neo4j:7687 \
        scrapper-leyes-ingest python3 /scripts/build_hierarchy.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from neo4j import GraphDatabase

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("build_hierarchy")

# ── Conexión ────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "catalog.db"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "password")
RAW_BASE = DATA_DIR / "raw"

BATCH_SIZE = int(os.environ.get("HIERARCHY_BATCH", "2000"))


# ════════════════════════════════════════════════════════════════════════════
# Tablas de clasificación jerárquica
# ════════════════════════════════════════════════════════════════════════════
#
# nivel_jerarquico sigue la pirámide de Kelsen adaptada al ordenamiento
# colombiano (1 = cima, 7 = base). rama_poder mapea al órgano que expide.
#
# La clasificación por ``tipo`` es robusta a las variantes sucias que arrivals
# desde distintas fuentes (SUIN, funcion_publica, Socrata): se normaliza a
# mayúsculas y se comparan prefijos cuando hay ambigüedad.

# tipo (UPPERCASE) → (nivel, rama_poder)
# rama_poder ∈ {constitucional, legislativo, ejecutivo, judicial, control,
#               electoral, supra_nacional, territorial, autonomo, administrativo}
TIPO_NIVEL: dict[str, tuple[int, str]] = {
    # ── Nivel 1: Constitución y reformas constitucionales ──────────────────
    "CONSTITUCION POLITICA": (1, "constitucional"),
    "CONSTITUCION": (1, "constitucional"),
    "ACTO LEGISLATIVO": (1, "constitucional"),
    "ACTO": (1, "constitucional"),
    # ── Nivel 2: Bloque de constitucionalidad (tratados) ───────────────────
    "TRATADO": (2, "supra_nacional"),
    # ── Nivel 3: Ley (incluye estatutarias/orgánicas via subtipo) ──────────
    "LEY": (3, "legislativo"),
    "CODIGO": (3, "legislativo"),
    "LEY ESTATUTARIA": (3, "legislativo"),
    "LEY ORGANICA": (3, "legislativo"),
    # ── Nivel 4: Decretos (ley y reglamentarios) ───────────────────────────
    "DECRETO": (4, "ejecutivo"),
    "DECRETO LEY": (4, "ejecutivo"),
    "DECRETO ORDINARIO": (4, "ejecutivo"),
    "LD": (4, "ejecutivo"),  # "Decreto Ley" abreviado en algunas fuentes
    "DIRECTIVA PRESIDENCIAL": (4, "ejecutivo"),
    "DIRECTIVA": (4, "ejecutivo"),
    "DIRECTIVA VICEPRESIDENCIAL": (4, "ejecutivo"),
    "DIRECTIVA MINISTERIAL": (4, "ejecutivo"),
    "CONPES": (4, "ejecutivo"),
    "INSTRUCCION": (4, "ejecutivo"),
    "INSTRUCCION ADMINISTRATIVA CONJUNTA": (4, "ejecutivo"),
    # ── Nivel 5: Actos administrativos (resoluciones, circulares, conceptos)
    "RESOLUCION": (5, "administrativo"),
    "RESOLUCION EXTERNA": (5, "administrativo"),
    "CIRCULAR": (5, "administrativo"),
    "CIRCULAR EXTERNA": (5, "administrativo"),
    "CIRCULAR CONJUNTA": (5, "administrativo"),
    "CIRCULAR VICEPRESIDENCIAL": (5, "administrativo"),
    "CARTA CIRCULAR": (5, "administrativo"),
    "CONCEPTO": (5, "control"),
    "OPINION_CONSULTIVA": (5, "control"),
    "OPINION CONSULTIVA": (5, "control"),
    "ORDEN ADMINISTRATIVA": (5, "administrativo"),
    # ── Nivel 6: Jurisprudencia (sentencias y autos de altas cortes) ───────
    "SENTENCIA": (6, "judicial"),
    "AUTO": (6, "judicial"),
    "FALLO DISCIPLINARIO": (6, "control"),
    # ── Nivel 7: Normativa territorial (acuerdos municipales/departamentales)
    "ACUERDO": (7, "territorial"),
}

# Valor por defecto cuando un tipo no aparece en TIPO_NIVEL. Lo dejamos en el
# nivel administrativo (5) para circulares/variantes menores no catalogadas,
# antes que en NULL, para que ningún nodo quede sin jerarquía.
DEFAULT_NIVEL = (5, "administrativo")

# Overrides por subtipo para LEY (estatutarias y orgánicas siguen siendo nivel 3
# pero marcamos el carácter especial en la propiedad ``subtipo_jerarquico``).
SUBTIPO_ESPECIAL: set[str] = {
    "LEY ESTATUTARIA",
    "LEY ORGANICA",
    "LEY MARCO",
}

# Catálogo de niveles para los nodos NivelJerarquico.
NIVELES_DESC: dict[int, str] = {
    1: "Constitución y Actos Legislativos",
    2: "Bloque de Constitucionalidad (Tratados)",
    3: "Ley (incluye estatutarias y orgánicas)",
    4: "Decretos y Directivas del Ejecutivo",
    5: "Actos Administrativos (Resoluciones, Circulares, Conceptos)",
    6: "Jurisprudencia (Sentencias y Autos de Altas Cortes)",
    7: "Normativa Territorial (Acuerdos)",
}

# Mapeo de valores ``rama`` del catálogo SQLite → valor normalizado rama_poder.
RAMA_NORMALIZE: dict[str, str] = {
    "Rama Ejecutiva": "ejecutivo",
    "Rama Judicial": "judicial",
    "Rama Legislativa": "legislativo",
    "Organismos de Control": "control",
    "Órgano Autónomo": "autonomo",
    "Organo Autonomo": "autonomo",
    "Órgano Electoral": "electoral",
    "Organo Electoral": "electoral",
    "Internacional": "supra_nacional",
    "Otros": "administrativo",
}

# IDs fijos
CID_CONSTITUCION = "co:constitucion:1991"
CID_CONSTITUCION_ROOT = "co:constitucion:1991:root"  # nodo raíz del árbol


# ════════════════════════════════════════════════════════════════════════════
# Utilidades de clasificación
# ════════════════════════════════════════════════════════════════════════════

def normalize_tipo(tipo: str | None) -> str:
    """Normaliza un tipo sucio a su forma canónica UPPERCASE para el lookup."""
    if not tipo:
        return ""
    t = tipo.strip().upper().replace("_", " ")
    # Colapsar espacios múltiples
    t = re.sub(r"\s+", " ", t)
    return t


def classify(tipo: str | None, subtipo: str | None = None) -> tuple[int, str, str]:
    """Devuelve (nivel_jerarquico, rama_poder, subtipo_jerarquico)."""
    t = normalize_tipo(tipo)
    nivel, rama = TIPO_NIVEL.get(t) or (
        _lookup_fuzzy(t) or DEFAULT_NIVEL
    )
    sub_jer = ""
    st = normalize_tipo(subtipo)
    if st in SUBTIPO_ESPECIAL:
        sub_jer = st
    return nivel, rama, sub_jer


def _lookup_fuzzy(t: str) -> tuple[int, str] | None:
    """Busca coincidencia por prefijo cuando el tipo exacto no está."""
    # Variantes observadas en el catálogo: "CIRCULAR MININTERIOR", etc.
    for prefix, val in (
        ("CONSTITUCION", (1, "constitucional")),
        ("ACTO LEGISLATIVO", (1, "constitucional")),
        ("LEY", (3, "legislativo")),
        ("DECRETO", (4, "ejecutivo")),
        ("RESOLUCION", (5, "administrativo")),
        ("CIRCULAR", (5, "administrativo")),
        ("CONCEPTO", (5, "control")),
        ("DIRECTIVA", (4, "ejecutivo")),
        ("ACUERDO", (7, "territorial")),
        ("TRATADO", (2, "supra_nacional")),
    ):
        if t.startswith(prefix):
            return val
    return None


def resolve_rama_poder(
    tipo: str | None,
    subtipo: str | None,
    rama_sqlite: str | None,
    corte: str | None,
) -> str:
    """Resuelve rama_poder usando SQLite cuando está disponible, si no el tipo."""
    if rama_sqlite:
        norm = RAMA_NORMALIZE.get(rama_sqlite.strip())
        if norm:
            return norm
    # Sentencias: la corte define la rama
    if corte == "cc":
        return "judicial"
    if corte == "csj":
        return "judicial"
    if corte == "ce":
        return "control"
    if corte == "jep":
        return "judicial"
    # Caer al valor derivado del tipo
    _, rama, _ = classify(tipo, subtipo)
    return rama


# ════════════════════════════════════════════════════════════════════════════
# Parsing de referencias normativas (para DESARROLLA / REGLAMENTA)
# ════════════════════════════════════════════════════════════════════════════

# Patrón: "artículo 23 de la Constitución" / "artículo 209 constitucional"
_ART_CONST_RE = re.compile(
    r"art[ií]culo\s+(?P<num>\d+[a-z]?)"
    r"(?:\s+de\s+la\s+)?(?:constituci[oó]n|carta\s+pol[ií]tica|c\.?\s*p\.?)",
    re.IGNORECASE,
)

# "reglamenta [parcialmente] la Ley 123 de 1994" / "reglamenta el Decreto 456..."
_REGLAMENTA_RE = re.compile(
    r"reglament\w*\s+(?:parcialmente\s+)?"
    r"(?:la\s+|el\s+|los\s+|las\s+)?"
    r"(?P<tipo>ley|decreto(?:\s*[-/]?\s*ley)?|acto\s+legislativo|resoluci[oó]n)\s+"
    r"(?P<num>\d+)\s+de\s+(?P<anio>\d{4})",
    re.IGNORECASE,
)


def parse_constitution_articles(text: str) -> set[str]:
    """Extrae números de artículos constitucionales citados en un texto."""
    if not text:
        return set()
    return {m.group("num").lower() for m in _ART_CONST_RE.finditer(text)}


def parse_reglamenta_target(text: str) -> tuple[str, str, str] | None:
    """Extrae (tipo, numero, anio) de una cláusula 'reglamenta la Ley X de Y'."""
    if not text:
        return None
    m = _REGLAMENTA_RE.search(text)
    if not m:
        return None
    tipo_raw = re.sub(r"\s+", " ", m.group("tipo").lower()).strip()
    # Normalizar "decreto ley" / "decreto-ley" → "decreto" (canónico)
    if tipo_raw.startswith("decreto"):
        tipo = "decreto"
    elif tipo_raw.startswith("acto"):
        tipo = "acto_legislativo"
    elif tipo_raw.startswith("resoluc"):
        tipo = "resolucion"
    else:
        tipo = "ley"
    return tipo, m.group("num"), m.group("anio")


def build_norm_cid(tipo: str, numero: str, anio: str) -> str:
    """Construye un canonical_id normalizado co:{tipo}:{numero}:{anio}."""
    return f"co:{tipo}:{numero.lower()}:{anio}"


# ════════════════════════════════════════════════════════════════════════════
# Helpers SQLite
# ════════════════════════════════════════════════════════════════════════════

def load_catalog_classifications(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Carga (canonical_id → {tipo, subtipo, rama, corte}) del catálogo.

    Se usa para enriquecer los nodos Neo4j con clasificación precisa, en
    particular cuando Neo4j tiene tipo=NULL (nodos creados como objetivos de
    CITA_A sin exportarse desde el catálogo).
    """
    rows = conn.execute(
        """
        SELECT canonical_id, tipo, subtipo, rama, corte
        FROM catalog
        WHERE canonical_id IS NOT NULL AND scrape_status = 'done'
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        cid = d["canonical_id"]
        # Solo la primera ocurrencia (puede haber duplicados por source).
        if cid not in out:
            out[cid] = d
    log.info("Cargadas %d clasificaciones del catálogo SQLite", len(out))
    return out


def iter_parsed_text(
    conn: sqlite3.Connection, batch_size: int = 500
) -> Iterable[tuple[str, str]]:
    """Itera (canonical_id, texto_relevante) leyendo parsed.json de cada norma.

    El texto relevante es la concatenación del ``raw_text`` + primeros artículos,
    acotado para no penalizar memoria. Se usa para detectar cláusulas
    ``DESARROLLA`` (cita artículos constitucionales) y ``REGLAMENTA``.
    """
    rows = conn.execute(
        """
        SELECT canonical_id, tipo, suin_id, source
        FROM catalog
        WHERE canonical_id IS NOT NULL
          AND scrape_status = 'done'
          AND suin_id IS NOT NULL
          AND tipo IN ('LEY', 'DECRETO', 'ACTO LEGISLATIVO', 'CODIGO',
                       'CONSTITUCION POLITICA', 'RESOLUCION')
        """
    ).fetchall()

    sources_order = ["suin", "corte_constitucional", "csj", "consejo_estado",
                     "funcion_publica"]

    for r in rows:
        d = dict(r)
        cid = d["canonical_id"]
        tipo = d["tipo"]
        suin_id = d["suin_id"]
        pref_source = d.get("source") or "suin"
        ordered = [pref_source] + [s for s in sources_order if s != pref_source]
        parsed = None
        for src in ordered:
            if src is None:
                continue
            p = RAW_BASE / src / tipo / suin_id / "parsed.json"
            if p.exists():
                try:
                    parsed = json.loads(p.read_text(encoding="utf-8"))
                    break
                except Exception:
                    continue
        if not parsed:
            continue

        parts: list[str] = []
        rt = parsed.get("raw_text") or ""
        if rt:
            parts.append(rt[:4000])
        for art in (parsed.get("articles") or [])[:5]:
            t = art.get("text") or ""
            if t:
                parts.append(t[:800])
        text = " \n ".join(parts)[:6000]
        if text.strip():
            yield cid, text


# ════════════════════════════════════════════════════════════════════════════
# Builder principal
# ════════════════════════════════════════════════════════════════════════════

class HierarchyBuilder:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.catalog = load_catalog_classifications(self.conn)

    # ── ciclo de vida ──────────────────────────────────────────────────────
    def close(self) -> None:
        self.driver.close()
        self.conn.close()

    # ── pasos del builder ──────────────────────────────────────────────────

    def _ensure_indexes(self, session) -> None:
        """Índices para acelerar los MERGE por nivel/rama y lookups por tipo."""
        idxs = [
            "CREATE INDEX norma_nivel IF NOT EXISTS FOR (n:Norma) ON (n.nivel_jerarquico)",
            "CREATE INDEX norma_rama IF NOT EXISTS FOR (n:Norma) ON (n.rama_poder)",
            "CREATE INDEX sent_nivel IF NOT EXISTS FOR (s:Sentencia) ON (s.nivel_jerarquico)",
            "CREATE INDEX nivelnodo_id IF NOT EXISTS FOR (nj:NivelJerarquico) ON (nj.nivel)",
            "CREATE INDEX norma_tipo IF NOT EXISTS FOR (n:Norma) ON (n.tipo)",
        ]
        for q in idxs:
            try:
                session.run(q)
            except Exception as e:
                log.warning("Índice (puede ya existir): %s", e)

    def create_root_and_levels(self, session) -> None:
        """Crea el nodo raíz CONSTITUCION_1991 y los nodos NivelJerarquico."""
        log.info("Creando nodo raíz y niveles jerárquicos…")
        # Nodo raíz: la Constitución como vértice del árbol.
        session.run(
            "MERGE (root:Norma:ConstitucionRaiz {id: $id}) "
            "SET root.tipo = 'CONSTITUCION POLITICA', "
            "    root.numero = '1', root.anio = '1991', "
            "    root.nombre = 'Constitución Política de 1991', "
            "    root.nivel_jerarquico = 1, root.rama_poder = 'constitucional'",
            id=CID_CONSTITUCION_ROOT,
        )
        # Compatibilidad: el nodo de la constitución referenciado por las CITA_A
        # también debe existir y marcarse como nivel 1.
        session.run(
            "MERGE (c:Norma:Constitucion {id: $id}) "
            "SET c.tipo = 'CONSTITUCION POLITICA', "
            "    c.nombre = 'Constitución Política de 1991', "
            "    c.nivel_jerarquico = 1, c.rama_poder = 'constitucional'",
            id=CID_CONSTITUCION,
        )
        # La constitución concreta cuelga de la raíz.
        session.run(
            "MATCH (root:ConstitucionRaiz {id: $rid}), (c:Constitucion {id: $cid}) "
            "MERGE (root)-[:CONTIENE]->(c)",
            rid=CID_CONSTITUCION_ROOT, cid=CID_CONSTITUCION,
        )
        # Nodos NivelJerarquico (1..7) colgando de la raíz.
        for nivel, desc in NIVELES_DESC.items():
            session.run(
                "MERGE (nj:NivelJerarquico {nivel: $nivel}) "
                "SET nj.descripcion = $desc, nj.id = $id "
                "WITH nj "
                "MATCH (root:ConstitucionRaiz {id: $rid}) "
                "MERGE (root)-[:TIENE_NIVEL]->(nj)",
                nivel=nivel, desc=desc,
                id=f"nivel:{nivel}", rid=CID_CONSTITUCION_ROOT,
            )
        log.info("Nodo raíz + %d niveles creados", len(NIVELES_DESC))

    # ── (b) Asignar nivel_jerarquico + rama_poder ──────────────────────────

    def assign_node_properties(self, session) -> dict[str, int]:
        """Asigna nivel_jerarquico y rama_poder a todos los nodos Norma y Sentencia.

        Estrategia optimizada para evitar full-scans (que colgaban el proceso
        cuando se usaba ``MATCH (n) WHERE n.id = ...`` sin label):

        1. Clasificar primero por el ``tipo`` del nodo en Neo4j (indexado) — esto
           cubre la gran mayoría en un solo pase por tipo, sin UNWIND por id.
        2. Sentencias por corte (nivel 6).
        3. Fuzzy por prefijo para tipos sucios no catalogados.
        4. Enriquecimiento desde SQLite: actualizar ``rama_poder`` y
           ``subtipo_jerarquico`` usando el catálogo como fuente de verdad,
           mediante UNWIND indexado por ``:Norma(id)`` y ``:Sentencia(id)``.
        5. Default nivel 5 para lo que quede.
        """
        log.info("Asignando nivel_jerarquico y rama_poder a los nodos…")

        # ── Paso 1: clasificar por tipo del nodo en Neo4j (rápido, indexado) ──
        # Agrupamos tipos que mapean al mismo (nivel, rama) para reducir queries.
        by_nivel_rama: dict[tuple[int, str], list[str]] = {}
        for tipo_key, (nivel, rama) in TIPO_NIVEL.items():
            by_nivel_rama.setdefault((nivel, rama), []).append(tipo_key)

        classified_by_tipo = 0
        for (nivel, rama), tipos in by_nivel_rama.items():
            res = session.run(
                """
                MATCH (n:Norma)
                WHERE n.tipo IN $tipos AND n.nivel_jerarquico IS NULL
                SET n.nivel_jerarquico = $nivel, n.rama_poder = $rama
                RETURN count(n) AS c
                """,
                tipos=tipos, nivel=nivel, rama=rama,
            )
            c = res.single()["c"]
            classified_by_tipo += c
        log.info("Paso 1: %d normas clasificadas por tipo (Neo4j indexado)",
                 classified_by_tipo)

        # ── Paso 1b: Sentencias — siempre nivel 6, rama por corte ──────────
        res = session.run(
            """
            MATCH (s:Sentencia) WHERE s.nivel_jerarquico IS NULL
            SET s.nivel_jerarquico = 6,
                s.rama_poder = CASE
                    WHEN s.corte = 'ce' THEN 'control'
                    WHEN s.corte = 'jep' THEN 'judicial'
                    ELSE 'judicial'
                END
            RETURN count(s) AS c
            """
        )
        c_sent = res.single()["c"]
        log.info("Paso 1b: %d sentencias clasificadas por corte", c_sent)

        # ── Paso 1c: fuzzy por prefijo para tipos sucios no cubiertos ──────
        fuzzy_hits = 0
        for prefix, (nivel, rama) in (
            ("CONSTITUCION", (1, "constitucional")),
            ("LEY", (3, "legislativo")),
            ("DECRETO", (4, "ejecutivo")),
            ("RESOLUCION", (5, "administrativo")),
            ("CIRCULAR", (5, "administrativo")),
            ("ACUERDO", (7, "territorial")),
        ):
            res = session.run(
                """
                MATCH (n:Norma)
                WHERE n.nivel_jerarquico IS NULL AND n.tipo STARTS WITH $prefix
                SET n.nivel_jerarquico = $nivel, n.rama_poder = $rama
                RETURN count(n) AS c
                """,
                prefix=prefix, nivel=nivel, rama=rama,
            )
            fuzzy_hits += res.single()["c"]
        if fuzzy_hits:
            log.info("Paso 1c (fuzzy): %d normas clasificadas por prefijo",
                     fuzzy_hits)

        # ── Paso 2: enriquecimiento desde SQLite (rama_poder, subtipo) ─────
        # Usamos MATCH con LABEL explícito para usar el índice :Norma(id) /
        # :Sentencia(id). El catálogo SQLite aporta rama_poder más precisa
        # (ej. distinguir Órgano Electoral de Otros) y subtipo_jerarquico
        # (LEY ESTATUTARIA / ORGANICA).
        cat_batch: list[dict[str, Any]] = []
        for cid, meta in self.catalog.items():
            tipo = meta.get("tipo")
            subtipo = meta.get("subtipo")
            rama_sql = meta.get("rama")
            corte = meta.get("corte")
            nivel, _, sub_jer = classify(tipo, subtipo)
            rama = resolve_rama_poder(tipo, subtipo, rama_sql, corte)
            cat_batch.append({
                "cid": cid, "nivel": nivel, "rama": rama,
                "subtipo": sub_jer,
            })

        enriched = 0
        # Un pase por label (Norma y Sentencia) para usar el índice de id.
        for label in ("Norma", "Sentencia"):
            for i in range(0, len(cat_batch), BATCH_SIZE):
                chunk = cat_batch[i : i + BATCH_SIZE]
                res = session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (n:{label} {{id: row.cid}})
                    SET n.rama_poder = row.rama,
                        n.subtipo_jerarquico =
                            CASE WHEN row.subtipo <> ''
                                 THEN row.subtipo ELSE null END
                    RETURN count(n) AS c
                    """,
                    rows=chunk,
                )
                enriched += res.single()["c"]
        log.info("Paso 2: %d nodos enriquecidos con rama_poder desde SQLite",
                 enriched)

        # ── Paso 3: lo que quede sin nivel → default ───────────────────────
        # (nodos creados on-the-fly como objetivos de CITA_A, sin tipo ni catálogo)
        res = session.run(
            """
            MATCH (n:Norma) WHERE n.nivel_jerarquico IS NULL
            SET n.nivel_jerarquico = $nivel, n.rama_poder = $rama
            RETURN count(n) AS c
            """,
            nivel=DEFAULT_NIVEL[0], rama=DEFAULT_NIVEL[1],
        )
        c_def = res.single()["c"]
        if c_def:
            log.info("Paso 3: %d normas al nivel default (%d)",
                     c_def, DEFAULT_NIVEL[0])

        # Estadística final por nivel
        rows = session.run(
            "MATCH (n) WHERE n.nivel_jerarquico IS NOT NULL "
            "RETURN n.nivel_jerarquico AS nivel, count(n) AS c "
            "ORDER BY nivel"
        ).data()
        total = 0
        for r in rows:
            log.info("  Nivel %s: %s nodos", r["nivel"], f"{r['c']:,}")
            total += r["c"]
        log.info("Total nodos con nivel_jerarquico: %s", f"{total:,}")
        return {
            "classified_by_tipo": classified_by_tipo,
            "sentencias": c_sent,
            "fuzzy": fuzzy_hits,
            "enriched": enriched,
            "default": c_def,
        }

    # ── (f) Conectar nodos a su NivelJerarquico ────────────────────────────

    def link_to_levels(self, session) -> int:
        """Crea (n)-[:PERTENECE_AL_NIVEL]->(NivelJerarquico {nivel: n.nivel}).

        Separa por label (Norma/Sentencia) para usar el índice
        ``:Norma(nivel_jerarquico)`` / ``:Sentencia(nivel_jerarquico)`` y evitar
        cartesian products.
        """
        log.info("Conectando nodos a sus NivelJerarquico…")
        total = 0
        for nivel in NIVELES_DESC:
            c_nivel = 0
            for label in ("Norma", "Sentencia"):
                res = session.run(
                    f"""
                    MATCH (n:{label}), (nj:NivelJerarquico {{nivel: $nivel}})
                    WHERE n.nivel_jerarquico = $nivel
                    MERGE (n)-[:PERTENECE_AL_NIVEL]->(nj)
                    RETURN count(*) AS c
                    """,
                    nivel=nivel,
                )
                c_nivel += res.single()["c"]
            if c_nivel:
                log.info("  Nivel %d: %s enlaces", nivel, f"{c_nivel:,}")
            total += c_nivel
        log.info("Total enlaces PERTENECE_AL_NIVEL: %s", f"{total:,}")
        return total

    # ── (d) DESARROLLA: Ley → artículo constitucional ──────────────────────

    def build_desarrolla(self, session) -> int:
        """Crea (norma)-[:DESARROLLA]->(artículo constitucional).

        Detecta menciones explícitas a artículos de la Constitución en el texto
        de leyes/actos legislativos/códigos y une la norma con el nodo Articulo
        de la Constitución correspondiente. Si el artículo no existe aún como
        nodo (la Constitución no fue scrapeada con artículos en Neo4j), se crea
        como nodo Articulo colgando de la Constitución.
        """
        log.info("Construyendo relaciones DESARROLLA (Ley → Constitución)…")
        # Descubrir los artículos constitucionales referenciados en las CITA_A
        # hacia la constitución (indexado por :Norma(id)).
        textos = session.run(
            """
            MATCH (c:Norma {id: $cid})<-[r:CITA_A]-()
            WHERE r.texto IS NOT NULL AND r.texto <> ''
            RETURN collect(DISTINCT r.texto) AS textos
            """,
            cid=CID_CONSTITUCION,
        ).single()["textos"]
        log.info("  %d textos de cita a la Constitución para parsear", len(textos))

        # Descubrir todos los artículos constitucionales referenciados.
        art_nums: set[str] = set()
        for t in textos:
            art_nums |= parse_constitution_articles(t)
        log.info("  %d artículos constitucionales distintos referenciados", len(art_nums))

        # Crear los nodos Articulo faltantes (colgando de la Constitución).
        if art_nums:
            session.run(
                """
                UNWIND $nums AS num
                MERGE (a:Articulo {id: 'co:constitucion:1991:art:' + num})
                SET a.numero = num, a.tipo_norma = 'constitucion'
                WITH a
                MATCH (c:Norma {id: $cid})
                MERGE (a)-[:PERTENECE_A]->(c)
                """,
                nums=sorted(art_nums), cid=CID_CONSTITUCION,
            )

        # Ahora, para cada ley/acto/codigo scrapeado, detectar artículos
        # constitucionales en su texto y crear DESARROLLA.
        desarrolla_batch: list[dict[str, Any]] = []
        for cid, text in iter_parsed_text(self.conn):
            nums = parse_constitution_articles(text)
            if not nums:
                continue
            for num in nums:
                desarrolla_batch.append({
                    "src": cid,
                    "art_id": f"co:constitucion:1991:art:{num}",
                    "articulo": num,
                })

        log.info("  %s aristas DESARROLLA candidatas", f"{len(desarrolla_batch):,}")

        # Escribir en lotes.
        written = 0
        for i in range(0, len(desarrolla_batch), BATCH_SIZE):
            chunk = desarrolla_batch[i : i + BATCH_SIZE]
            res = session.run(
                """
                UNWIND $rows AS row
                MATCH (src:Norma {id: row.src})
                MATCH (art:Articulo {id: row.art_id})
                MERGE (src)-[r:DESARROLLA]->(art)
                SET r.source = 'hierarchy', r.articulo = row.articulo
                RETURN count(r) AS c
                """,
                rows=chunk,
            )
            written += res.single()["c"]
        log.info("Creadas %s relaciones DESARROLLA", f"{written:,}")
        return written

    # ── (d) REGLAMENTA: Decreto → Ley ─────────────────────────────────────

    def build_reglamenta(self, session) -> int:
        """Crea (decreto)-[:REGLAMENTA]->(ley/decreto reglamentado).

        Parsea el texto de cada decreto buscando la cláusula canónica
        "reglamenta [parcialmente] la Ley X de Y" y une el decreto al canonical
        ID de la norma reglamentada (creando el nodo si no existía).
        """
        log.info("Construyendo relaciones REGLAMENTA (Decreto → Ley)…")
        batch: list[dict[str, Any]] = []
        for cid, text in iter_parsed_text(self.conn):
            tgt = parse_reglamenta_target(text)
            if not tgt:
                continue
            tipo, num, anio = tgt
            tgt_cid = build_norm_cid(tipo, num, anio)
            batch.append({
                "src": cid, "tgt": tgt_cid,
                "tgt_tipo": tipo.upper(),
                "tgt_nombre": f"{tipo.capitalize()} {num} de {anio}",
            })
        log.info("  %s aristas REGLAMENTA candidatas", f"{len(batch):,}")

        written = 0
        for i in range(0, len(batch), BATCH_SIZE):
            chunk = batch[i : i + BATCH_SIZE]
            res = session.run(
                """
                UNWIND $rows AS row
                MATCH (src:Norma {id: row.src})
                MERGE (tgt:Norma {id: row.tgt})
                  ON CREATE SET tgt.nombre = row.tgt_nombre
                MERGE (src)-[r:REGLAMENTA]->(tgt)
                SET r.source = 'hierarchy'
                RETURN count(r) AS c
                """,
                rows=chunk,
            )
            written += res.single()["c"]
        log.info("Creadas %s relaciones REGLAMENTA", f"{written:,}")
        return written

    # ── (d) CONTROLA: Sentencia → Norma ───────────────────────────────────

    def build_controla(self, session) -> int:
        """Crea (sentencia)-[:CONTROLA]->(norma) a partir de las decisiones.

        Reutiliza las aristas tipadas DECLARA_INEXEQUIBLE / DECLARA_EXEQUIBLE /
        DECLARA_EXEQUIBLE_CONDICIONADA ya existentes (creadas por export_neo4j a
        partir del RESUELVE de cada sentencia) y proyecta una arista genérica
        CONTROLA que captura el control constitucional abstracto, conservando el
        tipo de decisión como propiedad.
        """
        log.info("Construyendo relaciones CONTROLA (Sentencia → Norma)…")
        # Proyectar desde las aristas de decisión existentes.
        res = session.run(
            """
            MATCH (s:Sentencia)-[r]->(t)
            WHERE type(r) IN [
                'DECLARA_INEXEQUIBLE', 'DECLARA_EXEQUIBLE',
                'DECLARA_EXEQUIBLE_CONDICIONADA', 'DECLARA_NULIDAD'
              ]
              AND s.id IS NOT NULL AND t.id IS NOT NULL
            WITH s, t, r
            MERGE (s)-[c:CONTROLA]->(t)
              SET c.source = 'hierarchy',
                  c.tipo_control = type(r),
                  c.decision = coalesce(r.tipo, type(r))
            RETURN count(c) AS c
            """
        )
        written = res.single()["c"]
        log.info("  %s CONTROLA desde aristas de decisión", f"{written:,}")

        # Sumar control desde jurisprudencia (backlinks SUIN con source).
        res2 = session.run(
            """
            MATCH (s:Sentencia)-[r]->(t)
            WHERE r.source IN ['jurisprudencia', 'resuelve']
              AND NOT (s)-[:CONTROLA]->(t)
              AND type(r) <> 'CONTROLA'
            WITH s, t, r LIMIT 20000
            MERGE (s)-[c:CONTROLA]->(t)
              SET c.source = 'hierarchy',
                  c.tipo_control = type(r)
            RETURN count(c) AS c
            """
        )
        written += res2.single()["c"]
        log.info("Creadas %s relaciones CONTROLA (total)", f"{written:,}")
        return written

    # ── Orquestador ────────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        t0 = time.time()
        stats: dict[str, Any] = {}
        with self.driver.session() as session:
            log.info("=== build_hierarchy: inicio ===")
            self._ensure_indexes(session)
            self.create_root_and_levels(session)
            stats["properties"] = self.assign_node_properties(session)
            stats["level_links"] = self.link_to_levels(session)
            stats["desarrolla"] = self.build_desarrolla(session)
            stats["reglamenta"] = self.build_reglamenta(session)
            stats["controla"] = self.build_controla(session)
        elapsed = time.time() - t0
        stats["elapsed_sec"] = round(elapsed, 1)
        log.info("=== build_hierarchy: fin (%.1fs) ===", elapsed)
        return stats


def main() -> int:
    if not DB_PATH.exists():
        log.error("No existe %s — ¿DATA_DIR correcto?", DB_PATH)
        return 2
    builder = HierarchyBuilder()
    try:
        # Verificar conectividad antes de empezar.
        with builder.driver.session() as s:
            s.run("RETURN 1").consume()
        log.info("Conectado a Neo4j (%s) y SQLite (%s)", NEO4J_URI, DB_PATH)
        stats = builder.run()
    finally:
        builder.close()
    # Resumen imprimible para el operador.
    print("\n" + "=" * 60)
    print("RESUMEN build_hierarchy")
    print("=" * 60)
    print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
