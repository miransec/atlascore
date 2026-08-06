"""
Test suite for document parsers.

Tests:
  PA-01  PlainTextParser returns text for plain utf-8
  PA-02  PlainTextParser strips null bytes and normalises line endings
  PA-03  PlainTextParser falls back to latin-1 for non-utf8
  PA-04  MarkdownParser returns plain text (no markdown syntax)
  PA-05  MarkdownParser strips fenced code blocks
  PA-06  MarkdownParser preserves link text, removes URL
  PA-07  MarkdownParser preserves image alt text, removes URL
  PA-08  get_parser returns correct parser for each media type
  PA-09  get_parser raises UnsupportedMediaTypeError for unknown type
  PA-10  is_supported_media_type returns correct bool
  PA-11  Parser strips leading/trailing whitespace
  PA-12  Empty input returns empty string
"""

from __future__ import annotations

import pytest

from app.knowledge.parsers import (
    MarkdownParser,
    ParseResult,
    PlainTextParser,
    UnsupportedMediaTypeError,
    get_parser,
    is_supported_media_type,
)

# ---- PA-01: PlainTextParser utf-8 ---------------------------------------


def test_pa01_plain_text_utf8() -> None:
    parser = PlainTextParser()
    result = parser.parse(b"Hello world", "text/plain")
    assert isinstance(result, ParseResult)
    assert result.text == "Hello world"
    assert result.media_type == "text/plain"


# ---- PA-02: null bytes and line ending normalisation --------------------


def test_pa02_plain_text_normalisation() -> None:
    parser = PlainTextParser()
    content = b"line1\r\nline2\rline3\x00"
    result = parser.parse(content, "text/plain")
    assert "\x00" not in result.text
    assert "\r" not in result.text
    assert result.text == "line1\nline2\nline3"


# ---- PA-03: latin-1 fallback --------------------------------------------


def test_pa03_plain_text_latin1_fallback() -> None:
    parser = PlainTextParser()
    content = "café".encode("latin-1")  # not valid utf-8
    result = parser.parse(content, "text/plain")
    assert "caf" in result.text


# ---- PA-04: MarkdownParser strips headings and bold ---------------------


def test_pa04_markdown_strips_syntax() -> None:
    parser = MarkdownParser()
    content = b"# Title\n\n**bold** and *italic* text"
    result = parser.parse(content, "text/markdown")
    assert "#" not in result.text
    assert "**" not in result.text
    assert "bold" in result.text
    assert "italic" in result.text


# ---- PA-05: MarkdownParser strips fenced code blocks --------------------


def test_pa05_markdown_strips_code_blocks() -> None:
    parser = MarkdownParser()
    content = b"intro\n```python\nexec('bad')\n```\noutro"
    result = parser.parse(content, "text/markdown")
    assert "exec" not in result.text
    assert "intro" in result.text
    assert "outro" in result.text


# ---- PA-06: MarkdownParser preserves link text --------------------------


def test_pa06_markdown_link_text_preserved() -> None:
    parser = MarkdownParser()
    content = b"See [the documentation](https://example.com) for details"
    result = parser.parse(content, "text/markdown")
    assert "the documentation" in result.text
    assert "https://" not in result.text


# ---- PA-07: MarkdownParser preserves image alt text --------------------


def test_pa07_markdown_image_alt_preserved() -> None:
    parser = MarkdownParser()
    content = b"![A chart showing growth](chart.png)"
    result = parser.parse(content, "text/markdown")
    assert "A chart showing growth" in result.text
    assert "chart.png" not in result.text


# ---- PA-08: get_parser returns correct parser ---------------------------


@pytest.mark.parametrize(
    "mt,expected_class",
    [
        ("text/plain", PlainTextParser),
        ("text/plain; charset=utf-8", PlainTextParser),
        ("text/markdown", MarkdownParser),
        ("text/x-markdown", MarkdownParser),
    ],
)
def test_pa08_get_parser_returns_correct(mt: str, expected_class: type) -> None:
    parser = get_parser(mt)
    assert isinstance(parser, expected_class)


# ---- PA-09: get_parser raises UnsupportedMediaTypeError -----------------


def test_pa09_get_parser_unknown_raises() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        get_parser("application/pdf")


# ---- PA-10: is_supported_media_type ------------------------------------


def test_pa10_is_supported() -> None:
    assert is_supported_media_type("text/plain")
    assert is_supported_media_type("text/markdown")
    assert not is_supported_media_type("application/pdf")
    assert not is_supported_media_type("image/jpeg")


# ---- PA-11: strip whitespace -------------------------------------------


def test_pa11_strip_whitespace() -> None:
    parser = PlainTextParser()
    result = parser.parse(b"  spaces  \n  ", "text/plain")
    assert result.text == "spaces"


# ---- PA-12: empty input -------------------------------------------------


def test_pa12_empty_input() -> None:
    parser = PlainTextParser()
    result = parser.parse(b"", "text/plain")
    assert result.text == ""
