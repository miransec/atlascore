"""Invitation model — org and workspace invitations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class Invitation(Base):
    """
    An invitation to join an organisation (and optionally a workspace).

    Security guarantees:
    - invited_token is NEVER stored; only token_hash (BLAKE2b with pepper) is persisted.
    - Invitations are single-use: accepted_at is set on first acceptance.
    - Revoked invitations cannot be accepted.
    - Expired invitations (expires_at < now()) cannot be accepted.
    - The role encoded in the invitation cannot be escalated via request modification;
      the service reads the role from this row, never from the request body at acceptance time.
    - organisation_id cannot be changed after creation.

    RLS: protected by organisation_id policy.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        # Prevent duplicate active invitations for the same email+org combination.
        # A new invitation may be created once the previous one expires/is accepted/revoked.
        # (Partial unique index on active invitations enforced in migration.)
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_invitations_organisation_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_invitations_created_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Optional workspace scope — if set, the invitation also creates a workspace membership.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    invited_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # The org role to grant on acceptance. Nullable means "member without role".
    org_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Optional workspace role — only meaningful when workspace_id is set.
    workspace_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # BLAKE2b(INVITATION_TOKEN_PEPPER + raw_token) — raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    organisation: Mapped[Organisation] = relationship("Organisation", lazy="noload")
    created_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by_user_id], lazy="noload"
    )

    @property
    def is_active(self) -> bool:
        """An invitation is active if not accepted, not revoked, and not expired."""

        now = datetime.now(tz=UTC)
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > now

    def __repr__(self) -> str:
        return (
            f"<Invitation id={self.id} email={self.invited_email!r} "
            f"org={self.organisation_id} active={self.is_active}>"
        )


from app.db.models.organisation import Organisation  # noqa: E402
from app.db.models.user import User  # noqa: E402
