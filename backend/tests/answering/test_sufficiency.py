"""
Unit tests for app.answering.sufficiency.

Tests cover:
  - ABSTAIN_NO_EVIDENCE on empty packet / NONE band
  - ABSTAIN_NO_EVIDENCE when below min_items
  - ABSTAIN_WEAK_EVIDENCE on LOW band with require_medium=True
  - ANSWER when require_medium=False and LOW band
  - ANSWER on MEDIUM band
  - ANSWER on HIGH band
  - abstention_message strings
"""

from __future__ import annotations

from app.answering.evidence import EvidenceBand
from app.answering.sufficiency import EvidenceSufficiencyPolicy, SufficiencyOutcome
from tests.answering.conftest import make_evidence_item, make_packet


class TestEvidenceSufficiencyPolicy:
    def test_abstain_on_empty_packet(self) -> None:
        policy = EvidenceSufficiencyPolicy()
        packet = make_packet(items=[], band=EvidenceBand.NONE, score=0.0)
        assert policy.assess(packet) == SufficiencyOutcome.ABSTAIN_NO_EVIDENCE

    def test_abstain_on_none_band(self) -> None:
        policy = EvidenceSufficiencyPolicy()
        packet = make_packet(items=[make_evidence_item()], band=EvidenceBand.NONE, score=0.0)
        assert policy.assess(packet) == SufficiencyOutcome.ABSTAIN_NO_EVIDENCE

    def test_abstain_below_min_items(self) -> None:
        policy = EvidenceSufficiencyPolicy(min_items=3)
        packet = make_packet(
            items=[make_evidence_item(), make_evidence_item("E2")],
            band=EvidenceBand.HIGH,
            score=0.85,
        )
        assert policy.assess(packet) == SufficiencyOutcome.ABSTAIN_NO_EVIDENCE

    def test_abstain_weak_evidence_on_low_band(self) -> None:
        policy = EvidenceSufficiencyPolicy(require_medium=True)
        packet = make_packet(items=[make_evidence_item()], band=EvidenceBand.LOW, score=0.30)
        assert policy.assess(packet) == SufficiencyOutcome.ABSTAIN_WEAK_EVIDENCE

    def test_answer_on_low_band_when_not_required(self) -> None:
        policy = EvidenceSufficiencyPolicy(require_medium=False)
        packet = make_packet(items=[make_evidence_item()], band=EvidenceBand.LOW, score=0.30)
        assert policy.assess(packet) == SufficiencyOutcome.ANSWER

    def test_answer_on_medium_band(self) -> None:
        policy = EvidenceSufficiencyPolicy()
        packet = make_packet(items=[make_evidence_item()], band=EvidenceBand.MEDIUM, score=0.60)
        assert policy.assess(packet) == SufficiencyOutcome.ANSWER

    def test_answer_on_high_band(self) -> None:
        policy = EvidenceSufficiencyPolicy()
        packet = make_packet(items=[make_evidence_item()], band=EvidenceBand.HIGH, score=0.85)
        assert policy.assess(packet) == SufficiencyOutcome.ANSWER

    def test_abstention_message_no_evidence(self) -> None:
        msg = EvidenceSufficiencyPolicy.abstention_message(SufficiencyOutcome.ABSTAIN_NO_EVIDENCE)
        assert "No relevant information" in msg

    def test_abstention_message_weak_evidence(self) -> None:
        msg = EvidenceSufficiencyPolicy.abstention_message(SufficiencyOutcome.ABSTAIN_WEAK_EVIDENCE)
        assert "sufficient evidence" in msg

    def test_abstention_message_provider_failure(self) -> None:
        msg = EvidenceSufficiencyPolicy.abstention_message(SufficiencyOutcome.PROVIDER_FAILURE)
        assert msg  # non-empty fallback

    def test_answer_requires_min_items_met(self) -> None:
        policy = EvidenceSufficiencyPolicy(min_items=2)
        # Exactly 2 items → should ANSWER on HIGH band.
        items = [make_evidence_item("E1"), make_evidence_item("E2")]
        packet = make_packet(items=items, band=EvidenceBand.HIGH, score=0.85)
        assert policy.assess(packet) == SufficiencyOutcome.ANSWER
