"""Búsqueda semántica híbrida sobre Qdrant — la "herramienta 1" del deep-agent.

Combina recuperación **densa** (jina-es, significado) y **dispersa** (BM25,
coincidencia léxica exacta de números/artículos) con fusión RRF, y permite
filtrar por vigencia/tipo/año para que el LLM no cite norma derogada.

El resultado de cada hit trae ``canonical_id`` + ``estado_vigencia`` para que el
agente pueda citar con precisión y fundamentar (grounding). Este módulo es el
núcleo reutilizable detrás de ``/api/search`` y del futuro servidor MCP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from scrapper_leyes.config import Settings
from scrapper_leyes.export_vector import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    """Un resultado de búsqueda, listo para que el agente lo cite."""

    canonical_id: str
    norm_canonical_id: str
    titulo: str | None
    section: str | None
    tipo: str | None
    numero: str | None
    anio: str | None
    estado_vigencia: str | None
    derogado: bool
    suin_id: str | None
    score: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "norm_canonical_id": self.norm_canonical_id,
            "titulo": self.titulo,
            "section": self.section,
            "tipo": self.tipo,
            "numero": self.numero,
            "anio": self.anio,
            "estado_vigencia": self.estado_vigencia,
            "derogado": self.derogado,
            "suin_id": self.suin_id,
            "score": round(self.score, 4),
            "text": self.text,
        }


class SemanticSearcher:
    """Carga perezosa de modelos + cliente Qdrant; búsqueda híbrida con filtros."""

    def __init__(self, settings: Settings | None = None, client: QdrantClient | None = None):
        self.settings = settings or Settings()
        self.collection = self.settings.qdrant_collection
        self._client = client
        self._dense = None
        self._sparse = None

    # ── lazy resources ──────────────────────────────────────────────────
    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if self.settings.qdrant_url:
                self._client = QdrantClient(
                    url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key
                )
            else:
                self._client = QdrantClient(
                    host=self.settings.qdrant_host,
                    port=self.settings.qdrant_port,
                    api_key=self.settings.qdrant_api_key,
                )
        return self._client

    def _load_models(self) -> None:
        if self._dense is None or self._sparse is None:
            from fastembed import SparseTextEmbedding

            from scrapper_leyes.embeddings import get_dense_embedder

            logger.info("Cargando modelos de embedding para búsqueda...")
            # Dense via the SAME pluggable backend used at index time, or the
            # query/document spaces would not match. Sparse BM25 stays local.
            self._dense = get_dense_embedder(self.settings)
            self._sparse = SparseTextEmbedding(self.settings.embedding_model_sparse)

    # ── filtros ─────────────────────────────────────────────────────────
    @staticmethod
    def _build_filter(
        tipo: str | None,
        anio: str | None,
        estado_vigencia: str | None,
        excluir_derogadas: bool,
    ) -> models.Filter | None:
        must: list[models.FieldCondition] = []
        must_not: list[models.FieldCondition] = []
        if tipo:
            must.append(models.FieldCondition(key="tipo", match=models.MatchValue(value=tipo)))
        if anio:
            must.append(models.FieldCondition(key="anio", match=models.MatchValue(value=anio)))
        if estado_vigencia:
            must.append(
                models.FieldCondition(
                    key="estado_vigencia", match=models.MatchValue(value=estado_vigencia)
                )
            )
        if excluir_derogadas:
            must_not.append(
                models.FieldCondition(key="derogado", match=models.MatchValue(value=True))
            )
        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)

    # ── búsqueda ────────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tipo: str | None = None,
        anio: str | None = None,
        estado_vigencia: str | None = None,
        excluir_derogadas: bool = False,
    ) -> list[SearchHit]:
        """Búsqueda híbrida dense+sparse con fusión RRF y filtros de payload."""
        if not self.client.collection_exists(self.collection):
            raise CollectionMissing(self.collection)

        self._load_models()
        dense_vec = self._dense.embed_query(query)
        sparse = next(iter(self._sparse.embed([query])))
        q_filter = self._build_filter(tipo, anio, estado_vigencia, excluir_derogadas)

        prefetch = [
            models.Prefetch(
                query=dense_vec,
                using=DENSE_VECTOR_NAME,
                filter=q_filter,
                limit=max(limit * 4, 20),
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse.indices.tolist(), values=sparse.values.tolist()
                ),
                using=SPARSE_VECTOR_NAME,
                filter=q_filter,
                limit=max(limit * 4, 20),
            ),
        ]
        result = self.client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return [_hit_from_point(p) for p in result.points]


class CollectionMissing(RuntimeError):
    """La colección de vectores no existe todavía (falta correr export vector)."""

    def __init__(self, name: str):
        super().__init__(
            f"La colección '{name}' no existe en Qdrant. "
            f"Corre `scrapper-leyes export vector` para poblar el índice."
        )
        self.collection = name


def _hit_from_point(p: Any) -> SearchHit:
    pl = p.payload or {}
    return SearchHit(
        canonical_id=pl.get("canonical_id", ""),
        norm_canonical_id=pl.get("norm_canonical_id", ""),
        titulo=pl.get("titulo"),
        section=pl.get("section"),
        tipo=pl.get("tipo"),
        numero=pl.get("numero"),
        anio=str(pl.get("anio")) if pl.get("anio") is not None else None,
        estado_vigencia=pl.get("estado_vigencia"),
        derogado=bool(pl.get("derogado", False)),
        suin_id=pl.get("suin_id"),
        score=float(p.score) if p.score is not None else 0.0,
        text=pl.get("text", ""),
    )


@lru_cache(maxsize=1)
def get_searcher() -> SemanticSearcher:
    """Singleton perezoso — comparte modelos/cliente entre requests."""
    return SemanticSearcher()
