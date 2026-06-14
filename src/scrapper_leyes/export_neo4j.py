"""Export SQLite catalog and JSON caches into Neo4j Knowledge Graph.

Creates nodes for Normas, Artículos, Sentencias, and Magistrados,
plus edges for PERTENECE_A, MODIFICA, DECLARA_*, CITA_A, and FUE_PONENTE_DE.
"""

import logging
import re
from typing import Any
from neo4j import GraphDatabase
from scrapper_leyes.storage.database import Database
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.config import Settings

logger = logging.getLogger(__name__)


class Neo4jExporter:
    """Exports SQLite catalog and JSON caches into Neo4j Knowledge Graph."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        cache: ProvenanceCache,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
    ):
        self.settings = settings
        self.db = db
        self.cache = cache
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def export_all(self):
        """Export entire catalog to Neo4j."""
        with self.driver.session() as session:
            # Create constraints/indexes for performance
            self._ensure_indexes(session)

            # Export Norms (Leyes, Decretos)
            norms = self.db.conn.execute(
                "SELECT * FROM catalog WHERE tipo != 'SENTENCIA' AND scrape_status = 'done'"
            ).fetchall()
            exported_norms = 0
            for row in norms:
                self._export_norm(session, dict(row))
                exported_norms += 1

            logger.info(f"Exported {exported_norms} norms to Neo4j")

            # Export Jurisprudence
            sentencias = self.db.conn.execute(
                "SELECT * FROM catalog WHERE tipo = 'SENTENCIA' AND scrape_status = 'done'"
            ).fetchall()
            exported_sent = 0
            for row in sentencias:
                self._export_sentencia(session, dict(row))
                exported_sent += 1

            logger.info(f"Exported {exported_sent} sentencias to Neo4j")

            # Thematic interconnection via embeddings (beyond explicit citations).
            self._export_similarity_edges(session)

    def _ensure_indexes(self, session):
        """Create indexes and constraints for efficient graph operations."""
        try:
            session.run("CREATE INDEX norma_id IF NOT EXISTS FOR (n:Norma) ON (n.id)")
            session.run("CREATE INDEX articulo_id IF NOT EXISTS FOR (a:Articulo) ON (a.id)")
            session.run("CREATE INDEX sentencia_id IF NOT EXISTS FOR (s:Sentencia) ON (s.id)")
            session.run("CREATE INDEX magistrado_id IF NOT EXISTS FOR (m:Magistrado) ON (m.id)")
        except Exception as e:
            logger.warning(f"Index creation warning (may already exist): {e}")

    def _export_norm(self, session, row: dict[str, Any]):
        """Creates a Node for a Law/Decree and its Articles."""
        norm_id = row["canonical_id"] if "canonical_id" in row.keys() else f"co:{row['tipo'].lower()}:{row['numero']}:{row.get('anio', '?')}"
        tipo = row["tipo"]
        numero = row.get("numero")
        anio = row.get("anio")

        suin_id = row.get("suin_id")

        # Create Norm Node
        session.run(
            "MERGE (n:Norma {id: $id}) "
            "SET n.tipo = $tipo, n.numero = $numero, n.anio = $anio, n.suin_id = $suin_id",
            id=norm_id, tipo=tipo, numero=numero, anio=anio, suin_id=suin_id,
        )

        # Retrieve parsed articles
        suin_id = row.get("suin_id")
        if suin_id and row.get("scrape_status") == "done":
            parsed = self.cache.load_parsed("suin", tipo, suin_id)
            if parsed:
                # Export articles
                for article in parsed.get("articles", []):
                    art_cid = article.get("canonical_id", f"{norm_id}:art:{article.get('number_normalized', '?')}")
                    session.run(
                        "MERGE (a:Articulo {id: $art_id}) "
                        "SET a.titulo = $titulo, a.texto = $texto, a.numero = $numero "
                        "WITH a "
                        "MATCH (n:Norma {id: $norm_id}) "
                        "MERGE (a)-[:PERTENECE_A]->(n)",
                        art_id=art_cid,
                        titulo=article.get("title", ""),
                        texto=article.get("text", "")[:2000],
                        numero=article.get("number_normalized", ""),
                        norm_id=norm_id,
                    )

                # ── Export citation edges (CITA_A) ──────────────────────────
                self._export_citations(session, norm_id, parsed)

                # ── Export outgoing affectations (DEROGA/MODIFICA/…) ─────────
                self._export_origin_affectations(session, norm_id, parsed)

    def _export_origin_affectations(self, session, source_id: str, parsed: dict[str, Any]):
        """Create directed affectation edges from a norm to the norms it affects.

        Reads each article's ``affects`` (parsed from SUIN's official
        "Afecta la vigencia de" toggle) → edges like (norma)-[:DEROGA_TOTAL]->(target).
        These are authoritative, directional edges (source='suin').
        """
        seen: set[tuple[str, str]] = set()
        for art in parsed.get("articles", []):
            for aff in art.get("affects", []):
                rel = aff.get("normalized_type") or "AFECTA"
                if not re.fullmatch(r"[A-Z_]+", rel):
                    rel = "AFECTA"
                target_text = aff.get("target_text", "")
                parsed_target = self._citation_to_node_id(target_text)
                if not parsed_target:
                    continue
                target_id, target_name = parsed_target
                key = (rel, target_id)
                if key in seen:
                    continue
                seen.add(key)
                session.run(
                    f"MATCH (s {{id: $sid}}) "
                    f"MERGE (t:Norma {{id: $tid}}) ON CREATE SET t.nombre = $tn "
                    f"WITH s, t "
                    f"MERGE (s)-[r:{rel}]->(t) "
                    f"SET r.source = 'suin', r.texto = $txt",
                    sid=source_id, tid=target_id, tn=target_name, txt=target_text,
                )

    def _export_sentencia(self, session, row: dict[str, Any]):
        """Creates a Node for a Sentencia and its relationships to Norms."""
        sent_id = row.get("canonical_id") or f"co:sentencia:{row.get('corte', 'cc')}:{row['numero']}:{row.get('anio', '?')}"
        tipo = row["tipo"]
        corte = row.get("corte")
        magistrado = row.get("magistrado_ponente")
        suin_id = row.get("suin_id")

        # Create Sentencia Node
        session.run(
            "MERGE (s:Sentencia {id: $id}) "
            "SET s.corte = $corte, s.magistrado = $magistrado, "
            "    s.numero = $numero, s.anio = $anio, s.suin_id = $suin_id",
            id=sent_id, corte=corte, magistrado=magistrado,
            numero=row.get("numero"), anio=row.get("anio"), suin_id=suin_id,
        )

        # Create Magistrado node and relationship
        if magistrado and magistrado.strip():
            mp_clean = magistrado.strip()[:60]
            mp_id = f"mag_{re.sub(r'[^a-z0-9]', '_', mp_clean.lower())[:40]}"
            session.run(
                "MERGE (m:Magistrado {id: $mp_id}) "
                "SET m.nombre = $nombre "
                "WITH m "
                "MATCH (s:Sentencia {id: $sent_id}) "
                "MERGE (m)-[:FUE_PONENTE_DE]->(s)",
                mp_id=mp_id, nombre=mp_clean, sent_id=sent_id,
            )

        # Load parsed text/resuelve and create citation relationships
        if suin_id and row.get("scrape_status") == "done":
            source = "corte_constitucional"
            if corte == "csj":
                source = "csj"
            elif corte == "ce":
                source = "consejo_estado"

            parsed = self.cache.load_parsed(source, tipo, suin_id)
            if parsed:
                # Add text details
                session.run(
                    "MATCH (s:Sentencia {id: $id}) "
                    "SET s.resuelve = $resuelve, s.hechos = $hechos",
                    id=sent_id,
                    resuelve=parsed.get("resuelve", "")[:2000],
                    hechos=parsed.get("hechos", "")[:2000],
                )

                # ── Export citation edges (CITA_A) ──────────────────────────
                self._export_citations(session, sent_id, parsed)

    def _export_citations(self, session, source_id: str, parsed: dict[str, Any]):
        """Create CITA_A edges from the citaciones list in parsed data.

        Reads the 'citaciones' field from parsed.json and creates edges
        to target Norma/Sentencia nodes (MERGEing them if they don't exist).
        """
        citaciones = parsed.get("citaciones", [])
        if not citaciones:
            return

        for cita_raw in citaciones:
            if not cita_raw or not isinstance(cita_raw, str):
                continue

            cita_clean = cita_raw.strip()
            if not cita_clean:
                continue

            # Try to parse the citation into a canonical-like ID
            parsed_cita = self._citation_to_node_id(cita_clean)
            if not parsed_cita:
                continue
            target_id, target_name = parsed_cita

            # Determine target label
            if "sentencia" in cita_clean.lower():
                # It's a sentencia citation
                session.run(
                    "MERGE (t:Sentencia {id: $target_id}) "
                    "ON CREATE SET t.nombre = $nombre "
                    "WITH t "
                    "MATCH (s {id: $source_id}) "
                    "MERGE (s)-[:CITA_A {texto: $texto}]->(t)",
                    target_id=target_id, nombre=target_name,
                    source_id=source_id, texto=cita_clean,
                )
            else:
                # It's a norm citation (ley, decreto, etc.)
                session.run(
                    "MERGE (t:Norma {id: $target_id}) "
                    "ON CREATE SET t.nombre = $nombre "
                    "WITH t "
                    "MATCH (s {id: $source_id}) "
                    "MERGE (s)-[:CITA_A {texto: $texto}]->(t)",
                    target_id=target_id, nombre=target_name,
                    source_id=source_id, texto=cita_clean,
                )

    # ── Similarity edges (embeddings) ───────────────────────────────────

    def _representative_text(self, row: dict[str, Any]) -> str:
        """Short text that captures what a norm/sentencia is about, to embed."""
        tipo = row.get("tipo")
        suin_id = row.get("suin_id")
        label = f"{tipo} {row.get('numero')} de {row.get('anio')}"
        materia = row.get("materia") or ""

        if tipo == "SENTENCIA":
            corte = row.get("corte")
            source = "corte_constitucional"
            if corte == "csj":
                source = "csj"
            elif corte == "ce":
                source = "consejo_estado"
            parsed = self.cache.load_parsed(source, tipo, suin_id) if suin_id else None
            if parsed:
                txt = (
                    parsed.get("consideraciones")
                    or parsed.get("resuelve")
                    or parsed.get("hechos")
                    or ""
                )[:1500]
                return f"{label}. {txt}".strip()
            return label

        parsed = self.cache.load_parsed("suin", tipo, suin_id) if suin_id else None
        if parsed:
            titles = " · ".join(
                a.get("title") or "" for a in parsed.get("articles", []) if a.get("title")
            )[:600]
            first = (parsed.get("articles") or [{}])[0].get("text", "")[:600]
            return f"{label}. {materia}. {titles}. {first}".strip()
        return f"{label}. {materia}".strip()

    def _export_similarity_edges(
        self, session, top_k: int = 5, threshold: float = 0.55
    ) -> None:
        """Create SIMILAR_A edges between thematically related documents.

        Uses bge-m3 embeddings + cosine kNN so the graph is interconnected
        beyond explicit citations/affectations. Edges carry a `score` and are
        tagged so they're distinguishable from authoritative edges.
        """
        rows = self.db.conn.execute(
            "SELECT * FROM catalog WHERE scrape_status = 'done' AND canonical_id IS NOT NULL"
        ).fetchall()
        items: list[tuple[str, str]] = []
        for r in rows:
            row = dict(r)
            rep = self._representative_text(row)
            if rep and rep.strip():
                items.append((row["canonical_id"], rep))
        if len(items) < 3:
            return

        try:
            import numpy as np
            from fastembed import TextEmbedding
        except ImportError:
            logger.warning("fastembed/numpy no disponible; se omiten aristas SIMILAR_A")
            return

        logger.info(
            "Computando aristas de similitud para %d nodos (%s)...",
            len(items), self.settings.embedding_model_dense,
        )
        model = TextEmbedding(self.settings.embedding_model_dense)
        vecs = np.array(list(model.embed([t for _, t in items])), dtype="float32")
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        sims = vecs @ vecs.T

        edges = 0
        seen_pairs: set[tuple[str, str]] = set()
        for i in range(len(items)):
            order = sims[i].argsort()[::-1]
            added = 0
            for j in order:
                if j == i:
                    continue
                score = float(sims[i][j])
                if score < threshold or added >= top_k:
                    break
                added += 1
                a, b = items[i][0], items[j][0]
                pair = (a, b) if a < b else (b, a)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                session.run(
                    "MATCH (x {id: $a}), (y {id: $b}) "
                    "MERGE (x)-[r:SIMILAR_A]-(y) "
                    "SET r.score = $s, r.source = 'embedding'",
                    a=a, b=b, s=round(score, 3),
                )
                edges += 1
        logger.info("Creadas %d aristas SIMILAR_A", edges)

    @staticmethod
    def _citation_to_node_id(cita: str) -> tuple[str, str] | None:
        """Convert a citation string to a graph node ID and macro name.

        Returns a tuple: (node_id, macro_name)
        """
        cita_lower = cita.lower().strip()

        # Artículo X de la Ley Y de Z
        m = re.match(
            r"art[ií]culo\s+(\d+[a-z]?)\s+de\s+la\s+ley\s+(\d+)\s+de\s+(\d{4})",
            cita_lower,
        )
        if m:
            return (f"co:ley:{m.group(2)}:{m.group(3)}", f"Ley {m.group(2)} de {m.group(3)}")

        # Artículo X del Decreto Y de Z
        m = re.match(
            r"art[ií]culo\s+(\d+[a-z]?)\s+del\s+decreto\s+(?:ley\s+)?(\d+)\s+de\s+(\d{4})",
            cita_lower,
        )
        if m:
            return (f"co:decreto:{m.group(2)}:{m.group(3)}", f"Decreto {m.group(2)} de {m.group(3)}")

        # Sentencia X-NNN de YYYY
        m = re.match(
            r"sentencia\s+([a-z]{1,2})-(\d+)\s+de\s+(\d{4})",
            cita_lower,
        )
        if m:
            prefix = m.group(1)
            sala_map = {"c": "plena", "t": "revision", "su": "plena", "a": "auto"}
            sala = sala_map.get(prefix, "plena")
            return (f"co:sentencia:cc:{sala}:{prefix}-{m.group(2)}:{m.group(3)}", f"Sentencia {prefix.upper()}-{m.group(2)} de {m.group(3)}")

        # Ley X de Y
        m = re.match(r"ley\s+(\d+)\s+de\s+(\d{4})", cita_lower)
        if m:
            return (f"co:ley:{m.group(1)}:{m.group(2)}", f"Ley {m.group(1)} de {m.group(2)}")

        # Decreto X de Y
        m = re.match(r"decreto\s+(?:ley\s+)?(\d+)\s+de\s+(\d{4})", cita_lower)
        if m:
            return (f"co:decreto:{m.group(1)}:{m.group(2)}", f"Decreto {m.group(1)} de {m.group(2)}")

        # Resolución X de Y
        m = re.match(r"resoluci[oó]n\s+(\d+)\s+de\s+(\d{4})", cita_lower)
        if m:
            return (f"co:resolucion:{m.group(1)}:{m.group(2)}", f"Resolución {m.group(1)} de {m.group(2)}")

        # Acto Legislativo X de Y
        m = re.match(r"acto\s+legislativo\s+(\d+)\s+de\s+(\d{4})", cita_lower)
        if m:
            return (f"co:acto_legislativo:{m.group(1)}:{m.group(2)}", f"Acto Legislativo {m.group(1)} de {m.group(2)}")

        # Artículo X de la Constitución
        m = re.match(r"art[ií]culo\s+(\d+)\s+de\s+la\s+constituci[oó]n", cita_lower)
        if m:
            return (f"co:constitucion:1991", "Constitución Política de 1991")

        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    db = Database(settings.catalog_db_path)
    cache = ProvenanceCache(settings)

    exporter = Neo4jExporter(settings, db, cache)
    try:
        exporter.export_all()
        logger.info("Neo4j export script ready.")
    finally:
        exporter.close()
