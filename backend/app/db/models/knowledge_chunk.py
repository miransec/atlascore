"""KnowledgeChunk model — a single parsed + chunked text segment."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class KnowledgeChunk(Base):
    """
    A deterministic text segment produced by the chunker for a document version.

    chunk_index is 0-based and stable: the same source text with the same
    chunker settings always produces chunks in the same order.

    content_sha256 is hex SHA-256 of the UTF-8 encoded chunk_text.
    Used for within-version deduplication and integrity checks.
    NOT password hashing — use hashlib.sha256().
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_document_versions.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_version_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_workspace_org",
        ),
        UniqueConstraint(
            "version_id",
            "chunk_index",
            name="uq_knowledge_chunks_version_chunk_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Hex SHA-256 of UTF-8(chunk_text) — NOT password hashing.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    version: Mapped[KnowledgeDocumentVersion] = relationship(
        "KnowledgeDocumentVersion", back_populates="chunks", lazy="noload"
    )
    embeddings: Mapped[list[KnowledgeChunkEmbedding]] = relationship(
        "KnowledgeChunkEmbedding", back_populates="chunk", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id} version_id={self.version_id} idx={self.chunk_index}>"


from app.db.models.knowledge_chunk_embedding import KnowledgeChunkEmbedding  # noqa: E402
from app.db.models.knowledge_document_version import KnowledgeDocumentVersion  # noqa: E402
