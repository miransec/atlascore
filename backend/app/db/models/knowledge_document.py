"""KnowledgeDocument model — a document within a knowledge source."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class KnowledgeDocument(Base):
    """
    A document uploaded to a knowledge source.

    original_filename is display metadata only and is NEVER used as a
    filesystem path or storage key.  The server generates storage_key
    independently for each document version.

    Documents may be archived (soft-deleted) but are never hard-deleted
    from this table — versions and chunks cascade from the version rows.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_documents_workspace_org",
        ),
        ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            ondelete="CASCADE",
            name="fk_knowledge_documents_source_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_documents_created_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Display metadata — never used as filesystem path.
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(256), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
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
    source: Mapped[KnowledgeSource] = relationship(
        "KnowledgeSource", back_populates="documents", lazy="noload"
    )
    versions: Mapped[list[KnowledgeDocumentVersion]] = relationship(
        "KnowledgeDocumentVersion", back_populates="document", lazy="noload"
    )
    ingestion_jobs: Mapped[list[KnowledgeIngestionJob]] = relationship(
        "KnowledgeIngestionJob", back_populates="document", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id} filename={self.original_filename!r}>"


from app.db.models.knowledge_document_version import KnowledgeDocumentVersion  # noqa: E402
from app.db.models.knowledge_ingestion_job import KnowledgeIngestionJob  # noqa: E402
from app.db.models.knowledge_source import KnowledgeSource  # noqa: E402
