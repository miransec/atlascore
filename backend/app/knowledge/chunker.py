"""
Deterministic text chunker for the knowledge ingestion pipeline.

Splits plain text into overlapping fixed-size chunks.  The output is
fully deterministic: identical input text with identical settings always
produces identical chunks in identical order.

Chunking strategy:
  - Split at word boundaries (whitespace).  Never cut in the middle of a word.
  - Each chunk is at most `chunk_size` words.
  - Consecutive chunks overlap by `overlap` words.
  - Empty or whitespace-only input produces zero chunks.
  - The last chunk includes all remaining words (may be shorter than chunk_size).

Word-token approximation:
  - "Tokens" here are whitespace-delimited words.  This is an approximation
    that avoids a tokeniser dependency.  It will slightly undercount for
    languages without whitespace delimiters, but is exact for English/EU
    languages, which are the Phase 2A target.

SECURITY:
  - The chunker treats input text as plain data.  It does not interpret,
    execute, or otherwise process content semantics.
  - SHA-256 is used for content identity (not password hashing).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A single text chunk produced by the chunker."""

    chunk_index: int
    """0-based stable ordinal within the parent document version."""

    chunk_text: str
    """The text content of this chunk."""

    content_sha256: str
    """Hex SHA-256 of UTF-8 encoded chunk_text — for integrity/deduplication."""

    token_count: int
    """Approximate word count (whitespace-delimited words)."""


class ChunkerConfigError(ValueError):
    """Raised when chunker configuration is invalid."""


class TextChunker:
    """
    Configurable, deterministic word-boundary text chunker.

    Parameters
    ----------
    chunk_size:    Maximum number of words per chunk (must be ≥ 1).
    overlap:       Number of words shared between consecutive chunks (must be ≥ 0
                   and strictly less than chunk_size).
    """

    def __init__(self, *, chunk_size: int, overlap: int) -> None:
        if chunk_size <= 0:
            raise ChunkerConfigError(f"chunk_size must be positive; got {chunk_size}")
        if overlap < 0:
            raise ChunkerConfigError(f"overlap must be non-negative; got {overlap}")
        if overlap >= chunk_size:
            raise ChunkerConfigError(
                f"overlap ({overlap}) must be strictly less than chunk_size ({chunk_size})"
            )
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._step = chunk_size - overlap

    def chunk(self, text: str) -> list[Chunk]:
        """
        Split text into chunks and return them in stable order.

        Returns an empty list for blank/whitespace-only input.
        """
        words = text.split()
        if not words:
            return []

        chunks: list[Chunk] = []
        idx = 0
        start = 0

        while start < len(words):
            end = min(start + self._chunk_size, len(words))
            chunk_words = words[start:end]
            chunk_text = " ".join(chunk_words)
            sha = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    chunk_index=idx,
                    chunk_text=chunk_text,
                    content_sha256=sha,
                    token_count=len(chunk_words),
                )
            )
            idx += 1
            if end == len(words):
                break
            start += self._step

        return chunks
