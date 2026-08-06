"""
Tests for Phase 2D observability logging.

Security assertions — what must NEVER appear in log records:
  - Actual question text
  - Actual evidence/chunk content
  - API keys or secrets
  - Raw filenames (may contain PII)

What MUST appear:
  - event type
  - workspace_id, organisation_id (as opaque UUIDs)
  - Non-sensitive metadata (counts, durations, model names)
  - Fingerprint (SHA-256 prefix) rather than the raw text

All tests capture log records via logging.Handler — no I/O, no network.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from app.core.observability import (
    _fingerprint,
    log_answer_abstained,
    log_answer_completed,
    log_answer_provider_failure,
    log_answer_started,
    log_ingestion_completed,
    log_ingestion_failed,
    log_ingestion_started,
    log_retrieval_completed,
    log_retrieval_failed,
    log_retrieval_started,
    make_query_fingerprint,
)

# ---------------------------------------------------------------------------
# Log capture helper
# ---------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    """Capture log records emitted to 'atlascore.events' logger."""

    def __init__(self):
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        # The message is the dict passed to logger.info(record)
        msg = record.getMessage()
        self.records.append({"raw": msg, "record": record})


def _capture() -> tuple[_CapturingHandler, logging.Logger]:
    handler = _CapturingHandler()
    obs_logger = logging.getLogger("atlascore.events")
    obs_logger.addHandler(handler)
    obs_logger.setLevel(logging.DEBUG)
    return handler, obs_logger


def _remove_handler(handler: _CapturingHandler) -> None:
    obs_logger = logging.getLogger("atlascore.events")
    obs_logger.removeHandler(handler)


# Shared UUIDs
_WS_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
_ORG_ID = uuid.UUID("22222222-0000-0000-0000-000000000002")
_SRC_ID = uuid.UUID("33333333-0000-0000-0000-000000000003")
_DOC_ID = uuid.UUID("44444444-0000-0000-0000-000000000004")


# ---------------------------------------------------------------------------
# _fingerprint helper
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_returns_16_hex_chars(self) -> None:
        fp = _fingerprint("hello world")
        assert len(fp) == 16
        int(fp, 16)  # must be valid hex

    def test_deterministic(self) -> None:
        assert _fingerprint("test") == _fingerprint("test")

    def test_different_inputs_different_outputs(self) -> None:
        assert _fingerprint("hello") != _fingerprint("world")

    def test_matches_sha256_prefix(self) -> None:
        text = "my question text"
        expected = hashlib.sha256(text.encode()).hexdigest()[:16]
        assert _fingerprint(text) == expected

    def test_empty_string(self) -> None:
        fp = _fingerprint("")
        assert len(fp) == 16

    def test_make_query_fingerprint_alias(self) -> None:
        assert make_query_fingerprint("query") == _fingerprint("query")


# ---------------------------------------------------------------------------
# Ingestion events
# ---------------------------------------------------------------------------


class TestIngestionEvents:
    def test_started_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_ingestion_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                source_id=_SRC_ID,
                document_id=_DOC_ID,
                filename_hash=_fingerprint("report.pdf"),
                content_type="application/pdf",
                file_size_bytes=102400,
            )
            assert len(handler.records) == 1
            raw = handler.records[0]["raw"]
            assert "ingestion.started" in raw
        finally:
            _remove_handler(handler)

    def test_started_contains_workspace_id(self) -> None:
        handler, _ = _capture()
        try:
            log_ingestion_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                source_id=_SRC_ID,
                document_id=_DOC_ID,
                filename_hash=_fingerprint("secret_report.pdf"),
                content_type="application/pdf",
                file_size_bytes=1024,
            )
            raw = handler.records[0]["raw"]
            assert str(_WS_ID) in raw
        finally:
            _remove_handler(handler)

    def test_started_never_logs_raw_filename(self) -> None:
        """The API only accepts a hash — raw filename never reaches logger."""
        handler, _ = _capture()
        raw_filename = "personal_employee_data_confidential.pdf"
        try:
            log_ingestion_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                source_id=_SRC_ID,
                document_id=_DOC_ID,
                filename_hash=_fingerprint(raw_filename),
                content_type="application/pdf",
                file_size_bytes=500,
            )
            raw = handler.records[0]["raw"]
            assert raw_filename not in raw
        finally:
            _remove_handler(handler)

    def test_completed_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_ingestion_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                document_id=_DOC_ID,
                chunk_count=42,
                duration_ms=320.5,
                embedding_model="text-embedding-3-small",
            )
            raw = handler.records[0]["raw"]
            assert "ingestion.completed" in raw
            assert "42" in raw
        finally:
            _remove_handler(handler)

    def test_failed_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_ingestion_failed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                document_id=_DOC_ID,
                error_type="ValueError",
                duration_ms=10.0,
            )
            raw = handler.records[0]["raw"]
            assert "ingestion.failed" in raw
            assert "ValueError" in raw
        finally:
            _remove_handler(handler)

    def test_failed_never_logs_error_message(self) -> None:
        """error_type is a class name; the actual message (possible internals) is excluded."""
        handler, _ = _capture()
        secret_path = "/var/storage/very-secret-path/document.pdf"
        try:
            log_ingestion_failed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                document_id=_DOC_ID,
                error_type="FileNotFoundError",  # Only the class, not the message
                duration_ms=5.0,
            )
            raw = handler.records[0]["raw"]
            assert secret_path not in raw
        finally:
            _remove_handler(handler)


# ---------------------------------------------------------------------------
# Retrieval events
# ---------------------------------------------------------------------------


class TestRetrievalEvents:
    def test_started_event_emitted(self) -> None:
        handler, _ = _capture()
        query_text = "What is the refund policy?"
        try:
            log_retrieval_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                query_length=len(query_text),
                query_fingerprint=_fingerprint(query_text),
                top_k=10,
            )
            raw = handler.records[0]["raw"]
            assert "retrieval.started" in raw
            # Raw query text must NOT appear
            assert query_text not in raw
        finally:
            _remove_handler(handler)

    def test_started_uses_fingerprint_not_text(self) -> None:
        handler, _ = _capture()
        private_query = "secret company merger plan Q4 2025"
        fp = _fingerprint(private_query)
        try:
            log_retrieval_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                query_length=len(private_query),
                query_fingerprint=fp,
                top_k=5,
            )
            raw = handler.records[0]["raw"]
            assert private_query not in raw
            assert fp in raw  # fingerprint IS present
        finally:
            _remove_handler(handler)

    def test_completed_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_retrieval_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                result_count=8,
                lexical_count=5,
                vector_count=3,
                duration_ms=45.2,
                embedding_model="text-embedding-3-small",
            )
            raw = handler.records[0]["raw"]
            assert "retrieval.completed" in raw
            assert "result_count" in raw
        finally:
            _remove_handler(handler)

    def test_failed_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_retrieval_failed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                error_type="TimeoutError",
                duration_ms=30000.0,
            )
            raw = handler.records[0]["raw"]
            assert "retrieval.failed" in raw
        finally:
            _remove_handler(handler)


# ---------------------------------------------------------------------------
# Answering events
# ---------------------------------------------------------------------------


class TestAnsweringEvents:
    def test_started_event_emitted(self) -> None:
        handler, _ = _capture()
        question = "What is the capital of France?"
        try:
            log_answer_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                question_length=len(question),
                question_fingerprint=_fingerprint(question),
                top_k=10,
            )
            raw = handler.records[0]["raw"]
            assert "answer.started" in raw
            # Actual question text must NOT appear
            assert question not in raw
        finally:
            _remove_handler(handler)

    def test_started_never_logs_question_text(self) -> None:
        handler, _ = _capture()
        sensitive_question = "What is the M&A target for Q2 2026?"
        try:
            log_answer_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                question_length=len(sensitive_question),
                question_fingerprint=_fingerprint(sensitive_question),
                top_k=20,
            )
            raw = handler.records[0]["raw"]
            assert sensitive_question not in raw
            assert "M&A" not in raw
        finally:
            _remove_handler(handler)

    def test_started_contains_question_length(self) -> None:
        handler, _ = _capture()
        question = "short"
        try:
            log_answer_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                question_length=len(question),
                question_fingerprint=_fingerprint(question),
                top_k=5,
            )
            raw = handler.records[0]["raw"]
            assert str(len(question)) in raw
        finally:
            _remove_handler(handler)

    def test_abstained_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_answer_abstained(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                reason="no_evidence",
                evidence_band="none",
                duration_ms=12.5,
            )
            raw = handler.records[0]["raw"]
            assert "answer.abstained" in raw
            assert "no_evidence" in raw
        finally:
            _remove_handler(handler)

    def test_completed_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_answer_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                evidence_band="high",
                citation_count=3,
                suspicious_count=0,
                provider_id="deterministic-test",
                model_id="deterministic-test",
                duration_ms=250.0,
            )
            raw = handler.records[0]["raw"]
            assert "answer.completed" in raw
            assert "citation_count" in raw
        finally:
            _remove_handler(handler)

    def test_completed_never_logs_answer_text(self) -> None:
        """log_answer_completed has no answer_text parameter — by design."""
        handler, _ = _capture()
        try:
            log_answer_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                evidence_band="high",
                citation_count=1,
                suspicious_count=0,
                provider_id="openai",
                model_id="gpt-4o",
                duration_ms=500.0,
            )
            raw = handler.records[0]["raw"]
            # These strings should never appear in a real answer log
            assert "capital of France" not in raw
            assert "refund policy" not in raw
        finally:
            _remove_handler(handler)

    def test_provider_failure_event_emitted(self) -> None:
        handler, _ = _capture()
        try:
            log_answer_provider_failure(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                error_type="AnswerProviderError",
                provider_id="openai",
                model_id="gpt-4o",
                attempt_count=3,
                duration_ms=61000.0,
            )
            raw = handler.records[0]["raw"]
            assert "answer.provider_failure" in raw
            assert "AnswerProviderError" in raw
            assert "attempt_count" in raw
        finally:
            _remove_handler(handler)

    def test_provider_failure_never_logs_api_key(self) -> None:
        """The API key is not a parameter — it structurally cannot be logged."""
        handler, _ = _capture()
        fake_key = "sk-test-super-secret-key-12345"
        try:
            # This function has no api_key parameter by design.
            log_answer_provider_failure(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                error_type="AnswerProviderError",
                provider_id="openai",
                model_id="gpt-4o",
                attempt_count=2,
                duration_ms=30000.0,
            )
            raw = handler.records[0]["raw"]
            assert fake_key not in raw
            assert "sk-" not in raw
        finally:
            _remove_handler(handler)

    def test_provider_failure_contains_only_error_type_not_message(self) -> None:
        """error_type is the class name only — error messages can contain internals."""
        handler, _ = _capture()
        try:
            log_answer_provider_failure(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                error_type="TimeoutError",
                provider_id="anthropic",
                model_id="claude-opus-4-5",
                attempt_count=3,
                duration_ms=90000.0,
            )
            raw = handler.records[0]["raw"]
            assert "TimeoutError" in raw
            # The error message text is NOT here
            assert "ECONNREFUSED" not in raw
            assert "timed out after" not in raw
        finally:
            _remove_handler(handler)


# ---------------------------------------------------------------------------
# Event structure consistency
# ---------------------------------------------------------------------------


class TestEventStructure:
    def test_all_events_include_workspace_id(self) -> None:
        """Every event type must include workspace_id for correlation."""
        events_and_calls = [
            lambda: log_answer_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                question_length=5,
                question_fingerprint="abc",
                top_k=10,
            ),
            lambda: log_answer_abstained(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                reason="no_evidence",
                evidence_band="none",
                duration_ms=0.0,
            ),
            lambda: log_answer_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                evidence_band="high",
                citation_count=1,
                suspicious_count=0,
                provider_id="deterministic-test",
                model_id="deterministic-test",
                duration_ms=0.0,
            ),
            lambda: log_answer_provider_failure(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                error_type="Err",
                provider_id="openai",
                model_id="gpt-4o",
                attempt_count=1,
                duration_ms=0.0,
            ),
            lambda: log_retrieval_started(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                query_length=5,
                query_fingerprint="abc",
                top_k=10,
            ),
            lambda: log_retrieval_completed(
                workspace_id=_WS_ID,
                organisation_id=_ORG_ID,
                result_count=3,
                lexical_count=2,
                vector_count=1,
                duration_ms=0.0,
                embedding_model="mock",
            ),
        ]

        for emit_fn in events_and_calls:
            handler, _ = _capture()
            try:
                emit_fn()
                assert handler.records, "No log record emitted"
                raw = handler.records[0]["raw"]
                assert str(_WS_ID) in raw, f"workspace_id missing from event: {raw}"
            finally:
                _remove_handler(handler)
