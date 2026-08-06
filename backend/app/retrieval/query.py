"""
Query normalisation for Phase 2B retrieval.

The query is UNTRUSTED USER INPUT. Normalisation is purely deterministic
text processing — no LLM rewriting, no automatic translation, no execution.

A query containing "ignore previous instructions" is normalised identically
to any other query and returned as plain text to the retrieval pipeline.
"""

from __future__ import annotations

import re
import unicodedata

# Maximum query length in characters (after normalisation).
# Enforced before any database operation.
MAX_QUERY_LENGTH = 2000

# Collapsed whitespace pattern.
_WHITESPACE_RE = re.compile(r"\s+")


class QueryNormalisationError(Exception):
    """Raised when the query cannot be normalised to a valid search query."""


def normalise_query(raw: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    """
    Normalise a raw user query into a form safe for lexical and vector retrieval.

    Steps (all deterministic, no I/O):
      1. Strip surrounding whitespace.
      2. Collapse internal whitespace runs to a single space.
      3. NFC-normalise Unicode (canonical decomposition then recomposition).
         This preserves meaningful Unicode characters including accented letters,
         CJK, Arabic, Hebrew, etc. — it does not strip or transliterate them.
      4. Reject empty string after normalisation.
      5. Truncate to max_length if needed (not an error; silently enforced).
         Callers should reject oversized queries at the API layer with a 422.

    Parameters
    ----------
    raw:        The raw query string from the user.
    max_length: Maximum allowed character length. Defaults to MAX_QUERY_LENGTH.

    Returns
    -------
    The normalised query string (non-empty, ≤ max_length chars).

    Raises
    ------
    QueryNormalisationError — if the query is empty after normalisation.
    """
    if not isinstance(raw, str):
        raise QueryNormalisationError("Query must be a string.")

    # Step 1: strip surrounding whitespace.
    q = raw.strip()

    # Step 2: collapse internal whitespace.
    q = _WHITESPACE_RE.sub(" ", q)

    # Step 3: NFC Unicode normalisation (preserves meaning; does not strip).
    q = unicodedata.normalize("NFC", q)

    # Step 4: reject empty.
    if not q:
        raise QueryNormalisationError("Query is empty after normalisation.")

    # Step 5: enforce max length (caller should reject before reaching here,
    # but we silently clamp for defence-in-depth).
    if len(q) > max_length:
        q = q[:max_length]

    return q


def validate_query_length(query: str, max_length: int = MAX_QUERY_LENGTH) -> None:
    """
    Raise QueryNormalisationError if the query exceeds max_length.

    Call this BEFORE normalise_query if you want an explicit rejection
    rather than silent truncation.
    """
    if len(query) > max_length:
        raise QueryNormalisationError(
            f"Query is too long ({len(query)} chars). Maximum is {max_length} chars."
        )
