"""KnowledgeDocumentVersion model — immutable version snapshot."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class KnowledgeDocumentVersion(Base):
    """
    An immutable snapshot of a document's content at a point in time.

    Each upload creates a new version row.  Versions are never mutated or
    deleted directly; they cascade when the parent document is deleted.

    storage_key is server-generated.  Format:
        {org_id}/{workspace_id}/{document_id}/{version_id}
    It is never derived from uploaded filename.

    content_sha256 is the hex-encoded SHA-256 of the raw file bytes.
    This is content integrity / within-org deduplication — NOT password hashing.
    Use hashlib.sha256(), not bcrypt/argon2.
    """

    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_knowledge_document_versions_document_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_document_versions_workspace_org",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_document_versions_created_by_user_id",
        ),
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_knowledge_document_versions_doc_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Hex SHA-256 of raw file bytes — integrity check, NOT password hashing.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(256), nullable=False)
    # Server-generated. Never derived from original_filename.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    document: Mapped[KnowledgeDocument] = relationship(
        "KnowledgeDocument", back_populates="versions", lazy="noload"
    )
    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        "KnowledgeChunk", back_populates="version", lazy="noload"
    )
    ingestion_jobs: Mapped[list[KnowledgeIngestionJob]] = relationship(
        "KnowledgeIngestionJob", back_populates="version", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocumentVersion id={self.id} "
            f"document_id={self.document_id} v={self.version_number}>"
        )


from app.db.models.knowledge_chunk import KnowledgeChunk  # noqa: E402
from app.db.models.knowledge_document import KnowledgeDocument  # noqa: E402
from app.db.models.knowledge_ingestion_job import KnowledgeIngestionJob  # noqa: E402
