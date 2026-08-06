"""Workspace model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class Workspace(Base):
    """
    A workspace within an organisation.

    Workspaces are always owned by an organisation.  The composite foreign key
    (workspace_id, organisation_id) is carried by workspace_memberships to
    guarantee DB-level consistency — it is impossible to create a workspace
    membership with a mismatched org.

    The composite FK pattern requires a UNIQUE(id, organisation_id) constraint
    on this table, which is defined here and referenced in the migration.
    """

    __tablename__ = "workspaces"
    __table_args__ = (
        # Required for composite FK from workspace_memberships
        # UNIQUE(id, organisation_id) is added in the migration via DDL.
        # SQLAlchemy ORM doesn't need to declare it here; the migration handles it.
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_workspaces_organisation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    organisation: Mapped[Organisation] = relationship(
        "Organisation", back_populates="workspaces", lazy="noload"
    )
    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        "WorkspaceMembership", back_populates="workspace", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Workspace id={self.id} slug={self.slug!r}>"


from app.db.models.membership import WorkspaceMembership  # noqa: E402
from app.db.models.organisation import Organisation  # noqa: E402
