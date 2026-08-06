"""
Audit service — three emission paths.

TRANSACTIONAL (emit_transactional):
  Used for all tenant-scoped events.  The audit row is inserted in the
  SAME database transaction as the business operation.  If the business
  operation rolls back, the audit row rolls back too — no phantom events.

INDEPENDENT (emit_independent):
  Used for global events that occur before an organisation context exists,
  such as:
  - auth.login_failed (no org context yet)
  - auth.pre_auth_session_expired
  - auth.pre_auth_session_reused

  These CANNOT use the main audit table under the tenant RLS policy because
  no valid organisation context has been established.

  Implementation: these events are written via a SECURITY DEFINER database
  function (fn_audit_insert_global) that runs with the privileges of the
  migration role (which owns the audit table and bypasses RLS).  This
  function accepts only specific event types (allowlisted) and performs
  its own input validation.

  NEVER implement this by adding a permissive 'organisation_id IS NULL'
  RLS rule to the main audit table.  That rule would grant full read access
  to any session that has not set app.current_organisation_id, which is a
  serious security flaw.

TENANT-INDEPENDENT (emit_tenant_independent):
  Used for tenant-scoped events that MUST be durable regardless of whether
  the caller's surrounding transaction rolls back.  The canonical case is
  invitation.expired — the expiry audit row must survive even though
  InvitationExpiredError causes the calling transaction to roll back.

  This path opens a SEPARATE AsyncSession (not the caller's session),
  inserts the AuditEvent row, and commits atomically before returning.
  Because it uses an entirely distinct database connection and transaction,
  a rollback on the caller's session cannot affect the audit row.

  Security constraints:
  - organisation_id MUST come from a trusted server-side source (e.g. an
    Invitation row already loaded from the database) — never from client input.
  - Allowed event types are restricted to TENANT_INDEPENDENT_EVENT_TYPES.
  - Raw tokens, hashes, peppers must never appear in event_data (enforced
    by _sanitise_event_data, same as the other paths).
  - invitation.expired is NOT added to fn_audit_insert_global (GLOBAL path);
    it uses this new path which does NOT bypass RLS via SECURITY DEFINER.
    Instead it writes through a normal session with explicit organisation_id.

Event types (Phase 1A + 1B):
  Global events (written via SECURITY DEFINER fn_audit_insert_global, no org context):
    auth.login_failed, auth.pre_auth_session_expired,
    auth.pre_auth_session_reused, auth.token_reuse_detected

  Tenant-independent durable events (written via emit_tenant_independent):
    invitation.expired

  Transactional tenant-scoped events (written via emit_transactional):
    auth.login, auth.logout, auth.logout_all, auth.token_refreshed,
    auth.org_selected, auth.password_changed
    org.created, org.updated, org.membership_added, org.membership_removed,
    org.role_changed, org.ownership_transferred, org.context_switched
    workspace.created, workspace.membership_added, workspace.membership_removed,
    workspace.role_changed, workspace.context_switched
    invitation.created, invitation.revoked, invitation.accepted
    team.created, team.updated, team.deleted, team.member_added, team.member_removed
    service_account.created, service_account.disabled, service_account.enabled
    api_key.created, api_key.revoked, api_key.rotated, api_key.used

DO NOT STORE:
  passwords, raw access tokens, raw refresh tokens, raw pre-auth tokens,
  CSRF secrets, private model reasoning, full sensitive request bodies.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.audit import AuditEvent

# ---------------------------------------------------------------------------
# Allowlisted global event types that can be written via the SECURITY DEFINER
# function.  Only these types are accepted by fn_audit_insert_global.
#
# CRITICAL: invitation.expired is NOT in this list.  It is a tenant-scoped
# event emitted via emit_tenant_independent(), which uses a separate session
# with an explicit organisation_id — not the SECURITY DEFINER bypass.
# ---------------------------------------------------------------------------
GLOBAL_EVENT_TYPES = frozenset(
    {
        "auth.login_failed",
        "auth.pre_auth_session_expired",
        "auth.pre_auth_session_reused",
        "auth.token_reuse_detected",
    }
)

# ---------------------------------------------------------------------------
# Allowlisted tenant-scoped event types that require durable emission via
# a separate session/transaction (emit_tenant_independent).
#
# These events must survive even when the caller's surrounding transaction
# rolls back — they are written to an independent DB connection.
#
# Restriction: only events where the organisation_id can be sourced from a
# trusted server-side entity (e.g. a loaded DB row) are eligible.
# ---------------------------------------------------------------------------
TENANT_INDEPENDENT_EVENT_TYPES = frozenset(
    {
        "invitation.expired",
    }
)

# ---------------------------------------------------------------------------
# All Phase 1A + Phase 1B event types
# ---------------------------------------------------------------------------
ALL_EVENT_TYPES = frozenset(
    {
        # Phase 1A auth events
        "auth.login",
        "auth.login_failed",
        "auth.logout",
        "auth.logout_all",
        "auth.token_refreshed",
        "auth.token_reuse_detected",
        "auth.pre_auth_session_expired",
        "auth.pre_auth_session_reused",
        "auth.org_selected",
        "auth.password_changed",
        # Phase 1A org events
        "org.created",
        "org.updated",
        "org.membership_added",
        "org.membership_removed",
        "org.role_changed",
        "org.ownership_transferred",
        "org.organisation_selected",
        # Phase 1A workspace events
        "workspace.created",
        "workspace.membership_added",
        "workspace.membership_removed",
        "workspace.role_changed",
        # Phase 1B invitation events
        "invitation.created",
        "invitation.revoked",
        "invitation.accepted",
        "invitation.expired",
        # Phase 1B team events
        "team.created",
        "team.updated",
        "team.deleted",
        "team.member_added",
        "team.member_removed",
        # Phase 1B service account events
        "service_account.created",
        "service_account.disabled",
        "service_account.enabled",
        # Phase 1B API key events
        "api_key.created",
        "api_key.revoked",
        "api_key.rotated",
        "api_key.used",
        # Phase 1B membership administration events
        "org.member_role_changed",
        "workspace.member_role_changed",
        "workspace.member_added",
        "workspace.member_removed",
        "org.context_switched",
        "workspace.context_switched",
        # Phase 2A knowledge events
        "knowledge.source.created",
        "knowledge.source.updated",
        "knowledge.document.uploaded",
        "knowledge.document.archived",
        "knowledge.ingestion.succeeded",
        "knowledge.ingestion.failed",
        "knowledge.ingestion.retry_requested",
    }
)


class AuditService:
    """Write audit events — three paths: transactional, independent, tenant-independent."""

    @staticmethod
    async def emit_tenant_independent(
        session_factory: async_sessionmaker[AsyncSession],
        *,
        event_type: str,
        organisation_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        event_data: dict[str, Any] | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
        outcome: str = "failure",
    ) -> None:
        """
        Write a tenant-scoped audit event that MUST be durable regardless of
        whether the caller's surrounding transaction rolls back.

        Opens a SEPARATE AsyncSession with its own connection and transaction,
        inserts the AuditEvent, and commits atomically.  The caller's session
        state is completely unaffected.

        Security constraints (non-negotiable):
        - event_type MUST be in TENANT_INDEPENDENT_EVENT_TYPES (explicitly restricted).
        - organisation_id MUST come from a trusted server-side source (e.g. a loaded
          DB row), never from client-supplied request data.
        - Raw tokens, hashes, and peppers are blocked by _sanitise_event_data.
        - This path does NOT use fn_audit_insert_global (no SECURITY DEFINER bypass).
        - This path does NOT bypass RLS — the INSERT is a normal statement; the
          audit table's RLS policies apply unless bypassed by migration role grants.

        Canonical use: invitation.expired emitted inside InvitationService.accept()
        before InvitationExpiredError is raised.  The calling request's transaction
        is about to roll back; this separate commit guarantees the row survives.
        """
        if event_type not in TENANT_INDEPENDENT_EVENT_TYPES:
            raise ValueError(
                f"Event type {event_type!r} is not in TENANT_INDEPENDENT_EVENT_TYPES. "
                f"Allowed: {sorted(TENANT_INDEPENDENT_EVENT_TYPES)}. "
                "For global (pre-org) events use emit_independent(); "
                "for transactional events use emit_transactional()."
            )

        sanitised = _sanitise_event_data(event_data or {})

        event_row = AuditEvent(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            event_data=sanitised,
            request_id=request_id,
            client_ip=client_ip,
            outcome=outcome,
        )

        async with session_factory() as independent_session:
            try:
                await independent_session.execute(
                    text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                    {"oid": str(organisation_id)},
                )
                if actor_user_id is not None:
                    await independent_session.execute(
                        text("SELECT set_config('app.current_user_id', :uid, true)"),
                        {"uid": str(actor_user_id)},
                    )
                independent_session.add(event_row)
                await independent_session.commit()
            except Exception:
                await independent_session.rollback()
                raise

    @staticmethod
    def emit_transactional(
        session: AsyncSession,
        *,
        event_type: str,
        organisation_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        event_data: dict[str, Any] | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
        outcome: str = "success",
    ) -> None:
        """
        Add an audit event to the current session's unit of work.

        The event commits or rolls back with the surrounding transaction.
        Call this for all tenant-scoped events.

        Does NOT flush or await — the caller's session.commit() writes it.
        """
        if event_type not in ALL_EVENT_TYPES:
            raise ValueError(f"Unknown audit event type: {event_type!r}")

        event = AuditEvent(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            event_data=_sanitise_event_data(event_data or {}),
            request_id=request_id,
            client_ip=client_ip,
            outcome=outcome,
        )
        session.add(event)

    @staticmethod
    async def emit_independent(
        session: AsyncSession,
        *,
        event_type: str,
        actor_user_id: uuid.UUID | None = None,
        event_data: dict[str, Any] | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
        outcome: str = "failure",
    ) -> None:
        """
        Write a global audit event via the SECURITY DEFINER function.

        Used for events that occur before an organisation context is
        established (login failures, pre-auth anomalies).

        The SECURITY DEFINER function fn_audit_insert_global:
        - Runs as the migration role (bypasses RLS).
        - Accepts only the event types in GLOBAL_EVENT_TYPES.
        - Sets organisation_id = NULL.
        - Validates inputs within the function body.

        This session must NOT have an organisation context set.
        """
        if event_type not in GLOBAL_EVENT_TYPES:
            raise ValueError(
                f"Event type {event_type!r} is not allowed as a global audit event. "
                f"Allowed types: {sorted(GLOBAL_EVENT_TYPES)}"
            )

        sanitised = _sanitise_event_data(event_data or {})

        await session.execute(
            text(
                "SELECT fn_audit_insert_global("
                "  CAST(:event_type AS text),"
                "  CAST(:actor_user_id AS uuid),"
                "  CAST(:event_data AS jsonb),"
                "  CAST(:request_id AS text),"
                "  CAST(:client_ip AS text),"
                "  CAST(:outcome AS text)"
                ")"
            ),
            {
                "event_type": event_type,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "event_data": _jsonb_dumps(sanitised),
                "request_id": request_id,
                "client_ip": client_ip,
                "outcome": outcome,
            },
        )
        await session.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fields that must never appear in audit event_data
_REDACTED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "current_password",
        "new_password",
        "token",
        "raw_token",
        "invitation_token",
        "access_token",
        "refresh_token",
        "pre_auth_token",
        "csrf_token",
        "secret",
        "secret_hash",
        "token_hash",
        "api_key",
        "raw_key",
        "key_hash",
        "pepper",
        "private_key",
    }
)


def _sanitise_event_data(data: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive keys from event data before storage."""
    return {k: "[REDACTED]" if k.lower() in _REDACTED_KEYS else v for k, v in data.items()}


def _jsonb_dumps(data: dict[str, Any]) -> str:
    """Serialise dict to JSON string for JSONB parameter binding."""
    import orjson

    return orjson.dumps(data).decode()
