"""
CitationValidator and citation schema — Phase 2C.

The provider may return citation IDs (E1, E3, …) in its answer.
CitationValidator resolves each ID to server-controlled provenance from
the current EvidencePacket.

KEY PRINCIPLE:
  The provider supplies citation IDs.
  AtlasCore resolves IDs → provenance.
  Provider-supplied source names, document titles, or URLs are NEVER used.
  This prevents fabricated source metadata.

Validation rules:
  1. Citation ID must exist in the current EvidencePacket (same request).
  2. Citation IDs from other requests or sessions are rejected.
  3. Duplicate IDs are collapsed.
  4. IDs that do not match the E{n} pattern are rejected.
  5. Invented IDs (e.g. E999 when only E1-E5 exist) are rejected.

Citations in the response are ordered by their evidence rank (ascending E1, E2, …).
The final answer text uses numeric labels [1], [2], … mapped to citation_id E1, E2, …
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.answering.evidence import EvidenceItem, EvidencePacket

_EVIDENCE_ID_PATTERN = re.compile(r"^E\d+$")


class CitationValidationError(Exception):
    """Raised when provider returns invalid citation IDs."""


@dataclass
class Citation:
    """
    A validated citation derived from server-controlled evidence provenance.

    All metadata comes from the EvidenceItem (server-side), not the provider.
    Provider-supplied source names or document titles are NEVER used.
    """

    citation_id: str  # "E1", "E2", …
    source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    source_name: str  # from EvidenceItem.source_name (server-controlled)
    document_title: str  # from EvidenceItem.document_title (server-controlled)
    version_number: int
    chunk_index: int
    excerpt: str | None  # short excerpt for display (optional, bounded)


class CitationValidator:
    """
    Validates and resolves provider-supplied citation IDs.

    Usage:
        validator = CitationValidator(max_excerpt_chars=200)
        citations = validator.validate(provider_citation_ids, evidence_packet)
    """

    def __init__(self, max_excerpt_chars: int = 200) -> None:
        self._max_excerpt_chars = max_excerpt_chars

    def validate(
        self,
        provider_citation_ids: list[str],
        evidence_packet: EvidencePacket,
    ) -> list[Citation]:
        """
        Validate provider-supplied citation IDs against the EvidencePacket.

        Parameters
        ----------
        provider_citation_ids:  IDs returned by the AnswerProvider.
        evidence_packet:        The EvidencePacket for the current request.

        Returns
        -------
        List of validated Citation objects, ordered by evidence ID (E1 first).
        Duplicate IDs are collapsed.

        Raises
        ------
        CitationValidationError — if any ID is invalid or not in the packet.
        """
        # Build a lookup from evidence_id → EvidenceItem.
        evidence_map: dict[str, EvidenceItem] = {
            item.evidence_id: item for item in evidence_packet.items
        }

        seen: set[str] = set()
        citations: list[Citation] = []

        for raw_id in provider_citation_ids:
            # Structural validation: must match E{n} pattern.
            if not isinstance(raw_id, str) or not _EVIDENCE_ID_PATTERN.match(raw_id):
                raise CitationValidationError(
                    f"Invalid citation ID format: {raw_id!r}. "
                    "Citation IDs must be in the form E1, E2, …"
                )

            # Must exist in the current packet (prevents E999, cross-request IDs).
            if raw_id not in evidence_map:
                raise CitationValidationError(
                    f"Citation ID {raw_id!r} does not exist in the current evidence packet. "
                    "Fabricated or stale citation IDs are not permitted."
                )

            # Deduplicate.
            if raw_id in seen:
                continue
            seen.add(raw_id)

            item = evidence_map[raw_id]
            excerpt = self._make_excerpt(item.content)

            citations.append(
                Citation(
                    citation_id=raw_id,
                    source_id=item.source_id,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    chunk_id=item.chunk_id,
                    source_name=item.source_name,
                    document_title=item.document_title,
                    version_number=item.version_number,
                    chunk_index=item.chunk_index,
                    excerpt=excerpt,
                )
            )

        # Sort by evidence ID order (E1 < E2 < …).
        citations.sort(key=lambda c: int(c.citation_id[1:]))
        return citations

    def _make_excerpt(self, content: str) -> str | None:
        """Return a safe bounded excerpt for display."""
        if not content:
            return None
        s = content[: self._max_excerpt_chars].strip()
        if len(content) > self._max_excerpt_chars:
            s += "…"
        return s


def rewrite_citations_in_answer(answer_text: str, citations: list[Citation]) -> str:
    """
    Rewrite provider citation markers [E1], [E2], … to numeric [1], [2], …

    The mapping is: sorted position in `citations` list → numeric label.
    E.g. if citations = [Citation(E1), Citation(E3)]:
      [E1] → [1]
      [E3] → [2]

    Unknown/rejected [EX] patterns are removed from the answer text.
    """
    id_to_num: dict[str, int] = {c.citation_id: idx + 1 for idx, c in enumerate(citations)}

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        eid = match.group(1)
        if eid in id_to_num:
            return f"[{id_to_num[eid]}]"
        # Unknown ID — remove from answer text.
        return ""

    return re.sub(r"\[(E\d+)\]", _replace, answer_text)
