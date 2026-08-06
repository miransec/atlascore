"""
AuditEvent model — append-only audit log.

SECURITY PROPERTIES:
- The application database role has INSERT-only permission on this table.
  No UPDATE or DELETE is granted.  This is a database GRANT, not a
  convention — the application cannot delete or modify audit records.
- Security-critical events (auth.login, auth.login_failed, etc.) are written
  transactionally with the business operation.  They cannot be lost.
- Global auth events (login failures before org context is established) are
  written via a SECURITY DEFINER database function that bypasses RLS.
  This avoids the anti-pattern of a permissive 'organisation_id IS NULL' RLS rule.
- organisation_id is nullable for global events (pre-org-selection login failures).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.engine import Base


class AuditEvent(Base):
    """
    Immutable audit event.

    Written by:
    - AuditService.emit_transactional() within the same DB transaction as
      the business operation (for all normal tenant-scoped events).
    - AuditService.emit_independent() via SECURITY DEFINER function (for
      global events such as auth.login_failed before org is selected).

    event_type values are defined in app/services/audit.py and must match
    the Phase 1A event list in PLAN.md.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # Efficient queries by organisation + time for audit dashboards
        Index("ix_audit_events_org_created", "organisation_id", "created_at"),
        # Queries by actor
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
        # Queries by event type
        Index("ix_audit_events_event_type", "event_type"),
        {"implicit_returning": False},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: None for global events (pre-org-selection login failures, etc.)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Nullable: None for events where no authenticated user exists yet
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Structured event data — redacted before storage (no secrets, passwords, tokens)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Request ID for correlation with application logs
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Client IP — stored for security review; may be None if not available
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    # outcome: 'success' | 'failure' | 'pending'
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} type={self.event_type!r} "
            f"org={self.organisation_id} outcome={self.outcome!r}>"
        )
