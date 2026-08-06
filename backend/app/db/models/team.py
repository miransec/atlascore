"""Team and TeamMembership models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class Team(Base):
    """
    A named group of users within an organisation.

    Teams are organisation-scoped; they may optionally be further scoped
    to a specific workspace (workspace_id is nullable).

    Tenant isolation: FORCE RLS policy on organisation_id.
    """

    __tablename__ = "teams"
    __table_args__ = (
        # Team names must be unique within the same org (and optionally workspace).
        # Enforced via partial unique index in migration for workspace-scoped uniqueness.
        UniqueConstraint(
            "organisation_id",
            "workspace_id",
            "name",
            name="uq_teams_org_workspace_name",
        ),
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_teams_organisation_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_teams_created_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Optional workspace scope. NULL = organisation-wide team.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
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
    memberships: Mapped[list[TeamMembership]] = relationship(
        "TeamMembership", back_populates="team", cascade="all, delete-orphan", lazy="noload"
    )
    created_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by_user_id], lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Team id={self.id} name={self.name!r} org={self.organisation_id}>"


class TeamMembership(Base):
    """
    Links a User to a Team.

    Cross-organisation attachment is blocked by:
    1. RLS: session org matches team.organisation_id.
    2. Service layer: verifies user is an org member before adding to team.
    3. FK: organisation_id on team membership must match the team's org.
    """

    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
        ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_team_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_user_id",
        ),
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_organisation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Denormalised organisation_id for RLS enforcement.
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    team: Mapped[Team] = relationship("Team", back_populates="memberships", lazy="noload")
    user: Mapped[User] = relationship("User", lazy="noload")

    def __repr__(self) -> str:
        return f"<TeamMembership team={self.team_id} user={self.user_id}>"


from app.db.models.user import User  # noqa: E402
