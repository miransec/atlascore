"""
Invitation service — create, accept, revoke, list invitations.

Security model:
- Raw invitation token is generated with secrets.token_urlsafe(32).
- Only BLAKE2b(key=INVITATION_TOKEN_PEPPER, data=raw_token) is stored.
- The raw token is returned to the caller ONCE at creation; they must
  deliver it to the invitee (in production, via email).
- At acceptance time, the raw token is re-hashed and compared against
  the stored hash — the raw token itself is never persisted anywhere.
- Role from the invitation row is used at acceptance — NOT from the request.
- Organisation and workspace IDs cannot be altered between creation and acceptance.
- Expired, revoked, and already-accepted invitations are all rejected.
- Duplicate active invitations for the same email+org are prevented by a
  partial unique index (uq_invitations_active_email_org).

Audit events:
- invitation.created — emitted by the caller (endpoint) after create().
- invitation.accepted — emitted by the caller after accept().
- invitation.revoked  — emitted by the caller after revoke().
- invitation.expired  — emitted INSIDE accept() when expiry is detected,
  via AuditService.emit_tenant_independent().

  DURABILITY: emit_tenant_independent() opens a SEPARATE AsyncSession with
  its own database connection and transaction, inserts the AuditEvent, and
  commits atomically before returning.  This guarantees that the audit row
  is durable (fully committed to Postgres) before InvitationExpiredError is
  raised.  Because the audit session is completely independent of the caller's
  session, a rollback on the caller's transaction cannot affect the audit row.

  This is fundamentally different from session.flush(): flush() writes within
  the SAME transaction.  If the caller's transaction rolls back, a flushed-but-
  not-committed audit row is also rolled back.  flush() does NOT equal commit
  and does NOT guarantee durability.

  organisation_id for the audit row is taken from invitation.organisation_id
  (a trusted server-side value loaded from the DB) — never from client input.

  NOT a global event; it is NOT emitted via fn_audit_insert_global.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models.invitation import Invitation
from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.services.audit import AuditService


class InvitationError(Exception):
    """Base class for invitation service errors."""


class InvitationNotFoundError(InvitationError):
    pass


class InvitationExpiredError(InvitationError):
    pass


class InvitationRevokedError(InvitationError):
    pass


class InvitationAlreadyAcceptedError(InvitationError):
    pass


class InvitationEmailMismatchError(InvitationError):
    pass


class InvitationDuplicateError(InvitationError):
    pass


class InvitationService:
    def __init__(self, settings: Settings) -> None:
        self._pepper = settings.INVITATION_TOKEN_PEPPER

    # -------------------------------------------------------------------------
    # Token hashing
    # -------------------------------------------------------------------------

    def _hash_token(self, raw_token: str) -> str:
        """BLAKE2b(pepper + raw_token) — 64 hex chars."""
        key = self._pepper.encode()
        return hashlib.blake2b(raw_token.encode(), key=key[:64], digest_size=32).hexdigest()

    @staticmethod
    def _extract_organisation_hint(raw_token: str) -> uuid.UUID | None:
        """Extract the non-secret tenant routing hint from an invitation token."""
        hint, sep, secret = raw_token.partition(".")
        if not sep or not secret:
            return None
        try:
            return uuid.UUID(hint)
        except ValueError:
            return None

    @staticmethod
    async def _set_org_context(session: AsyncSession, organisation_id: uuid.UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(organisation_id)},
        )

    def _generate_token(self, organisation_id: uuid.UUID) -> tuple[str, str]:
        """Generate a tenant-routable raw token and its keyed hash."""
        raw = f"{organisation_id}.{secrets.token_urlsafe(32)}"
        return raw, self._hash_token(raw)

    # -------------------------------------------------------------------------
    # Create
    # -------------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        invited_email: str,
        org_role: str | None,
        workspace_id: uuid.UUID | None = None,
        workspace_role: str | None = None,
        created_by_user_id: uuid.UUID,
        expires_in_hours: int = 72,
    ) -> tuple[Invitation, str]:
        """
        Create an invitation.

        Returns (Invitation, raw_token). The raw_token must be delivered to the
        invitee via some out-of-band channel (email in production, response body
        in development).  It is not persisted anywhere.
        """
        await self._set_org_context(session, organisation_id)
        raw_token, token_hash = self._generate_token(organisation_id)

        # Check for existing active invitation (partial unique index will also catch this
        # at DB level, but we give a cleaner error here).
        existing = await self._find_active_by_email_org(
            session, email=invited_email, organisation_id=organisation_id
        )
        if existing is not None:
            raise InvitationDuplicateError(
                f"An active invitation for {invited_email!r} already exists in this organisation. "
                "Revoke the existing invitation before sending a new one."
            )

        now = datetime.now(tz=UTC)
        invitation = Invitation(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            invited_email=invited_email.lower().strip(),
            org_role=org_role,
            workspace_role=workspace_role,
            token_hash=token_hash,
            created_by_user_id=created_by_user_id,
            expires_at=now + timedelta(hours=expires_in_hours),
        )
        session.add(invitation)
        await session.flush()  # get ID without committing
        return invitation, raw_token

    # -------------------------------------------------------------------------
    # Accept
    # -------------------------------------------------------------------------

    async def accept(
        self,
        session: AsyncSession,
        *,
        raw_token: str,
        accepting_user_id: uuid.UUID,
        accepting_user_email: str,
        request_id: str | None = None,
        client_ip: str | None = None,
        audit_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> Invitation:
        """
        Accept an invitation.

        Security:
        - Hashes raw_token and looks up by hash — token is never stored.
        - Validates accepting user's email matches invited_email.
        - Validates invitation is active (not expired, revoked, or already accepted).
        - Creates org membership (and workspace membership if applicable) atomically.
        - Marks invitation as accepted in the same transaction.
        - Role is taken from the invitation row, NOT from any request parameter.

        Parameters:
        - audit_session_factory: When provided, invitation.expired audit events are
          written via AuditService.emit_tenant_independent() (separate session,
          guaranteed durable regardless of caller transaction state).
          When None (e.g. in unit tests without a live DB), the audit emit is
          skipped — tests that need durability proof must supply a factory.
        """
        organisation_id = self._extract_organisation_hint(raw_token)
        if organisation_id is None:
            raise InvitationNotFoundError("Invalid invitation token.")
        await self._set_org_context(session, organisation_id)
        token_hash = self._hash_token(raw_token)

        result = await session.execute(
            select(Invitation).where(Invitation.token_hash == token_hash)
        )
        invitation = result.scalar_one_or_none()

        if invitation is None:
            raise InvitationNotFoundError("Invalid invitation token.")

        now = datetime.now(tz=UTC)

        # Expire check.
        # Emit invitation.expired via emit_tenant_independent() — a SEPARATE
        # session/transaction that commits atomically before this exception is
        # raised.  This guarantees the audit row is durable regardless of what
        # the caller does with its surrounding transaction.
        #
        # organisation_id is sourced from invitation.organisation_id (a trusted
        # server-side DB value), never from client-supplied input.
        #
        # IMPORTANT: session.flush() is NOT used here.  flush() writes within the
        # SAME transaction; a subsequent rollback would remove the audit row.
        # flush() != commit; flush() != durable.  Only an independent committed
        # transaction guarantees durability.
        if invitation.expires_at < now:
            if audit_session_factory is not None:
                await AuditService.emit_tenant_independent(
                    audit_session_factory,
                    event_type="invitation.expired",
                    organisation_id=invitation.organisation_id,
                    actor_user_id=accepting_user_id,
                    event_data={"invitation_id": str(invitation.id)},
                    request_id=request_id,
                    client_ip=client_ip,
                    outcome="failure",
                )
            raise InvitationExpiredError("This invitation has expired.")

        if invitation.revoked_at is not None:
            raise InvitationRevokedError("This invitation has been revoked.")

        if invitation.accepted_at is not None:
            raise InvitationAlreadyAcceptedError("This invitation has already been accepted.")

        # Email match check
        if invitation.invited_email.lower() != accepting_user_email.lower().strip():
            raise InvitationEmailMismatchError(
                "The invitation was issued for a different email address."
            )

        # Mark as accepted FIRST (single-use enforcement)
        invitation.accepted_at = now
        invitation.updated_at = now

        # Create org membership if not already a member
        existing_org_membership = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == invitation.organisation_id,
                OrganisationMembership.user_id == accepting_user_id,
            )
        )
        existing_member = existing_org_membership.scalar_one_or_none()

        if existing_member is None:
            org_membership = OrganisationMembership(
                id=uuid.uuid4(),
                organisation_id=invitation.organisation_id,
                user_id=accepting_user_id,
                org_role=invitation.org_role,
            )
            session.add(org_membership)

        # Create workspace membership if invitation is workspace-scoped
        if invitation.workspace_id is not None and invitation.workspace_role is not None:
            existing_ws = await session.execute(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == invitation.workspace_id,
                    WorkspaceMembership.user_id == accepting_user_id,
                )
            )
            if existing_ws.scalar_one_or_none() is None:
                ws_membership = WorkspaceMembership(
                    id=uuid.uuid4(),
                    workspace_id=invitation.workspace_id,
                    organisation_id=invitation.organisation_id,
                    user_id=accepting_user_id,
                    workspace_role=invitation.workspace_role,
                )
                session.add(ws_membership)

        await session.flush()
        return invitation

    # -------------------------------------------------------------------------
    # Revoke
    # -------------------------------------------------------------------------

    async def revoke(
        self,
        session: AsyncSession,
        *,
        invitation_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> Invitation:
        """Revoke an active invitation."""
        await self._set_org_context(session, organisation_id)
        result = await session.execute(
            select(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.organisation_id == organisation_id,
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise InvitationNotFoundError("Invitation not found.")
        if invitation.revoked_at is not None:
            raise InvitationRevokedError("Invitation is already revoked.")
        if invitation.accepted_at is not None:
            raise InvitationAlreadyAcceptedError("Cannot revoke an already-accepted invitation.")

        now = datetime.now(tz=UTC)
        invitation.revoked_at = now
        invitation.updated_at = now
        await session.flush()
        return invitation

    # -------------------------------------------------------------------------
    # List
    # -------------------------------------------------------------------------

    async def list_for_org(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Invitation], int]:
        """List invitations for an organisation with pagination."""
        await self._set_org_context(session, organisation_id)
        base_query = select(Invitation).where(Invitation.organisation_id == organisation_id)
        if active_only:
            now = datetime.now(tz=UTC)
            base_query = base_query.where(
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )

        from sqlalchemy import func

        count_result = await session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        items_result = await session.execute(
            base_query.order_by(Invitation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(items_result.scalars()), total

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    async def _find_active_by_email_org(
        self,
        session: AsyncSession,
        *,
        email: str,
        organisation_id: uuid.UUID,
    ) -> Invitation | None:
        await self._set_org_context(session, organisation_id)
        now = datetime.now(tz=UTC)
        result = await session.execute(
            select(Invitation).where(
                Invitation.invited_email == email.lower().strip(),
                Invitation.organisation_id == organisation_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
        )
        return result.scalar_one_or_none()
