"""Organisation model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class Organisation(Base):
    """
    A tenant organisation.

    Each organisation is a fully isolated tenant; all tenant-scoped tables
    carry an organisation_id foreign key, and Row-Level Security policies
    enforce that the application role can only see rows matching the
    transaction-scoped 'app.current_organisation_id' setting.

    Ownership: exactly one OrganisationMembership per organisation must
    have role='owner'.  This is enforced by a DEFERRABLE INITIALLY DEFERRED
    database trigger that fires at transaction commit.
    """

    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique slug for URL/display use — immutable after creation
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    memberships: Mapped[list[OrganisationMembership]] = relationship(
        "OrganisationMembership", back_populates="organisation", lazy="noload"
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        "Workspace", back_populates="organisation", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Organisation id={self.id} slug={self.slug!r}>"


from app.db.models.membership import OrganisationMembership  # noqa: E402
from app.db.models.workspace import Workspace  # noqa: E402
