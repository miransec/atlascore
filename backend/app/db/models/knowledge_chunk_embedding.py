"""KnowledgeChunkEmbedding model — vector embedding for a chunk.

Phase 2B fix: embedding column is now pgvector `vector` type (migration 0006).
The ORM maps it as list[float] via VectorType (app.db.types).

SECURITY:
- The embedding column is never returned to clients (retrieval schemas omit it).
- Dimension invariant is enforced at write time in the knowledge service.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base
from app.db.types import VectorType


class KnowledgeChunkEmbedding(Base):
    """
    A vector embedding produced for a knowledge chunk by a specific model.

    The embedding column stores a pgvector `vector` value, mapped to Python
    as list[float] via VectorType.  No JSON serialisation is used.

    model_id identifies the embedding model (e.g. "deterministic-test-v1",
    "text-embedding-3-small").  One chunk may have multiple embeddings from
    different models (UNIQUE(chunk_id, model_id)).

    Dimension invariant:
        len(embedding) == dimensions is enforced at write time in KnowledgeService.
        This prevents persisting a 768-element vector with dimensions=1536.
    """

    __tablename__ = "knowledge_chunk_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunks.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_embeddings_chunk_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_embeddings_workspace_org",
        ),
        UniqueConstraint(
            "chunk_id",
            "model_id",
            name="uq_knowledge_chunk_embeddings_chunk_model",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Native pgvector vector column.  Mapped to list[float] by VectorType.
    # Never expose to clients — retrieval schemas intentionally omit this field.
    embedding: Mapped[list[float]] = mapped_column(VectorType, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    chunk: Mapped[KnowledgeChunk] = relationship(
        "KnowledgeChunk", back_populates="embeddings", lazy="noload"
    )

    def get_vector(self) -> list[float]:
        """Return the stored pgvector value as a plain Python float list."""
        return [float(value) for value in self.embedding]

    def __repr__(self) -> str:
        return (
            f"<KnowledgeChunkEmbedding id={self.id} "
            f"chunk_id={self.chunk_id} model={self.model_id!r} "
            f"dims={self.dimensions}>"
        )


from app.db.models.knowledge_chunk import KnowledgeChunk  # noqa: E402
