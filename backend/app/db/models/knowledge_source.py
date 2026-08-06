"""KnowledgeSource model — workspace-scoped ingestion source."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class KnowledgeSource(Base):
    """
    A named source of knowledge documents within a workspace.

    knowledge_source_type is 'manual_upload' for Phase 2A.  Future phases
    may add connector types (e.g. 'confluence', 'google_drive') without schema
    changes.

    The configuration JSONB column MUST NOT contain secrets, tokens, passwords,
    or private keys.  The service layer enforces this at write time.

    Composite FK (workspace_id, organisation_id) guarantees that the workspace
    cannot belong to a different organisation than the source.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sources_workspace_org",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_sources_created_by_user_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "display_name",
            name="uq_knowledge_sources_workspace_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(
        ENUM("manual_upload", name="knowledge_source_type", create_type=False), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
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
    documents: Mapped[list[KnowledgeDocument]] = relationship(
        "KnowledgeDocument", back_populates="source", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeSource id={self.id} display_name={self.display_name!r}>"


from app.db.models.knowledge_document import KnowledgeDocument  # noqa: E402
