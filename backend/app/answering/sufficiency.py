"""
EvidenceSufficiencyPolicy — Phase 2C.

Determines whether evidence is sufficient to attempt grounded answering.
If insufficient, the system ABSTAINS — the AnswerProvider is NOT called.

CRITICAL REQUIREMENT:
  AtlasCore is an enterprise knowledge system.
  Grounding is more important than appearing helpful.
  If evidence does not support the answer, the system must say so.
  General model knowledge MUST NOT be used as a fallback.

Sufficiency outcomes:
  ANSWER          — evidence is sufficient; proceed to AnswerProvider
  ABSTAIN_NO_EVIDENCE   — zero eligible evidence items returned
  ABSTAIN_WEAK_EVIDENCE — evidence exists but below confidence threshold
  PROVIDER_FAILURE      — reserved for upstream errors (not a sufficiency state)

Thresholds (configurable):
  min_items:     minimum number of eligible evidence items (default: 1)
  min_band:      minimum EvidenceBand (default: LOW)

Conservative policy:
  - NONE band → always ABSTAIN_NO_EVIDENCE
  - LOW band → ABSTAIN_WEAK_EVIDENCE by default (configurable)
  - MEDIUM/HIGH → ANSWER
"""

from __future__ import annotations

from enum import StrEnum

from app.answering.evidence import EvidenceBand, EvidencePacket


class SufficiencyOutcome(StrEnum):
    ANSWER = "answer"
    ABSTAIN_NO_EVIDENCE = "abstain_no_evidence"
    ABSTAIN_WEAK_EVIDENCE = "abstain_weak_evidence"
    PROVIDER_FAILURE = "provider_failure"


class EvidenceSufficiencyPolicy:
    """
    Deterministic policy for deciding whether to invoke the AnswerProvider.

    Parameters
    ----------
    min_items:       Minimum evidence items required (default 1).
    require_medium:  If True, LOW band triggers ABSTAIN_WEAK_EVIDENCE.
                     If False, LOW band is accepted (default True).

    Design rationale:
    - Separating sufficiency policy from the AnswerProvider keeps the
      boundary explicit: the provider is never called with zero evidence,
      and the provider cannot override the abstention decision.
    - The policy is stateless and deterministic — no LLM involved.
    """

    def __init__(
        self,
        min_items: int = 1,
        require_medium: bool = True,
    ) -> None:
        self._min_items = min_items
        self._require_medium = require_medium

    def assess(self, packet: EvidencePacket) -> SufficiencyOutcome:
        """
        Assess whether the EvidencePacket justifies calling the AnswerProvider.

        Returns SufficiencyOutcome.
        """
        if not packet.items or packet.evidence_band == EvidenceBand.NONE:
            return SufficiencyOutcome.ABSTAIN_NO_EVIDENCE

        if len(packet.items) < self._min_items:
            return SufficiencyOutcome.ABSTAIN_NO_EVIDENCE

        if packet.evidence_band == EvidenceBand.LOW and self._require_medium:
            return SufficiencyOutcome.ABSTAIN_WEAK_EVIDENCE

        return SufficiencyOutcome.ANSWER

    @staticmethod
    def abstention_message(outcome: SufficiencyOutcome) -> str:
        """
        Human-facing message for abstention outcomes.

        Enterprise/professional tone.  Does NOT claim the model "knows nothing".
        """
        if outcome == SufficiencyOutcome.ABSTAIN_NO_EVIDENCE:
            return (
                "No relevant information was found in the available knowledge base "
                "to answer this question."
            )
        if outcome == SufficiencyOutcome.ABSTAIN_WEAK_EVIDENCE:
            return (
                "The available knowledge does not contain sufficient evidence to "
                "answer this question reliably."
            )
        # PROVIDER_FAILURE message is constructed by the caller with error context.
        return "Unable to generate a grounded answer at this time."
