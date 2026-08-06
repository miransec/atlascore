"""ServiceAccount and ApiKey models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.engine import Base


class ServiceAccount(Base):
    """
    A non-human identity for programmatic access.

    Security constraints:
    - Service accounts have NO password — they cannot use browser authentication.
    - Permissions must be explicitly assigned via API keys (scopes on ApiKey).
    - Service accounts CANNOT bypass RLS — they are bound to organisation_id.
    - Service accounts cannot receive owner/admin privileges through this mechanism.
    - A disabled service account's API keys are rejected at authentication time.
    """

    __tablename__ = "service_accounts"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id",
            "name",
            name="uq_service_accounts_org_name",
        ),
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_service_accounts_organisation_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_service_accounts_created_by_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Optional workspace scope.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey", back_populates="service_account", cascade="all, delete-orphan", lazy="noload"
    )
    created_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by_user_id], lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<ServiceAccount id={self.id} name={self.name!r} "
            f"org={self.organisation_id} active={self.is_active}>"
        )


class ApiKey(Base):
    """
    A cryptographic API key bound to a ServiceAccount.

    Key format: atk_<organisation_uuid>_<key_prefix>_<secret>
      - organisation_uuid: non-secret FORCE-RLS routing hint
      - key_prefix: 8 hexadecimal chars (public identifier shown in UI)
      - secret: 32 random bytes, base64url-encoded
      - full raw key returned EXACTLY ONCE at creation; never stored

    Storage: only secret_hash (BLAKE2b-256 with API_KEY_PEPPER) is persisted.
    The raw key CANNOT be recovered.

    Scopes: a JSON list of permission strings. Enforced at authentication time.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_prefix", name="uq_api_keys_prefix"),
        ForeignKeyConstraint(
            ["service_account_id"],
            ["service_accounts.id"],
            ondelete="CASCADE",
            name="fk_api_keys_service_account_id",
        ),
        ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_api_keys_organisation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Optional workspace scope — matches the service account's workspace if set.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Public prefix shown in UI (safe to expose): first 8 chars of raw key.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # BLAKE2b-256(key=API_KEY_PEPPER, data=raw_key) — keyed mode; raw key NEVER stored.
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSON list of permission strings e.g. ["org:read", "workspace:read"]
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    service_account: Mapped[ServiceAccount] = relationship(
        "ServiceAccount", back_populates="api_keys", lazy="noload"
    )

    @property
    def is_active(self) -> bool:

        now = datetime.now(tz=UTC)
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)

    def __repr__(self) -> str:
        return (
            f"<ApiKey id={self.id} prefix={self.key_prefix!r} "
            f"sa={self.service_account_id} active={self.is_active}>"
        )


from app.db.models.user import User  # noqa: E402
