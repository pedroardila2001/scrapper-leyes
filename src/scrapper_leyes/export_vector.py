"""Export parsed norms into Qdrant for hybrid (dense + sparse) retrieval.

This is the indexer behind the deep-agent's semantic-search tool. It turns the
file-cache `parsed.json` into vigencia-annotated chunks (see `chunking.py`) and
upserts them into Qdrant with:

  * **Dense** vectors from a multilingual model (bge-m3 by default) — Spanish
    legal text, not the English-only model the prototype shipped with.
  * **Real sparse** vectors from a BM25 model — replacing the hardcoded
    `SparseVector(indices=[1, 2], ...)` placeholder, so lexical recall of exact
    article/number references actually works.
  * **Rich payload** including ``canonical_id`` and vigencia flags, so the agent
    can cite precisely and filter out derogated law.

Re-running is idempotent: deterministic point IDs mean an article that changed
is updated in place instead of duplicated, and the collection is only recreated
when ``--recreate`` is passed.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator

from qdrant_client import QdrantClient
from qdrant_client.http import models

from scrapper_leyes.chunking import Chunk, chunk_document
from scrapper_leyes.config import Settings
from scrapper_leyes.storage.cache import ProvenanceCache
from scrapper_leyes.storage.database import Database

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = ""  # unnamed default vector (kept for backward compat)
SPARSE_VECTOR_NAME = "text"
_BATCH = 128


def _source_for(tipo: str, corte: str | None) -> str:
    """Cache source directory for a norm type / corte."""
    if tipo == "SENTENCIA":
        if corte == "csj":
            return "csj"
        if corte == "ce":
            return "consejo_estado"
        return "corte_constitucional"
    return "suin"


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so Qdrant payload stays compact and filterable."""
    return {k: v for k, v in payload.items() if v is not None}


class VectorStoreExporter:
    """Exports parsed text into Qdrant for hybrid search (dense + sparse)."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        cache: ProvenanceCache,
        collection_name: str | None = None,
        client: QdrantClient | None = None,
    ):
        self.settings = settings
        self.db = db
        self.cache = cache
        self.collection_name = collection_name or settings.qdrant_collection
        self.client = client or self._build_client(settings)

        logger.info("Loading embedding models (dense=%s, sparse=%s)...",
                    settings.embedding_model_dense, settings.embedding_model_sparse)
        from fastembed import SparseTextEmbedding, TextEmbedding

        self.dense_model = TextEmbedding(settings.embedding_model_dense)
        self.sparse_model = SparseTextEmbedding(settings.embedding_model_sparse)
        self._dense_size: int | None = None

    @staticmethod
    def _build_client(settings: Settings) -> QdrantClient:
        if settings.qdrant_url:
            return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        return QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key,
        )

    # ── Collection lifecycle ────────────────────────────────────────────

    def _detect_dense_size(self) -> int:
        if self._dense_size is None:
            vec = next(iter(self.dense_model.embed(["dimension probe"])))
            self._dense_size = len(vec)
        return self._dense_size

    def ensure_collection(self, recreate: bool = False) -> None:
        """Create the hybrid collection if missing (or recreate on demand)."""
        size = self._detect_dense_size()
        exists = self.client.collection_exists(self.collection_name)

        if exists and not recreate:
            logger.info("Collection '%s' exists — upserting incrementally.", self.collection_name)
            return

        if exists and recreate:
            logger.warning("Recreating collection '%s' (existing data dropped).", self.collection_name)
            self.client.delete_collection(self.collection_name)

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=size, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )
            },
        )
        # Payload indexes for the filters the agent will actually use.
        for fld, schema in (
            ("estado_vigencia", models.PayloadSchemaType.KEYWORD),
            ("tipo", models.PayloadSchemaType.KEYWORD),
            ("anio", models.PayloadSchemaType.KEYWORD),
            ("derogado", models.PayloadSchemaType.BOOL),
            ("canonical_id", models.PayloadSchemaType.KEYWORD),
            ("norm_canonical_id", models.PayloadSchemaType.KEYWORD),
        ):
            try:
                self.client.create_payload_index(self.collection_name, fld, schema)
            except Exception as e:  # pragma: no cover - index may pre-exist
                logger.debug("payload index %s: %s", fld, e)

    # ── Chunk iteration ─────────────────────────────────────────────────

    def iter_chunks(self, tipo: str | None = None) -> Iterator[Chunk]:
        """Yield chunks for every scraped norm, optionally filtered by tipo."""
        sql = "SELECT * FROM catalog WHERE scrape_status = 'done'"
        params: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)

        for row in self.db.conn.execute(sql, params).fetchall():
            cat = dict(row)
            suin_id = cat.get("suin_id")
            if not suin_id:
                continue
            source = _source_for(cat["tipo"], cat.get("corte"))
            parsed = self.cache.load_parsed(source, cat["tipo"], suin_id)
            if not parsed:
                continue
            try:
                yield from chunk_document(
                    parsed,
                    cat,
                    max_chars=self.settings.chunk_max_chars,
                    overlap=self.settings.chunk_overlap_chars,
                )
            except Exception as e:
                logger.error("Chunking failed for %s (%s): %s", suin_id, cat["tipo"], e)

    # ── Embedding + upsert ──────────────────────────────────────────────

    def _points_for(self, chunks: list[Chunk]) -> list[models.PointStruct]:
        texts = [c.text for c in chunks]
        dense = list(self.dense_model.embed(texts))
        sparse = list(self.sparse_model.embed(texts))
        points: list[models.PointStruct] = []
        for chunk, d_vec, s_vec in zip(chunks, dense, sparse):
            points.append(
                models.PointStruct(
                    id=chunk.uid,
                    vector={
                        DENSE_VECTOR_NAME: d_vec.tolist(),
                        SPARSE_VECTOR_NAME: models.SparseVector(
                            indices=s_vec.indices.tolist(),
                            values=s_vec.values.tolist(),
                        ),
                    },
                    payload=_clean_payload(chunk.payload),
                )
            )
        return points

    def export_all(self, tipo: str | None = None, recreate: bool = False) -> int:
        """Embed and upsert all scraped norms. Returns total chunks indexed."""
        self.ensure_collection(recreate=recreate)
        total = 0
        for batch in _batched(self.iter_chunks(tipo), _BATCH):
            points = self._points_for(batch)
            if points:
                self.client.upsert(collection_name=self.collection_name, points=points)
                total += len(points)
                logger.info("Upserted %d chunks (running total: %d)", len(points), total)
        logger.info("Done. %d chunks in '%s'.", total, self.collection_name)
        return total


def _batched(it: Iterable[Chunk], size: int) -> Iterator[list[Chunk]]:
    batch: list[Chunk] = []
    for item in it:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def run_export(
    settings: Settings | None = None,
    tipo: str | None = None,
    recreate: bool = False,
) -> int:
    """Entry point used by the CLI and the ``__main__`` block."""
    settings = settings or Settings()
    db = Database(settings.catalog_db_path)
    cache = ProvenanceCache(settings)
    try:
        exporter = VectorStoreExporter(settings, db, cache)
        return exporter.export_all(tipo=tipo, recreate=recreate)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_export()
