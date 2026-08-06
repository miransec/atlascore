"""
Unit tests for query normalisation (app.retrieval.query).

These are pure Python — no database, no network, no async.
"""

import unicodedata

import pytest

from app.retrieval.query import QueryNormalisationError, normalise_query


class TestNormaliseQuery:
    """Happy-path normalisation behaviour."""

    def test_plain_ascii_passthrough(self) -> None:
        result = normalise_query("machine learning")
        assert result == "machine learning"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        result = normalise_query("   hello world   ")
        assert result == "hello world"

    def test_internal_whitespace_collapsed(self) -> None:
        result = normalise_query("hello\t\tworld\n\nfoo")
        assert result == "hello world foo"

    def test_tabs_and_newlines_collapsed(self) -> None:
        result = normalise_query("a\tb\nc\rd")
        assert result == "a b c d"

    def test_unicode_nfc_normalisation(self) -> None:
        # NFD: e + combining acute accent — two code-points.
        nfd = "café"  # 'cafe' + combining acute on 'e' → 'é' in NFD
        result = normalise_query(nfd)
        # NFC: single precomposed code-point U+00E9.
        expected = unicodedata.normalize("NFC", nfd)
        assert result == expected

    def test_already_nfc_unchanged(self) -> None:
        # U+00E9 is already NFC.
        already_nfc = "café"
        result = normalise_query(already_nfc)
        assert result == already_nfc

    def test_max_length_not_exceeded_at_boundary(self) -> None:
        # Exactly at the limit — should pass without truncation.
        query = "a" * 2000
        result = normalise_query(query, max_length=2000)
        assert len(result) == 2000

    def test_query_truncated_when_over_max_length(self) -> None:
        # Over the limit — should be silently truncated to max_length.
        query = "a" * 2100
        result = normalise_query(query, max_length=2000)
        assert len(result) == 2000

    def test_custom_max_length_respected(self) -> None:
        result = normalise_query("abcde", max_length=3)
        assert len(result) == 3

    def test_returns_string(self) -> None:
        result = normalise_query("anything")
        assert isinstance(result, str)


class TestNormaliseQueryErrors:
    """Error cases that must raise QueryNormalisationError."""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(QueryNormalisationError):
            normalise_query("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(QueryNormalisationError):
            normalise_query("   \t\n  ")

    def test_tab_only_raises(self) -> None:
        with pytest.raises(QueryNormalisationError):
            normalise_query("\t\t\t")

    def test_error_message_is_informative(self) -> None:
        with pytest.raises(QueryNormalisationError, match="empty"):
            normalise_query("")


class TestNormaliseQueryEdgeCases:
    """Edge cases that must not raise."""

    def test_single_character_passes(self) -> None:
        result = normalise_query("a")
        assert result == "a"

    def test_single_unicode_char_passes(self) -> None:
        result = normalise_query("中")  # Chinese character
        assert result == "中"

    def test_query_with_punctuation_passes(self) -> None:
        # SQL injection attempt — must be treated as plain text, not raise.
        result = normalise_query("' OR 1=1 --")
        assert result == "' OR 1=1 --"

    def test_numeric_query_passes(self) -> None:
        result = normalise_query("123456")
        assert result == "123456"

    def test_mixed_whitespace_and_content(self) -> None:
        result = normalise_query("  foo  bar  ")
        assert result == "foo bar"
