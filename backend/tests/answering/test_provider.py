"""
Unit tests for app.answering.provider.

Tests cover:
  - DeterministicTestAnswerProvider: generates answer from first evidence item
  - DeterministicTestAnswerProvider: cites all evidence items
  - DeterministicTestAnswerProvider: raises AnswerProviderError on empty packet
  - build_answer_provider factory
  - AnswerProvider ABC enforcement
"""

from __future__ import annotations

import asyncio

import pytest

from app.answering.provider import (
    AnswerProviderError,
    DeterministicTestAnswerProvider,
    ProviderAnswer,
    build_answer_provider,
)
from tests.answering.conftest import make_evidence_item, make_packet


class TestDeterministicTestAnswerProvider:
    def setup_method(self) -> None:
        self.provider = DeterministicTestAnswerProvider()

    def test_provider_id(self) -> None:
        assert self.provider.provider_id == "deterministic-test"

    def test_model_id(self) -> None:
        assert self.provider.model_id == "deterministic-test-v1"

    def test_generates_answer_from_first_item(self) -> None:
        content = "AtlasCore supports multi-workspace deployments."
        item = make_evidence_item("E1", content=content)
        packet = make_packet(items=[item])
        answer = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        assert isinstance(answer, ProviderAnswer)
        assert content[:100] in answer.answer_text

    def test_cites_all_items(self) -> None:
        items = [make_evidence_item(f"E{i + 1}") for i in range(4)]
        packet = make_packet(items=items)
        answer = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        assert set(answer.citation_ids) == {"E1", "E2", "E3", "E4"}

    def test_raises_on_empty_packet(self) -> None:
        from app.answering.evidence import EvidenceBand

        packet = make_packet(items=[], band=EvidenceBand.NONE, score=0.0)
        with pytest.raises(AnswerProviderError):
            asyncio.run(
                self.provider.generate("question", packet, "prompt")
            )

    def test_returns_provider_answer_dataclass(self) -> None:
        packet = make_packet()
        answer = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        assert hasattr(answer, "answer_text")
        assert hasattr(answer, "citation_ids")
        assert hasattr(answer, "provider")
        assert hasattr(answer, "model")

    def test_answer_text_is_string(self) -> None:
        packet = make_packet()
        answer = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        assert isinstance(answer.answer_text, str)
        assert len(answer.answer_text) > 0

    def test_citation_ids_are_strings(self) -> None:
        packet = make_packet()
        answer = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        for cid in answer.citation_ids:
            assert isinstance(cid, str)

    def test_deterministic_same_input_same_output(self) -> None:
        packet = make_packet()
        answer1 = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        answer2 = asyncio.run(
            self.provider.generate("question", packet, "prompt")
        )
        assert answer1.answer_text == answer2.answer_text
        assert answer1.citation_ids == answer2.citation_ids


class TestBuildAnswerProvider:
    def test_mock_alias(self) -> None:
        p = build_answer_provider("mock")
        assert isinstance(p, DeterministicTestAnswerProvider)

    def test_test_alias(self) -> None:
        p = build_answer_provider("test")
        assert isinstance(p, DeterministicTestAnswerProvider)

    def test_deterministic_test_alias(self) -> None:
        p = build_answer_provider("deterministic-test")
        assert isinstance(p, DeterministicTestAnswerProvider)

    def test_empty_string_alias(self) -> None:
        p = build_answer_provider("")
        assert isinstance(p, DeterministicTestAnswerProvider)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown answer provider"):
            build_answer_provider("definitely-unknown")
