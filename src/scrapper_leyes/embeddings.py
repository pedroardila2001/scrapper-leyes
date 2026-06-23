"""Dense embedding backends (single source of truth for index + query).

Two backends, selected by ``EMBEDDING_BACKEND``:

  * ``fastembed`` (default) — local fastembed ``TextEmbedding`` (e.g. jina-es).
  * ``openai``   — any OpenAI-compatible ``/v1/embeddings`` server. This is how
                   we offload embedding to a GPU box running vLLM/TEI serving
                   Qwen3-Embedding (over Tailscale, say).

Why an abstraction: the **same** model must embed documents (at index time,
`export_vector`) and queries (at search time, `search.py`), or retrieval breaks.
Both call sites go through ``get_dense_embedder`` so they can never drift.

Document vs query asymmetry: instruction-tuned embedding models (Qwen3-Embedding)
expect a task **instruction prefix on queries only** — documents are embedded
raw. ``embed_query`` applies the prefix; ``embed_documents`` does not.
"""

from __future__ import annotations

import logging
import math
from typing import Protocol

from scrapper_leyes.config import Settings

logger = logging.getLogger(__name__)


def _l2_normalize(vec: list[float]) -> list[float]:
    """Unit-normalize a vector (no-op on the zero vector).

    MRL-truncated embeddings (e.g. Qwen3 at `dimensions=1536`) are not guaranteed
    unit-length; normalizing keeps cosine == dot and makes the dimension switch
    safe regardless of the Qdrant distance metric configured.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class DenseEmbedder(Protocol):
    """Embeds documents and queries into the SAME dense space."""

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# ── fastembed (local) ────────────────────────────────────────────────────────


class FastembedDense:
    """Local fastembed backend. Documents and queries are embedded identically
    (jina-style models are not instruction-tuned)."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        logger.info("Embeddings: fastembed dense '%s'", model_name)
        self._model = TextEmbedding(model_name)
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(next(iter(self._model.embed(["dimension probe"]))))
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([text]))).tolist()


# ── OpenAI-compatible API (vLLM / TEI / LM Studio) ───────────────────────────


class OpenAIDense:
    """Calls an OpenAI-compatible ``/v1/embeddings`` endpoint.

    Supports Matryoshka truncation via the ``dimensions`` request field (Qwen3),
    applies the query instruction prefix to queries only, and L2-normalizes the
    returned vectors so the index and the query live in a consistent space.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int = 0,
        api_key: str = "EMPTY",
        query_instruction: str = "",
        timeout: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim or 0
        self._api_key = api_key or "EMPTY"
        self._query_instruction = query_instruction or ""
        self._timeout = timeout
        logger.info(
            "Embeddings: OpenAI-API dense model='%s' url='%s' dim=%s",
            model, self._base_url, dim or "auto",
        )

    @property
    def dim(self) -> int:
        if not self._dim:
            self._dim = len(self.embed_documents(["dimension probe"])[0])
        return self._dim

    def _post(self, inputs: list[str]) -> list[list[float]]:
        import httpx

        # We do NOT send the OpenAI `dimensions` field: many servers (e.g. vLLM
        # serving Qwen3-Embedding) reject it with 400. Instead we truncate
        # client-side — valid for Matryoshka (MRL) models, where a vector prefix
        # is itself a good embedding — then re-normalize. Same truncation runs
        # for documents and queries, so the spaces stay consistent.
        body = {"model": self._model, "input": inputs}
        resp = httpx.post(
            f"{self._base_url}/embeddings",
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Preserve request order (OpenAI spec returns an "index" per item).
        data.sort(key=lambda d: d.get("index", 0))
        out: list[list[float]] = []
        for d in data:
            vec = d["embedding"]
            if self._dim and len(vec) > self._dim:
                vec = vec[: self._dim]  # MRL prefix
            out.append(_l2_normalize(vec))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._post(texts)

    def embed_query(self, text: str) -> list[float]:
        prompt = f"{self._query_instruction}{text}" if self._query_instruction else text
        return self._post([prompt])[0]


# ── factory ──────────────────────────────────────────────────────────────────


def get_dense_embedder(settings: Settings) -> DenseEmbedder:
    """Build the dense embedder selected by ``settings.embedding_backend``."""
    backend = (settings.embedding_backend or "fastembed").lower()
    if backend in ("openai", "vllm", "tei", "lmstudio"):
        return OpenAIDense(
            base_url=settings.embedding_api_url,
            model=settings.embedding_api_model,
            dim=settings.embedding_dim,
            api_key=settings.embedding_api_key,
            query_instruction=settings.embedding_query_instruction,
        )
    return FastembedDense(settings.embedding_model_dense)
