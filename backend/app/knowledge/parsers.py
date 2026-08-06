"""
Document parser abstraction for the knowledge ingestion pipeline.

SECURITY:
  - All parsers treat the input bytes as UNTRUSTED DATA.
  - Parsers extract text only.  They must never:
      - Execute document content as code
      - Interpret embedded macros or scripts
      - Modify system prompts, permissions, or ingestion configuration
      - Make network requests
      - Write to disk (the service layer owns blob writes)
  - The extracted text is plain Unicode string — no markup, no instructions.

Supported media types (Phase 2A):
  - text/plain               → PlainTextParser
  - text/markdown            → MarkdownParser (strips markdown syntax)

Extending:
  - Implement DocumentParser and register via PARSER_REGISTRY.
  - Do NOT add parsers that execute, interpret, or network-fetch content.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ParseResult:
    """The result of parsing a document blob."""

    text: str
    """Extracted plain text.  May be empty for blank documents."""

    media_type: str
    """The media type the parser was invoked for."""


class ParseError(Exception):
    """Raised when a parser cannot process the given content."""


class UnsupportedMediaTypeError(ParseError):
    """Raised when no parser is registered for the given media type."""


class DocumentParser(ABC):
    """Abstract interface for a document parser."""

    @property
    @abstractmethod
    def supported_media_types(self) -> frozenset[str]:
        """The set of media type strings this parser handles."""

    @abstractmethod
    def parse(self, content: bytes, media_type: str) -> ParseResult:
        """
        Parse the binary content and return extracted plain text.

        Parameters
        ----------
        content:    Raw document bytes.  Treated as untrusted input.
        media_type: The declared media type (already validated by the service).

        Returns
        -------
        ParseResult with the extracted text.

        Raises
        ------
        ParseError  — if the content cannot be parsed.
        """


class PlainTextParser(DocumentParser):
    """
    Parser for text/plain content.

    Decodes UTF-8 bytes to text, stripping null bytes and normalising
    line endings.  Falls back to latin-1 if UTF-8 decoding fails.
    """

    @property
    def supported_media_types(self) -> frozenset[str]:
        return frozenset({"text/plain"})

    def parse(self, content: bytes, media_type: str) -> ParseResult:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise ParseError(f"Cannot decode text/plain content: {exc}") from exc

        # Normalise: remove null bytes, normalise line endings.
        text = text.replace("\x00", "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return ParseResult(text=text.strip(), media_type=media_type)


# Markdown syntax patterns to strip.
# These are intentionally conservative — the goal is plain text, not perfect
# rendering.  We strip: headings, bold/italic/strikethrough, inline code,
# fenced code blocks, blockquotes, horizontal rules, link syntax, image
# syntax, HTML tags, and leading list markers.
_MD_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE_CODE = re.compile(r"`[^`\n]+`")
_MD_HTML_TAG = re.compile(r"<[^>]+>")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_ITALIC = re.compile(r"\*{1,3}(.*?)\*{1,3}", re.DOTALL)
_MD_STRIKETHROUGH = re.compile(r"~~(.*?)~~", re.DOTALL)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^\)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^\)]*\)")
_MD_BLOCKQUOTE = re.compile(r"^>\s+", re.MULTILINE)
_MD_HR = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MD_LIST_MARKER = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_MD_ORDERED_LIST = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)


class MarkdownParser(DocumentParser):
    """
    Parser for text/markdown content.

    Strips Markdown syntax and returns plain text.  Does not execute
    embedded code blocks or HTML.
    """

    @property
    def supported_media_types(self) -> frozenset[str]:
        return frozenset({"text/markdown", "text/x-markdown"})

    def parse(self, content: bytes, media_type: str) -> ParseResult:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise ParseError(f"Cannot decode markdown content: {exc}") from exc

        # Strip in order: fenced code blocks, inline code, HTML tags, headings,
        # bold/italic, strikethrough, images (keep alt text), links (keep label),
        # blockquotes, horizontal rules, list markers.
        text = _MD_FENCED_CODE.sub("", text)
        text = _MD_INLINE_CODE.sub("", text)
        text = _MD_HTML_TAG.sub("", text)
        text = _MD_HEADING.sub("", text)
        text = _MD_BOLD_ITALIC.sub(r"\1", text)
        text = _MD_STRIKETHROUGH.sub(r"\1", text)
        text = _MD_IMAGE.sub(r"\1", text)
        text = _MD_LINK.sub(r"\1", text)
        text = _MD_BLOCKQUOTE.sub("", text)
        text = _MD_HR.sub("", text)
        text = _MD_LIST_MARKER.sub("", text)
        text = _MD_ORDERED_LIST.sub("", text)

        # Collapse multiple blank lines into one.
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Remove null bytes.
        text = text.replace("\x00", "")

        return ParseResult(text=text.strip(), media_type=media_type)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Global parser registry — maps each supported media type to its parser.
PARSER_REGISTRY: dict[str, DocumentParser] = {}

_plain = PlainTextParser()
_markdown = MarkdownParser()

for _mt in _plain.supported_media_types:
    PARSER_REGISTRY[_mt] = _plain

for _mt in _markdown.supported_media_types:
    PARSER_REGISTRY[_mt] = _markdown


def get_parser(media_type: str) -> DocumentParser:
    """
    Return the parser for the given media type.

    Raises UnsupportedMediaTypeError if no parser is registered.
    """
    # Normalise: strip parameters (e.g. "; charset=utf-8").
    base_type = media_type.split(";")[0].strip().lower()
    parser = PARSER_REGISTRY.get(base_type)
    if parser is None:
        raise UnsupportedMediaTypeError(
            f"No parser registered for media type: {base_type!r}. "
            f"Supported types: {sorted(PARSER_REGISTRY)}"
        )
    return parser


def is_supported_media_type(media_type: str) -> bool:
    """Return True if a parser exists for the given media type."""
    base_type = media_type.split(";")[0].strip().lower()
    return base_type in PARSER_REGISTRY
