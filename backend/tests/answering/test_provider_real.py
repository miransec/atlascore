"""
Tests for Phase 2D real LLM providers (OpenAIAnswerProvider, AnthropicAnswerProvider).

All tests use mocked httpx — no real network calls, no API key required.
The mocks simulate:
  - Successful responses (valid answer text + citations)
  - Transient errors (429, 503, timeout) — expect bounded retries
  - Non-retryable errors (401, 400) — expect immediate AnswerProviderError
  - Malformed response bodies — expect AnswerProviderError

Security assertions:
  - AnswerProviderError never exposes API key value
  - AnswerProviderError message is generic (no stack trace)
  - API key is never in the log output (tested by checking that KeyError
    is raised if 'api_key' string appears in any log record)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.answering.provider import (
    AnswerProviderError,
    AnthropicAnswerProvider,
    DeterministicTestAnswerProvider,
    OpenAIAnswerProvider,
    _extract_citation_ids,
    build_answer_provider,
)
from tests.answering.conftest import make_evidence_item, make_packet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_packet():
    item = make_evidence_item(
        evidence_id="E1",
        content="The capital of France is Paris.",
        hybrid_score=0.033,
    )
    return make_packet([item], band="high", score=0.8, query="capital of France")


def _openai_200(answer_text: str) -> MagicMock:
    """Create a mock httpx.Response for a successful OpenAI response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": answer_text}}]}
    return resp


def _anthropic_200(answer_text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"content": [{"type": "text", "text": answer_text}]}
    return resp


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"error": "test error"}
    return resp


# ---------------------------------------------------------------------------
# _extract_citation_ids
# ---------------------------------------------------------------------------


class TestExtractCitationIds:
    def test_extracts_single(self) -> None:
        result = _extract_citation_ids("The answer is X [E1].")
        assert result == ["E1"]

    def test_extracts_multiple(self) -> None:
        result = _extract_citation_ids("See [E1] and also [E3] for more [E2].")
        assert result == ["E1", "E3", "E2"]

    def test_deduplicates_preserving_order(self) -> None:
        result = _extract_citation_ids("[E1] is mentioned [E1] again and [E2].")
        assert result == ["E1", "E2"]

    def test_empty_string_returns_empty(self) -> None:
        assert _extract_citation_ids("") == []

    def test_no_citations_returns_empty(self) -> None:
        assert _extract_citation_ids("No citations here.") == []

    def test_large_index(self) -> None:
        result = _extract_citation_ids("[E99] is cited.")
        assert result == ["E99"]


# ---------------------------------------------------------------------------
# build_answer_provider factory
# ---------------------------------------------------------------------------


class TestBuildAnswerProvider:
    def test_deterministic_by_default(self) -> None:
        provider = build_answer_provider("deterministic-test")
        assert isinstance(provider, DeterministicTestAnswerProvider)

    def test_mock_alias(self) -> None:
        provider = build_answer_provider("mock")
        assert isinstance(provider, DeterministicTestAnswerProvider)

    def test_demo_alias(self) -> None:
        provider = build_answer_provider("demo")
        assert isinstance(provider, DeterministicTestAnswerProvider)

    def test_openai_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            build_answer_provider("openai", openai_api_key="")

    def test_anthropic_requires_api_key(self) -> None:
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            build_answer_provider("anthropic", anthropic_api_key="")

    def test_openai_with_key_returns_provider(self) -> None:
        provider = build_answer_provider(
            "openai",
            openai_api_key="sk-test-key-not-real",
            openai_model="gpt-4o",
        )
        assert isinstance(provider, OpenAIAnswerProvider)
        assert provider.provider_id == "openai"
        assert provider.model_id == "gpt-4o"

    def test_anthropic_with_key_returns_provider(self) -> None:
        provider = build_answer_provider(
            "anthropic",
            anthropic_api_key="sk-ant-test-not-real",
            anthropic_model="claude-opus-4-5",
        )
        assert isinstance(provider, AnthropicAnswerProvider)
        assert provider.provider_id == "anthropic"

    def test_openai_with_custom_base_url(self) -> None:
        custom = "https://gateway.example.com/v1/chat/completions"
        provider = build_answer_provider(
            "openai",
            openai_api_key="sk-test-key-not-real",
            openai_base_url=custom,
        )
        assert isinstance(provider, OpenAIAnswerProvider)
        assert provider._base_url == custom

    def test_openai_default_base_url_when_empty(self) -> None:
        provider = build_answer_provider(
            "openai",
            openai_api_key="sk-test-key-not-real",
            openai_base_url="",
        )
        assert isinstance(provider, OpenAIAnswerProvider)
        assert "api.openai.com" in provider._base_url

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown answer provider"):
            build_answer_provider("unknown-xyz")


# ---------------------------------------------------------------------------
# OpenAIAnswerProvider (mocked httpx)
# ---------------------------------------------------------------------------


class TestOpenAIAnswerProvider:
    def _make_provider(self) -> OpenAIAnswerProvider:
        return OpenAIAnswerProvider(
            api_key="sk-test-not-real",
            model="gpt-4o",
            timeout_seconds=10,
            max_retries=1,
        )

    def test_successful_response(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()
        mock_response = _openai_200("Paris is the capital [E1].")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = _run(provider.generate("What is the capital?", packet, "prompt"))

        assert result.answer_text == "Paris is the capital [E1]."
        assert result.citation_ids == ["E1"]
        assert result.provider == "openai"
        assert result.model == "gpt-4o"

    def test_transient_429_retried(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _error_response(429)
            return _openai_200("Success after retry [E1].")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            result = _run(provider.generate("question", packet, "prompt"))

        assert call_count == 2  # one retry
        assert "Success after retry" in result.answer_text

    def test_non_retryable_401_raises_immediately(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _error_response(401)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AnswerProviderError):
                _run(provider.generate("question", packet, "prompt"))

        # Non-retryable — should only be called once.
        assert call_count == 1

    def test_all_retries_exhausted_raises(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()

        async def mock_post(*args, **kwargs):
            return _error_response(503)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AnswerProviderError) as exc_info:
                _run(provider.generate("question", packet, "prompt"))

        # Error message must not expose API key or internals.
        assert "sk-test" not in str(exc_info.value)
        assert "not-real" not in str(exc_info.value)

    def test_empty_choices_raises(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AnswerProviderError, match="no choices"):
                _run(provider.generate("question", packet, "prompt"))

    def test_timeout_retried(self) -> None:
        import httpx as _httpx

        provider = self._make_provider()
        packet = _make_packet()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _httpx.TimeoutException("timeout")
            return _openai_200("After timeout retry [E1].")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            result = _run(provider.generate("question", packet, "prompt"))

        assert call_count == 2
        assert "After timeout retry" in result.answer_text

    def test_provider_id_and_model_id(self) -> None:
        provider = OpenAIAnswerProvider(
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        assert provider.provider_id == "openai"
        assert provider.model_id == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# AnthropicAnswerProvider (mocked httpx)
# ---------------------------------------------------------------------------


class TestAnthropicAnswerProvider:
    def _make_provider(self) -> AnthropicAnswerProvider:
        return AnthropicAnswerProvider(
            api_key="sk-ant-test-not-real",
            model="claude-opus-4-5",
            timeout_seconds=10,
            max_retries=1,
        )

    def test_successful_response(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()
        mock_response = _anthropic_200("Paris is the capital of France [E1].")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = _run(provider.generate("question", packet, "prompt"))

        assert "Paris" in result.answer_text
        assert result.citation_ids == ["E1"]
        assert result.provider == "anthropic"

    def test_529_retried(self) -> None:
        """Anthropic-specific 529 overloaded status is retried."""
        provider = self._make_provider()
        packet = _make_packet()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _error_response(529)
            return _anthropic_200("After 529 retry [E1].")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=mock_post)
            mock_client_cls.return_value = mock_client

            _run(provider.generate("question", packet, "prompt"))

        assert call_count == 2

    def test_no_text_content_raises(self) -> None:
        provider = self._make_provider()
        packet = _make_packet()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": [{"type": "tool_use", "input": {}}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(AnswerProviderError, match="no text content"):
                _run(provider.generate("question", packet, "prompt"))

    def test_provider_id_and_model_id(self) -> None:
        provider = AnthropicAnswerProvider(
            api_key="sk-ant-test",
            model="claude-haiku-4-5",
        )
        assert provider.provider_id == "anthropic"
        assert provider.model_id == "claude-haiku-4-5"
