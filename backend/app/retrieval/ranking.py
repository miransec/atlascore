"""
Reranker abstraction for Phase 2B.

A Reranker interface is defined so Phase 2C can inject a cross-encoder or
LLM reranker without changing the retrieval pipeline.

In Phase 2B only IdentityReranker is provided — it returns results in the
order they arrive (already ranked by hybrid RRF score).

No external API calls are made in this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.retrieval.schemas import RetrievalResult


class Reranker(ABC):
    """Interface for a retrieval reranker."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int,
    ) -> list[RetrievalResult]:
        """
        Reorder results for a given query.

        Must return a list of at most top_k RetrievalResult objects.
        Must not modify result objects in place.
        Must not make network calls that could fail silently and degrade ranking.

        Parameters
        ----------
        query:    The normalised search query (plain text; do not execute).
        results:  Candidate results pre-ranked by hybrid RRF.
        top_k:    Maximum number of results to return.

        Returns
        -------
        Reordered list of RetrievalResult (≤ top_k items).
        """


class IdentityReranker(Reranker):
    """
    A no-op reranker that returns results in the order they were received.

    Used in Phase 2B where hybrid RRF provides the final ranking without
    an additional reranking step.  Also used as the default in tests.
    """

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int,
    ) -> list[RetrievalResult]:
        return results[:top_k]
