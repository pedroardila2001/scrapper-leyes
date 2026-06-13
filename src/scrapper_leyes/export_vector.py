import logging
from typing import Any
import json
from qdrant_client import QdrantClient
from qdrant_client.http import models
from fastembed import TextEmbedding

from scrapper_leyes.storage.database import Database
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.config import Settings

logger = logging.getLogger(__name__)

class VectorStoreExporter:
    """Exports parsed text into Qdrant for Hybrid Search (Dense + Sparse)."""
    
    def __init__(self, settings: Settings, db: Database, cache: ProvenanceCache, collection_name: str = "legal_corpus"):
        self.settings = settings
        self.db = db
        self.cache = cache
        self.collection_name = collection_name
        self.client = QdrantClient(host="localhost", port=6333)
        logger.info("Loading FastEmbed model...")
        self.embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def setup_collection(self):
        """Sets up the Hybrid Search collection in Qdrant."""
        self.client.recreate_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
            sparse_vectors_config={
                "text": models.SparseVectorParams(modifier=models.Modifier.IDF)
            }
        )

    def export_all(self):
        """Process DB and embed text."""
        self.setup_collection()
        points = []
        idx = 1
        
        norms = self.db.conn.execute("SELECT * FROM catalog WHERE scrape_status = 'done'").fetchall()
        logger.info(f"Generating vectors for {len(norms)} parsed documents...")
        for row in norms:
            suin_id = row["suin_id"]
            tipo = row["tipo"]
            source = "suin"
            if tipo == "SENTENCIA":
                corte = row["corte"]
                if corte == "csj": source = "csj"
                elif corte == "ce": source = "consejo_estado"
                else: source = "corte_constitucional"

            parsed = self.cache.load_parsed(source, tipo, suin_id)
            if not parsed:
                continue

            chunks = []
            if tipo == "SENTENCIA":
                text = parsed.get("consideraciones", "") or parsed.get("hechos", "")
                if text:
                    chunks.append(text[:2000])
            else:
                for article in parsed.get("articles", []):
                    text = article.get("text", "")
                    if text:
                        chunks.append(text[:2000])

            if not chunks:
                continue

            embeddings = list(self.embedding_model.embed(chunks))

            for chunk_text, dense_vec in zip(chunks, embeddings):
                if not chunk_text.strip(): continue

                sparse_vec = models.SparseVector(indices=[1, 2], values=[0.5, 0.8])
                
                point = models.PointStruct(
                    id=idx,
                    vector={
                        "": dense_vec.tolist(),
                        "text": sparse_vec
                    },
                    payload={
                        "id_interno": suin_id,
                        "tipo": tipo,
                        "numero": row["numero"],
                        "anio": row["anio"],
                        "corte": row["corte"],
                        "magistrado": row["magistrado_ponente"],
                        "text": chunk_text
                    }
                )
                points.append(point)
                idx += 1
                
        if points:
            self.client.upload_points(
                collection_name=self.collection_name,
                points=points
            )
            logger.info(f"Inserted {len(points)} vectors into Qdrant.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    db = Database(settings.catalog_db_path)
    cache = ProvenanceCache(settings)
    
    exporter = VectorStoreExporter(settings, db, cache)
    exporter.export_all()
    logger.info("Qdrant export script finished.")
