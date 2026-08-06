"""
Structured observability logging — Phase 2D.

Emits structured JSON-compatible log events for the three pipeline stages:
  - knowledge ingestion
  - retrieval
  - answering

SECURITY:
  - Evidence content (chunk bodies) is NEVER logged — only metadata.
  - Question text is NEVER logged — only length and a hashed fingerprint.
  - API keys are NEVER logged (enforced by never passing them to these functions).
  - User IDs and org IDs are included only as opaque UUIDs for correlation.
  - All sensitive fields are explicitly excluded from the log record.

Usage:
    from app.core.observability import log_ingestion_event, log_retrieval_event, log_answer_event
    log_retrieval_event(workspace_id=ws_id, result_count=5, duration_ms=42.1)

Events are emitted via Python standard logging at INFO level.
The logger name is "atlascore.events" — configure handlers in the application.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

logger = logging.getLogger("atlascore.events")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fingerprint(text: str) -> str:
    """SHA-256 fingerprint (first 16 hex chars) of a string — for dedup only.
    Never used to reconstruct the original text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _emit(event_type: str, fields: dict[str, Any]) -> None:
    """Emit a structured log event. All values must be JSON-serialisable."""
    record = {"event": event_type, **fields}
    logger.info(record)


# ---------------------------------------------------------------------------
# Ingestion events
# ---------------------------------------------------------------------------


def log_ingestion_started(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    source_id: uuid.UUID,
    document_id: uuid.UUID,
    filename_hash: str,
    content_type: str,
    file_size_bytes: int,
) -> None:
    """Emitted when a document ingestion job begins.

    filename_hash: SHA-256 of the filename (never the raw filename — may contain PII).
    file_size_bytes: File size in bytes (for capacity monitoring).
    """
    _emit(
        "ingestion.started",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "source_id": str(source_id),
            "document_id": str(document_id),
            "filename_hash": filename_hash,
            "content_type": content_type,
            "file_size_bytes": file_size_bytes,
        },
    )


def log_ingestion_completed(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_count: int,
    duration_ms: float,
    embedding_model: str,
) -> None:
    """Emitted when a document ingestion job completes successfully."""
    _emit(
        "ingestion.completed",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "document_id": str(document_id),
            "chunk_count": chunk_count,
            "duration_ms": round(duration_ms, 1),
            "embedding_model": embedding_model,
        },
    )


def log_ingestion_failed(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    document_id: uuid.UUID,
    error_type: str,
    duration_ms: float,
) -> None:
    """Emitted when a document ingestion job fails.

    error_type: Exception class name only. Never include the error message —
    it may contain file content or internal paths.
    """
    _emit(
        "ingestion.failed",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "document_id": str(document_id),
            "error_type": error_type,
            "duration_ms": round(duration_ms, 1),
        },
    )


# ---------------------------------------------------------------------------
# Retrieval events
# ---------------------------------------------------------------------------


def log_retrieval_started(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    query_length: int,
    query_fingerprint: str,
    top_k: int,
) -> None:
    """Emitted when a retrieval request begins.

    query_length: Character count of the normalised query.
    query_fingerprint: SHA-256 hash prefix (for dedup/correlation — NOT the query text).
    """
    _emit(
        "retrieval.started",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "query_length": query_length,
            "query_fingerprint": query_fingerprint,
            "top_k": top_k,
        },
    )


def log_retrieval_completed(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    result_count: int,
    lexical_count: int,
    vector_count: int,
    duration_ms: float,
    embedding_model: str,
) -> None:
    """Emitted when a retrieval request completes.

    result_count: Number of results after hybrid fusion.
    lexical/vector_count: How many candidates came from each channel.
    """
    _emit(
        "retrieval.completed",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "result_count": result_count,
            "lexical_count": lexical_count,
            "vector_count": vector_count,
            "duration_ms": round(duration_ms, 1),
            "embedding_model": embedding_model,
        },
    )


def log_retrieval_failed(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    error_type: str,
    duration_ms: float,
) -> None:
    """Emitted when retrieval fails. Only error_type (class name) — never message."""
    _emit(
        "retrieval.failed",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "error_type": error_type,
            "duration_ms": round(duration_ms, 1),
        },
    )


# ---------------------------------------------------------------------------
# Answering events
# ---------------------------------------------------------------------------


def log_answer_started(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    question_length: int,
    question_fingerprint: str,
    top_k: int,
) -> None:
    """Emitted when a grounded answer request begins.

    question_length: Character count after normalisation.
    question_fingerprint: Hash prefix — NOT the question text.
    """
    _emit(
        "answer.started",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "question_length": question_length,
            "question_fingerprint": question_fingerprint,
            "top_k": top_k,
        },
    )


def log_answer_abstained(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    reason: str,
    evidence_band: str,
    duration_ms: float,
) -> None:
    """Emitted when the grounded answer pipeline abstains (no/weak evidence)."""
    _emit(
        "answer.abstained",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "reason": reason,
            "evidence_band": evidence_band,
            "duration_ms": round(duration_ms, 1),
        },
    )


def log_answer_completed(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    evidence_band: str,
    citation_count: int,
    suspicious_count: int,
    provider_id: str,
    model_id: str,
    duration_ms: float,
) -> None:
    """Emitted when a grounded answer is successfully generated.

    Evidence content and answer text are NEVER included.
    """
    _emit(
        "answer.completed",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "evidence_band": evidence_band,
            "citation_count": citation_count,
            "suspicious_count": suspicious_count,
            "provider_id": provider_id,
            "model_id": model_id,
            "duration_ms": round(duration_ms, 1),
        },
    )


def log_answer_provider_failure(
    *,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    error_type: str,
    provider_id: str,
    model_id: str,
    attempt_count: int,
    duration_ms: float,
) -> None:
    """Emitted when the provider fails and PROVIDER_FAILURE is returned.

    error_type: Exception class name only. Never the message (may expose internals).
    API keys are NEVER logged — they are not passed to this function.
    """
    _emit(
        "answer.provider_failure",
        {
            "workspace_id": str(workspace_id),
            "organisation_id": str(organisation_id),
            "error_type": error_type,
            "provider_id": provider_id,
            "model_id": model_id,
            "attempt_count": attempt_count,
            "duration_ms": round(duration_ms, 1),
        },
    )


def make_query_fingerprint(query: str) -> str:
    """Return a SHA-256 fingerprint for a query. For correlation only — not reversible."""
    return _fingerprint(query)
