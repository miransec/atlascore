"""Authentication session models: PreAuthSession, RefreshToken, Session."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class PreAuthSession(Base):
    """
    Single-purpose server-side session between login step 1 and step 2.

    SECURITY PROPERTIES:
    - Only the SHA-256 hash of the raw token is stored (never the raw token).
    - The raw token is issued as an HttpOnly, Secure, SameSite=Strict cookie
      scoped to the /api/v1/auth/select-organisation endpoint only.
    - The session is consumed atomically via UPDATE … WHERE consumed_at IS NULL
      RETURNING — replayed tokens are detected because consumed_at is set.
    - The session expires after PRE_AUTH_SESSION_EXPIRE_MINUTES (5 minutes).
    - user_id is derived exclusively from this server-side row — it is never
      accepted from the step-2 request body.

    NOT subject to RLS — these rows are not tenant-scoped.
    Audit writes for pre-auth anomalies go via SECURITY DEFINER function.
    """

    __tablename__ = "pre_auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256(PRE_AUTH_SESSION_PEPPER + raw_token) — never the raw token
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # IP and user-agent for audit purposes — never used for access decisions
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Relationship
    user: Mapped[User] = relationship("User", back_populates="pre_auth_sessions", lazy="noload")

    def __repr__(self) -> str:
        return f"<PreAuthSession id={self.id} user={self.user_id} consumed={self.consumed_at}>"


class RefreshToken(Base):
    """
    Refresh token with family-based rotation and reuse detection.

    SECURITY PROPERTIES:
    - Only the BLAKE2b(REFRESH_TOKEN_PEPPER + raw_token) hash is stored.
    - Tokens are organised into families.  On every use, the old token is
      revoked and a new token in the same family is issued.
    - If a revoked token is replayed (reuse detected), the entire family is
      immediately revoked, forcing the user to log in again.
    - is_active=False means the token has been rotated or explicitly revoked.
    - family_revoked_at is set when the entire family is invalidated.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Token family ID — all rotations of one login session share the same family
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # jti — unique identifier embedded in the JWT access token for CSRF binding
    jti: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    # BLAKE2b(REFRESH_TOKEN_PEPPER + raw_token) — never the raw token
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    family_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # IP and user-agent for audit
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Relationship
    user: Mapped[User] = relationship("User", back_populates="refresh_tokens", lazy="noload")

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} family={self.family_id} active={self.is_active}>"


class Session(Base):
    """
    Optional server-side session record for auditing active access token sessions.

    Not used for authentication decisions — JWTs are stateless.
    Used for: logout-all (revoke all refresh token families), session listing.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Links session to a refresh token family
    refresh_family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return f"<Session id={self.id} user={self.user_id}>"


from app.db.models.user import User  # noqa: E402
