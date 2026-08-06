"""
Authentication service — register, login, select-org, refresh, logout.

All methods are stateless with respect to the service class; dependencies
(password service, JWT service, etc.) are injected.

SECURITY INVARIANTS:
- user_id is NEVER sourced from request data at the select-org step.
- Org membership is re-verified against the live DB on every authenticated request.
- All auth events are audited transactionally or via SECURITY DEFINER function.
- On refresh token reuse: entire family revoked before returning 401.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import PasswordService
from app.auth.pre_auth import PreAuthSessionReuseError, PreAuthSessionService
from app.auth.refresh import RefreshTokenReuseError, RefreshTokenService
from app.auth.tokens import JWTService
from app.db.models.auth import RefreshToken
from app.db.models.membership import OrganisationMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.services.audit import AuditService


class RegistrationError(Exception):
    """Raised when registration fails (email taken, slug taken, etc.)."""


class AuthenticationError(Exception):
    """Raised when credentials are invalid or account is inactive."""


class OrgSelectionError(Exception):
    """Raised when org selection fails (not a member, pre-auth expired, etc.)."""


class AuthService:
    """Core authentication operations."""

    def __init__(
        self,
        password_service: PasswordService,
        jwt_service: JWTService,
        refresh_service: RefreshTokenService,
        pre_auth_service: PreAuthSessionService,
    ) -> None:
        self._pwd = password_service
        self._jwt = jwt_service
        self._refresh = refresh_service
        self._pre_auth = pre_auth_service

    async def register(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        full_name: str,
        organisation_name: str,
        organisation_slug: str,
        client_ip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[User, Organisation]:
        """
        Register a new user and create their first organisation.

        Atomic single transaction:
          INSERT users + INSERT organisations + INSERT org_memberships(owner)
          + INSERT workspaces (default)

        Raises RegistrationError if email or slug is already taken.
        """
        # Check for existing email
        email_check = await session.execute(select(User.id).where(User.email == email))
        if email_check.scalar_one_or_none() is not None:
            raise RegistrationError(f"Email address is already registered: {email!r}")

        # Check for existing slug
        slug_check = await session.execute(
            select(Organisation.id).where(Organisation.slug == organisation_slug)
        )
        if slug_check.scalar_one_or_none() is not None:
            raise RegistrationError(f"Organisation slug is already taken: {organisation_slug!r}")

        # Hash password
        password_hash = self._pwd.hash(password)

        # Create user
        user = User(
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            pepper_version=1,
            is_active=True,
            email_verified=False,
        )
        session.add(user)
        await session.flush()  # get user.id

        # Create organisation.
        #
        # The organisations table is FORCE-RLS protected and its tenant key is
        # the organisation id itself.  Bootstrap therefore has to choose the id
        # first and establish that id as transaction-local RLS context before
        # inserting the row.
        org_id = uuid.uuid4()
        await session.execute(
            text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user.id)},
        )

        org = Organisation(
            id=org_id,
            slug=organisation_slug,
            display_name=organisation_name,
            is_active=True,
        )
        session.add(org)
        await session.flush()

        # Create owner membership
        membership = OrganisationMembership(
            organisation_id=org.id,
            user_id=user.id,
            org_role="owner",
        )
        session.add(membership)

        # Create default workspace
        workspace = Workspace(
            organisation_id=org.id,
            slug="default",
            display_name="Default Workspace",
            is_active=True,
        )
        session.add(workspace)
        await session.flush()

        # Audit — transactional, org context is the new org
        # Note: this is a special case; we're inside the registration transaction
        AuditService.emit_transactional(
            session,
            event_type="org.created",
            organisation_id=org.id,
            actor_user_id=user.id,
            event_data={
                "organisation_slug": organisation_slug,
                "user_email": email,
            },
            request_id=request_id,
            client_ip=client_ip,
        )

        return user, org

    async def login_step1(
        self,
        raw_session: AsyncSession,
        *,
        email: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> tuple[User, list[OrganisationMembership], str]:
        """
        Login step 1: verify credentials, return org list and pre-auth token.

        Returns (user, memberships, pre_auth_raw_token).
        The pre-auth token must be set as an HttpOnly cookie.
        On failure, raises AuthenticationError.

        AUDIT: login_failed events are written via emit_independent
        (no org context at this point).
        """
        # Look up user by email
        result = await raw_session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None or not user.is_active:
            # Write global audit event via SECURITY DEFINER
            await AuditService.emit_independent(
                raw_session,
                event_type="auth.login_failed",
                event_data={"email": email, "reason": "user_not_found"},
                request_id=request_id,
                client_ip=client_ip,
                outcome="failure",
            )
            await raw_session.commit()
            raise AuthenticationError("Invalid email or password.")

        # Verify password
        if not self._pwd.verify(password, user.password_hash, user.pepper_version):
            await AuditService.emit_independent(
                raw_session,
                event_type="auth.login_failed",
                actor_user_id=user.id,
                event_data={"email": email, "reason": "invalid_password"},
                request_id=request_id,
                client_ip=client_ip,
                outcome="failure",
            )
            await raw_session.commit()
            raise AuthenticationError("Invalid email or password.")

        # OrganisationMembership has a user-self RLS policy for login discovery.
        # Establish the authenticated user GUC before enumerating memberships.
        await raw_session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user.id)},
        )

        # Load the authenticated user's membership rows through the dedicated
        # user-self RLS policy.  Do not join the FORCE-RLS organisations table
        # here: before organisation selection there is deliberately no single
        # app.current_organisation_id context.  Step 2 re-validates the selected
        # organisation and its active status under that tenant's context.
        mem_result = await raw_session.execute(
            select(OrganisationMembership).where(OrganisationMembership.user_id == user.id)
        )
        memberships = list(mem_result.scalars().all())

        # Create pre-auth session
        raw_token = await self._pre_auth.create(
            raw_session,
            user_id=user.id,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        # Lazily rehash if needed
        if self._pwd.needs_rehash(user.password_hash, user.pepper_version):
            new_hash, new_version = self._pwd.rehash(password)
            await raw_session.execute(
                update(User)
                .where(User.id == user.id)
                .values(password_hash=new_hash, pepper_version=new_version)
            )

        # Update last_login_at
        await raw_session.execute(
            update(User).where(User.id == user.id).values(last_login_at=datetime.now(UTC))
        )

        await raw_session.commit()
        return user, memberships, raw_token

    async def select_organisation(
        self,
        raw_session: AsyncSession,
        *,
        pre_auth_raw_token: str,
        organisation_id: uuid.UUID,
        client_ip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[User, Organisation, OrganisationMembership, str, RefreshToken]:
        """
        Login step 2: consume pre-auth session, verify org membership, issue tokens.

        user_id is sourced ONLY from the pre-auth session row — never from
        the request.

        Returns (user, org, membership, raw_refresh_token, refresh_token_row).
        The caller issues the JWT (it needs the refresh_token.jti).

        Raises:
          OrgSelectionError — pre-auth expired, already consumed, not a member
          PreAuthSessionReuseError → converted to OrgSelectionError with audit
        """
        # Consume pre-auth session atomically
        try:
            pas = await self._pre_auth.consume(raw_session, raw_token=pre_auth_raw_token)
        except PreAuthSessionReuseError as exc:
            # Audit the anomaly
            await AuditService.emit_independent(
                raw_session,
                event_type="auth.pre_auth_session_reused",
                actor_user_id=exc.user_id,
                event_data={"session_id": str(exc.session_id)},
                request_id=request_id,
                client_ip=client_ip,
                outcome="failure",
            )
            await raw_session.commit()
            raise OrgSelectionError("Session has already been used. Please log in again.") from exc

        if pas is None:
            raise OrgSelectionError("Session has expired. Please log in again.")

        user_id = pas.user_id

        # The requested organisation comes from the authenticated pre-auth flow.
        # Establish transaction-local tenant/user context before touching
        # FORCE-RLS organisation or membership rows.
        await raw_session.execute(
            text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
            {"org_id": str(organisation_id)},
        )
        await raw_session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )

        # Load user
        user_result = await raw_session.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise OrgSelectionError("User account is inactive.")

        # Load org
        org_result = await raw_session.execute(
            select(Organisation).where(
                Organisation.id == organisation_id,
                Organisation.is_active.is_(True),
            )
        )
        org = org_result.scalar_one_or_none()
        if org is None:
            raise OrgSelectionError("Organisation not found.")

        # Verify live membership — this is the re-verification step
        mem_result = await raw_session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.user_id == user_id,
                OrganisationMembership.organisation_id == organisation_id,
            )
        )
        membership = mem_result.scalar_one_or_none()
        if membership is None:
            raise OrgSelectionError("You are not a member of this organisation.")

        # Issue refresh token (new family)
        raw_refresh, rt = await self._refresh.create(
            raw_session,
            user_id=user_id,
            organisation_id=organisation_id,
            client_ip=client_ip,
        )

        # Audit org selection — transactional with this commit
        AuditService.emit_transactional(
            raw_session,
            event_type="org.organisation_selected",
            organisation_id=organisation_id,
            actor_user_id=user_id,
            event_data={"org_slug": org.slug},
            request_id=request_id,
            client_ip=client_ip,
        )

        await raw_session.commit()
        return user, org, membership, raw_refresh, rt

    async def refresh_tokens(
        self,
        raw_session: AsyncSession,
        *,
        raw_refresh_token: str,
        client_ip: str | None = None,
        request_id: str | None = None,
    ) -> tuple[str, RefreshToken] | None:
        """
        Rotate refresh token and return new (raw_token, RefreshToken).

        Returns None if the token is not found or expired.
        Raises RefreshTokenReuseError (caught by caller, which returns 401).
        """
        try:
            result = await self._refresh.rotate(
                raw_session,
                raw_token=raw_refresh_token,
                client_ip=client_ip,
            )
        except RefreshTokenReuseError as exc:
            await AuditService.emit_independent(
                raw_session,
                event_type="auth.token_reuse_detected",
                event_data={"family_id": str(exc.family_id)},
                request_id=request_id,
                client_ip=client_ip,
                outcome="failure",
            )
            await raw_session.commit()
            raise

        await raw_session.commit()
        return result

    async def logout(
        self,
        raw_session: AsyncSession,
        *,
        family_id: uuid.UUID,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Revoke the current refresh token family and audit the event."""
        await self._refresh.revoke_family(
            raw_session,
            family_id=family_id,
            organisation_id=organisation_id,
        )

        AuditService.emit_transactional(
            raw_session,
            event_type="auth.logout",
            organisation_id=organisation_id,
            actor_user_id=user_id,
            event_data={},
            request_id=request_id,
            client_ip=client_ip,
        )
        await raw_session.commit()

    async def logout_all(
        self,
        raw_session: AsyncSession,
        *,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Revoke all refresh token families for user+org and audit."""
        await self._refresh.revoke_all_for_user_org(
            raw_session,
            user_id=user_id,
            organisation_id=organisation_id,
        )
        AuditService.emit_transactional(
            raw_session,
            event_type="auth.logout",
            organisation_id=organisation_id,
            actor_user_id=user_id,
            event_data={"all_sessions": True},
            request_id=request_id,
            client_ip=client_ip,
        )
        await raw_session.commit()
