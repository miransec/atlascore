"""
Embedding provider abstraction for the knowledge ingestion pipeline.

SECURITY:
  - The DeterministicTestEmbeddingProvider makes NO network calls and requires
    NO API keys.  It is safe for use in tests and CI.
  - Real provider implementations (Phase 2B+) must be added as separate classes
    that clearly advertise their network behaviour.
  - Embedding vectors are plain float lists — they must not contain executable
    content or instructions.

Extending:
  - Implement EmbeddingProvider.
  - Register the new provider in EMBEDDING_PROVIDER_REGISTRY.
  - Do NOT store API keys in configuration as JSON values; use environment
    variables / Settings fields validated at startup.
"""

from __future__ import annotations

import hashlib
import math
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingResult:
    """The result of embedding a single text string."""

    model_id: str
    """The identifier of the model that produced this embedding."""

    vector: list[float]
    """The embedding vector.  Length == dimensions."""

    dimensions: int
    """Number of dimensions in the vector."""


class EmbeddingError(Exception):
    """Raised when embedding fails."""


class EmbeddingProvider(ABC):
    """Abstract interface for an embedding provider."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Stable identifier for this model (persisted in the database)."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Number of dimensions produced by this provider."""

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """
        Embed a single text string.

        Parameters
        ----------
        text:   The text to embed.  Must be plain text; the provider
                must not execute or interpret the content.

        Returns
        -------
        EmbeddingResult with a float vector of length self.dimensions.

        Raises
        ------
        EmbeddingError — on any failure.
        """

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """
        Embed a list of texts.

        Default implementation calls embed() sequentially.  Providers may
        override this for true batch efficiency.
        """
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results


class DeterministicTestEmbeddingProvider(EmbeddingProvider):
    """
    A deterministic embedding provider for tests and development.

    Makes NO network calls.  Requires NO API keys.

    The embedding is produced by hashing the UTF-8 text with SHA-256,
    then expanding the hash bytes into a float vector via a seeded
    pseudo-random expansion using successive SHA-256 rounds.  The result is:
      - Deterministic: same text → same vector, always.
      - Distinct: different texts → different vectors (with very high probability).
      - Normalised: L2-norm ≈ 1.0 (unit sphere).

    model_id is "deterministic-test-v1".  This is persisted in the database;
    do not change it without a migration.
    """

    _MODEL_ID = "deterministic-test-v1"

    def __init__(self, dimensions: int = 1536) -> None:
        if dimensions <= 0:
            raise ValueError(f"dimensions must be positive; got {dimensions}")
        self._dimensions = dimensions

    @property
    def model_id(self) -> str:
        return self._MODEL_ID

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> EmbeddingResult:
        """Embed text deterministically without any I/O."""
        vector = self._make_vector(text)
        return EmbeddingResult(
            model_id=self._MODEL_ID,
            vector=vector,
            dimensions=self._dimensions,
        )

    def _make_vector(self, text: str) -> list[float]:
        """
        Expand text into a float vector of length self._dimensions.

        Algorithm:
          1. Hash the text to 32 seed bytes via SHA-256.
          2. Repeatedly SHA-256 the previous block to fill the needed bytes.
          3. Interpret each 4-byte little-endian chunk as a float in [-1, 1].
          4. L2-normalise the resulting vector.
        """
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw_bytes = bytearray()
        block = seed
        # Each float uses 4 bytes; we need dimensions * 4 bytes.
        needed = self._dimensions * 4
        while len(raw_bytes) < needed:
            raw_bytes.extend(block)
            block = hashlib.sha256(block).digest()

        raw_bytes = raw_bytes[:needed]
        # Unpack as unsigned ints and normalise to [-1, 1].
        n_floats = self._dimensions
        ints = struct.unpack(f"<{n_floats}I", bytes(raw_bytes))
        max_uint32 = 2**32 - 1
        floats = [2.0 * (x / max_uint32) - 1.0 for x in ints]

        # L2 normalise.
        norm = math.sqrt(sum(f * f for f in floats))
        if norm == 0.0:
            # Pathological: return a unit vector along the first axis.
            floats = [1.0] + [0.0] * (n_floats - 1)
        else:
            floats = [f / norm for f in floats]

        return floats


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def build_embedding_provider(model_id: str, dimensions: int) -> EmbeddingProvider:
    """
    Build an EmbeddingProvider from configuration.

    Phase 2B providers:
    - "mock" / "test" / "deterministic-test-v1" — DeterministicTestEmbeddingProvider
      (no network calls; suitable for development and CI).

    Phase 2C will add production providers (OpenAI, Cohere, etc.) behind
    API keys.  Add them here as additional branches keyed on model_id.

    Raises ValueError for unknown model_id strings.
    """
    if model_id in {"mock", "deterministic-test-v1", "test"}:
        return DeterministicTestEmbeddingProvider(dimensions=dimensions)
    raise ValueError(
        f"Unknown embedding provider: {model_id!r}. "
        "Supported values: 'mock', 'test', 'deterministic-test-v1'."
    )
