"""Organisation and workspace membership models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base

# ---------------------------------------------------------------------------
# Role values — kept as string constants to match the DB enum 'org_role'
# and 'workspace_role'.  The canonical list lives in app/auth/permissions.py.
# ---------------------------------------------------------------------------
_ORG_ROLES = (
    "owner",
    "administrator",
    "workflow_builder",
    "analyst",
    "operator",
    "viewer",
    "auditor",
)
_WORKSPACE_ROLES = ("administrator", "workflow_builder", "analyst", "operator", "viewer", "auditor")


class OrganisationMembership(Base):
    """
    Links a User to an Organisation with an optional role.

    org_role is nullable.  A NULL org_role means the user is a member of
    the organisation but holds no named role — they can see the organisation
    in their list but cannot perform role-gated actions.

    Exactly one membership per organisation must have role='owner'.
    This is enforced by a DEFERRABLE INITIALLY DEFERRED trigger.
    """

    __tablename__ = "organisation_memberships"
    __table_args__ = (
        UniqueConstraint("organisation_id", "user_id", name="uq_org_memberships_org_user"),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_org_memberships_user_id",
        ),
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_org_memberships_organisation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Nullable: NULL means "member without a named role"
    org_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    user: Mapped[User] = relationship(
        "User", back_populates="organisation_memberships", lazy="noload"
    )
    organisation: Mapped[Organisation] = relationship(
        "Organisation", back_populates="memberships", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<OrganisationMembership org={self.organisation_id} "
            f"user={self.user_id} role={self.org_role!r}>"
        )


class WorkspaceMembership(Base):
    """
    Links a User to a Workspace with a role.

    The composite FK (workspace_id, organisation_id) references
    workspaces(id, organisation_id), which guarantees at the DB level that
    a WorkspaceMembership can never reference a workspace that belongs to a
    different organisation.

    Users must be an OrganisationMembership member before receiving a
    WorkspaceMembership (enforced at the service layer, not FK).
    """

    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_ws_user"),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_ws_memberships_user_id",
        ),
        # Composite FK: workspace_id + organisation_id must match a row in
        # workspaces(id, organisation_id)
        ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_ws_memberships_workspace_org",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    workspace_role: Mapped[str] = mapped_column(String(32), nullable=False)
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
    user: Mapped[User] = relationship("User", lazy="noload")
    workspace: Mapped[Workspace] = relationship(
        "Workspace", back_populates="memberships", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<WorkspaceMembership ws={self.workspace_id} "
            f"user={self.user_id} role={self.workspace_role!r}>"
        )


from app.db.models.organisation import Organisation  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.db.models.workspace import Workspace  # noqa: E402
