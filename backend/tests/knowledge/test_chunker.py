"""
Test suite for TextChunker.

Tests:
  CH-01  Basic chunk count for short text (no overflow)
  CH-02  Chunk size is not exceeded
  CH-03  Overlap produces correct shared words between adjacent chunks
  CH-04  Same input always produces same chunks (determinism)
  CH-05  Empty text produces zero chunks
  CH-06  Whitespace-only text produces zero chunks
  CH-07  chunk_index is 0-based and contiguous
  CH-08  content_sha256 matches SHA-256 of chunk_text
  CH-09  Invalid configuration raises ChunkerConfigError
  CH-10  Single-word text produces one chunk
  CH-11  Text shorter than chunk_size produces one chunk
  CH-12  Last chunk contains all remaining words (may be shorter)
  CH-13  token_count matches actual word count of chunk_text
"""

from __future__ import annotations

import hashlib

import pytest

from app.knowledge.chunker import ChunkerConfigError, TextChunker

# ---- CH-01: basic chunk count ------------------------------------------


def test_ch01_basic_chunk_count() -> None:
    chunker = TextChunker(chunk_size=3, overlap=0)
    # 9 words, 3 per chunk, 0 overlap → 3 chunks
    text = "one two three four five six seven eight nine"
    chunks = chunker.chunk(text)
    assert len(chunks) == 3


# ---- CH-02: chunk size not exceeded -------------------------------------


def test_ch02_chunk_size_not_exceeded() -> None:
    chunker = TextChunker(chunk_size=4, overlap=1)
    text = " ".join([f"w{i}" for i in range(20)])
    chunks = chunker.chunk(text)
    for chunk in chunks:
        assert chunk.token_count <= 4


# ---- CH-03: overlap produces shared words -------------------------------


def test_ch03_overlap_shared_words() -> None:
    chunker = TextChunker(chunk_size=4, overlap=2)
    text = "a b c d e f g h"
    chunks = chunker.chunk(text)
    # chunk 0: a b c d
    # chunk 1 (step=2): c d e f
    # chunk 2: e f g h
    words0 = chunks[0].chunk_text.split()
    words1 = chunks[1].chunk_text.split()
    # Last 2 words of chunk 0 == first 2 words of chunk 1
    assert words0[-2:] == words1[:2]


# ---- CH-04: determinism ------------------------------------------------


def test_ch04_deterministic() -> None:
    chunker = TextChunker(chunk_size=5, overlap=1)
    text = "The quick brown fox jumps over the lazy dog"
    chunks1 = chunker.chunk(text)
    chunks2 = chunker.chunk(text)
    assert [(c.chunk_index, c.chunk_text) for c in chunks1] == [
        (c.chunk_index, c.chunk_text) for c in chunks2
    ]


# ---- CH-05: empty text produces no chunks -------------------------------


def test_ch05_empty_text() -> None:
    chunker = TextChunker(chunk_size=5, overlap=1)
    assert chunker.chunk("") == []


# ---- CH-06: whitespace-only text produces no chunks --------------------


def test_ch06_whitespace_only() -> None:
    chunker = TextChunker(chunk_size=5, overlap=0)
    assert chunker.chunk("   \t\n  ") == []


# ---- CH-07: chunk_index is 0-based and contiguous ----------------------


def test_ch07_chunk_index_contiguous() -> None:
    chunker = TextChunker(chunk_size=3, overlap=1)
    text = " ".join([f"w{i}" for i in range(15)])
    chunks = chunker.chunk(text)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


# ---- CH-08: content_sha256 matches SHA-256 of chunk_text ---------------


def test_ch08_sha256_matches() -> None:
    chunker = TextChunker(chunk_size=5, overlap=2)
    text = "alpha beta gamma delta epsilon zeta eta"
    chunks = chunker.chunk(text)
    for chunk in chunks:
        expected = hashlib.sha256(chunk.chunk_text.encode("utf-8")).hexdigest()
        assert chunk.content_sha256 == expected


# ---- CH-09: invalid configuration raises ChunkerConfigError ------------


@pytest.mark.parametrize(
    "chunk_size,overlap",
    [
        (0, 0),  # chunk_size <= 0
        (-1, 0),  # chunk_size <= 0
        (5, 5),  # overlap >= chunk_size
        (5, 6),  # overlap > chunk_size
        (5, -1),  # overlap < 0
    ],
)
def test_ch09_invalid_config(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ChunkerConfigError):
        TextChunker(chunk_size=chunk_size, overlap=overlap)


# ---- CH-10: single word text produces one chunk ------------------------


def test_ch10_single_word() -> None:
    chunker = TextChunker(chunk_size=10, overlap=2)
    chunks = chunker.chunk("hello")
    assert len(chunks) == 1
    assert chunks[0].chunk_text == "hello"
    assert chunks[0].chunk_index == 0


# ---- CH-11: text shorter than chunk_size → one chunk -------------------


def test_ch11_short_text_one_chunk() -> None:
    chunker = TextChunker(chunk_size=100, overlap=10)
    text = "short sentence here"
    chunks = chunker.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == text.strip()


# ---- CH-12: last chunk contains remaining words ------------------------


def test_ch12_last_chunk_contains_remainder() -> None:
    chunker = TextChunker(chunk_size=4, overlap=0)
    # 10 words: 2 full chunks of 4, last chunk of 2
    text = "a b c d e f g h i j"
    chunks = chunker.chunk(text)
    last = chunks[-1]
    assert "i" in last.chunk_text
    assert "j" in last.chunk_text


# ---- CH-13: token_count matches word count of chunk_text ---------------


def test_ch13_token_count_matches_words() -> None:
    chunker = TextChunker(chunk_size=5, overlap=1)
    text = " ".join([f"word{i}" for i in range(20)])
    chunks = chunker.chunk(text)
    for chunk in chunks:
        assert chunk.token_count == len(chunk.chunk_text.split())
