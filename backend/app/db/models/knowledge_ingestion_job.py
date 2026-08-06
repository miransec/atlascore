"""KnowledgeIngestionJob model — state machine for document ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base

# Valid ingestion job statuses — explicit allowlist, no free-form strings.
INGESTION_JOB_STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})

# Valid status transitions.  Only these moves are permitted.
INGESTION_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset({"queued"}),  # retry resets to queued
    "cancelled": frozenset(),
}


class KnowledgeIngestionJob(Base):
    """
    Tracks the lifecycle of a single document version ingestion run.

    State machine: queued → running → succeeded / failed / cancelled
    Failed jobs may be retried (→ queued).
    Succeeded and cancelled jobs are terminal.

    idempotency_key provides DB-level protection against double-submission:
    UNIQUE(organisation_id, workspace_id, idempotency_key).
    """

    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_document_versions.id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_version_id",
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_document_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_workspace_org",
        ),
        UniqueConstraint(
            "organisation_id",
            "workspace_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_jobs_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        ENUM(
            "queued", "running", "succeeded", "failed", "cancelled",
            name="ingestion_job_status", create_type=False,
        ),
        nullable=False, default="queued",
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    version: Mapped[KnowledgeDocumentVersion] = relationship(
        "KnowledgeDocumentVersion", back_populates="ingestion_jobs", lazy="noload"
    )
    document: Mapped[KnowledgeDocument] = relationship(
        "KnowledgeDocument", back_populates="ingestion_jobs", lazy="noload"
    )

    def transition_to(self, new_status: str) -> None:
        """Apply a status transition, raising ValueError if not allowed."""
        allowed = INGESTION_JOB_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid ingestion job transition: {self.status!r} → {new_status!r}. "
                f"Allowed from {self.status!r}: {sorted(allowed)}"
            )
        self.status = new_status

    def __repr__(self) -> str:
        return f"<KnowledgeIngestionJob id={self.id} status={self.status!r}>"


from app.db.models.knowledge_document import KnowledgeDocument  # noqa: E402
from app.db.models.knowledge_document_version import KnowledgeDocumentVersion  # noqa: E402
