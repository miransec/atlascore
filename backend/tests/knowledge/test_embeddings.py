"""
Test suite for EmbeddingProvider abstraction.

Tests:
  EM-01  DeterministicTestEmbeddingProvider.embed returns correct model_id
  EM-02  embed returns vector of correct length
  EM-03  embed is deterministic (same text → same vector)
  EM-04  embed produces different vectors for different texts
  EM-05  embed_batch processes all texts
  EM-06  Vector is approximately L2-normalised (unit norm)
  EM-07  All vector elements are floats in [-1, 1]
  EM-08  model_id attribute is stable ("deterministic-test-v1")
  EM-09  Invalid dimensions raises ValueError
  EM-10  build_embedding_provider returns DeterministicTestEmbeddingProvider for "mock"
  EM-11  build_embedding_provider raises ValueError for unknown provider
"""

from __future__ import annotations

import math

import pytest

from app.knowledge.embeddings import (
    DeterministicTestEmbeddingProvider,
    EmbeddingResult,
    build_embedding_provider,
)


@pytest.fixture()
def provider() -> DeterministicTestEmbeddingProvider:
    return DeterministicTestEmbeddingProvider(dimensions=64)


# ---- EM-01: model_id ---------------------------------------------------


@pytest.mark.asyncio()
async def test_em01_model_id(provider: DeterministicTestEmbeddingProvider) -> None:
    result = await provider.embed("hello")
    assert result.model_id == "deterministic-test-v1"


# ---- EM-02: vector length ----------------------------------------------


@pytest.mark.asyncio()
async def test_em02_vector_length(provider: DeterministicTestEmbeddingProvider) -> None:
    result = await provider.embed("hello")
    assert len(result.vector) == 64
    assert result.dimensions == 64


# ---- EM-03: determinism -----------------------------------------------


@pytest.mark.asyncio()
async def test_em03_deterministic(provider: DeterministicTestEmbeddingProvider) -> None:
    text = "deterministic test input"
    r1 = await provider.embed(text)
    r2 = await provider.embed(text)
    assert r1.vector == r2.vector


# ---- EM-04: different texts → different vectors ------------------------


@pytest.mark.asyncio()
async def test_em04_distinct_vectors(provider: DeterministicTestEmbeddingProvider) -> None:
    r1 = await provider.embed("hello world")
    r2 = await provider.embed("goodbye world")
    assert r1.vector != r2.vector


# ---- EM-05: embed_batch processes all texts ----------------------------


@pytest.mark.asyncio()
async def test_em05_batch(provider: DeterministicTestEmbeddingProvider) -> None:
    texts = ["one", "two", "three", "four"]
    results = await provider.embed_batch(texts)
    assert len(results) == 4
    for r in results:
        assert isinstance(r, EmbeddingResult)
        assert len(r.vector) == 64


# ---- EM-06: approximately unit L2 norm ---------------------------------


@pytest.mark.asyncio()
async def test_em06_unit_norm(provider: DeterministicTestEmbeddingProvider) -> None:
    result = await provider.embed("normalisation test")
    norm = math.sqrt(sum(x * x for x in result.vector))
    assert abs(norm - 1.0) < 1e-6


# ---- EM-07: all elements in [-1, 1] ------------------------------------


@pytest.mark.asyncio()
async def test_em07_elements_in_range(provider: DeterministicTestEmbeddingProvider) -> None:
    result = await provider.embed("range check")
    for x in result.vector:
        assert -1.0 <= x <= 1.0


# ---- EM-08: model_id attribute ----------------------------------------


def test_em08_model_id_attribute(provider: DeterministicTestEmbeddingProvider) -> None:
    assert provider.model_id == "deterministic-test-v1"


# ---- EM-09: invalid dimensions raises ValueError -----------------------


def test_em09_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions must be positive"):
        DeterministicTestEmbeddingProvider(dimensions=0)
    with pytest.raises(ValueError, match="dimensions must be positive"):
        DeterministicTestEmbeddingProvider(dimensions=-1)


# ---- EM-10: build_embedding_provider returns correct provider ----------


def test_em10_build_mock() -> None:
    provider = build_embedding_provider("mock", 32)
    assert isinstance(provider, DeterministicTestEmbeddingProvider)
    assert provider.dimensions == 32


# ---- EM-11: build_embedding_provider raises for unknown ----------------


def test_em11_build_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedding_provider("openai-text-embedding-3-large", 1536)
