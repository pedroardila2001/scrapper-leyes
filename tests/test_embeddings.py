"""Tests for the dense embedding abstraction (pure logic; no network/models)."""

from __future__ import annotations

import math

from scrapper_leyes.config import Settings
from scrapper_leyes.embeddings import OpenAIDense, _l2_normalize, get_dense_embedder


def test_l2_normalize_unit_length():
    v = _l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)
    assert math.isclose(v[0], 0.6) and math.isclose(v[1], 0.8)


def test_l2_normalize_zero_vector_is_noop():
    assert _l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def test_factory_selects_openai_backend():
    s = Settings(
        embedding_backend="openai",
        embedding_api_url="http://pc:8001/v1",
        embedding_api_model="Qwen/Qwen3-Embedding-4B",
        embedding_dim=1536,
    )
    emb = get_dense_embedder(s)
    assert isinstance(emb, OpenAIDense)
    assert emb.dim == 1536


def test_query_gets_instruction_prefix_documents_do_not():
    emb = OpenAIDense(
        base_url="http://pc:8001/v1",
        model="m",
        dim=4,
        query_instruction="Instruct: tarea\nQuery: ",
    )
    captured = {}

    def fake_post(inputs):
        captured["inputs"] = inputs
        return [[0.0, 0.0, 0.0, 1.0] for _ in inputs]

    emb._post = fake_post  # type: ignore[assignment]

    emb.embed_query("¿qué es el habeas data?")
    assert captured["inputs"] == ["Instruct: tarea\nQuery: ¿qué es el habeas data?"]

    emb.embed_documents(["artículo 15 de la Constitución"])
    assert captured["inputs"] == ["artículo 15 de la Constitución"]  # no prefix


def test_dim_is_reported_from_config_without_probe():
    emb = OpenAIDense(base_url="u", model="m", dim=1536)
    assert emb.dim == 1536  # no network call needed when dim is set


def test_post_truncates_to_dim_and_normalizes():
    # MRL prefix truncation + L2-normalize, client-side (no `dimensions` field).
    import sys
    import types

    fake = types.ModuleType("httpx")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [3.0, 4.0, 100.0, 100.0]}]}

    fake.post = lambda *a, **k: FakeResp()  # type: ignore[attr-defined]
    sys.modules["httpx"] = fake
    try:
        emb = OpenAIDense(base_url="u", model="m", dim=2)
        v = emb.embed_documents(["x"])[0]
        assert len(v) == 2                       # truncated 4 → 2 (MRL prefix)
        assert math.isclose(v[0], 0.6) and math.isclose(v[1], 0.8)  # renormalized
    finally:
        del sys.modules["httpx"]
