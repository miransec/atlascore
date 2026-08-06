"""User model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class User(Base):
    """
    Platform-level user account.

    Users exist outside of any organisation; membership rows link users
    to organisations and workspaces.  Passwords are stored as Argon2id
    hashes with a server-side pepper — never in plaintext.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Pepper version enables rotation: when ARGON2_PEPPER_VERSION increments,
    # old-pepper hashes are lazily rehashed on successful login.
    pepper_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships (back-populated from child tables)
    organisation_memberships: Mapped[list[OrganisationMembership]] = relationship(
        "OrganisationMembership", back_populates="user", lazy="noload"
    )
    pre_auth_sessions: Mapped[list[PreAuthSession]] = relationship(
        "PreAuthSession", back_populates="user", lazy="noload"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken", back_populates="user", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


# Deferred imports to avoid circular references in relationship annotations
from app.db.models.auth import PreAuthSession, RefreshToken  # noqa: E402
from app.db.models.membership import OrganisationMembership  # noqa: E402
