"""
Refresh token service: family-based rotation with reuse detection.

SECURITY PROPERTIES:
- Raw tokens are never stored.  Only BLAKE2b(REFRESH_TOKEN_PEPPER + raw) is stored.
- Tokens are organised into families.  All rotations of one login session share
  a family_id.
- On every use:
  1. The incoming token hash is looked up.
  2. If found and is_active=True: rotate (deactivate old, issue new in same family).
  3. If found and is_active=False (already rotated): REUSE DETECTED — revoke the
     entire family, force re-login.
  4. If not found: invalid token.
- Logout invalidates a specific family.
- Logout-all invalidates all families for a user+org.
- The refresh cookie is: HttpOnly, Secure, SameSite=Lax, Path=/api/v1/auth.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.auth import RefreshToken


def _hash_token(pepper: str, raw_token: str) -> str:
    """BLAKE2b(pepper + raw_token) — 64-byte hex digest."""
    h = hashlib.blake2b(digest_size=32)
    h.update((pepper + raw_token).encode())
    return h.hexdigest()


class RefreshTokenService:
    """Manage refresh token lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self._pepper = settings.REFRESH_TOKEN_PEPPER
        self._expire_days = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS

    def _hash(self, raw_token: str) -> str:
        return _hash_token(self._pepper, raw_token)

    @staticmethod
    def _extract_organisation_hint(raw_token: str) -> uuid.UUID | None:
        """Return the non-secret organisation routing hint from a raw token."""
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

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
        family_id: uuid.UUID | None = None,
        client_ip: str | None = None,
    ) -> tuple[str, RefreshToken]:
        """
        Create a new refresh token.

        Returns (raw_token, RefreshToken ORM object).
        raw_token is the value to set in the cookie — it is NOT stored.
        family_id=None creates a new family (new login session).
        """
        # The organisation UUID is a non-secret routing hint.  FORCE RLS means
        # a fresh refresh request must establish tenant context before it can
        # look up the token row.  The entire raw token (hint + secret) is still
        # keyed-hashed and authenticated.
        raw_token = f"{organisation_id}.{secrets.token_urlsafe(48)}"
        await self._set_org_context(session, organisation_id)
        jti = str(uuid.uuid4())
        fid = family_id if family_id is not None else uuid.uuid4()
        token_hash = self._hash(raw_token)
        expires_at = datetime.now(UTC) + timedelta(days=self._expire_days)

        rt = RefreshToken(
            user_id=user_id,
            organisation_id=organisation_id,
            family_id=fid,
            jti=jti,
            token_hash=token_hash,
            is_active=True,
            expires_at=expires_at,
            client_ip=client_ip,
        )
        session.add(rt)
        await session.flush()  # get id without committing
        return raw_token, rt

    async def rotate(
        self,
        session: AsyncSession,
        *,
        raw_token: str,
        client_ip: str | None = None,
    ) -> tuple[str, RefreshToken] | None:
        """
        Rotate a refresh token.

        Returns (new_raw_token, new_RefreshToken) on success.
        Returns None if the token is not found.
        Raises RefreshTokenReuseError if a revoked token is replayed
        (entire family revoked).
        """
        organisation_id = self._extract_organisation_hint(raw_token)
        if organisation_id is None:
            return None
        await self._set_org_context(session, organisation_id)
        token_hash = self._hash(raw_token)
        result = await session.execute(
            # SELECT FOR UPDATE acquires a row-level lock so that a second
            # concurrent rotation attempt for the same token blocks until
            # the first transaction commits or rolls back.  This prevents
            # a race condition where two requests both see is_active=True
            # and both successfully "rotate" the same token.
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        old_rt = result.scalar_one_or_none()

        if old_rt is None or old_rt.organisation_id != organisation_id:
            return None

        now = datetime.now(UTC)

        if not old_rt.is_active:
            # REUSE DETECTED — revoke entire family
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == old_rt.family_id,
                    RefreshToken.is_active.is_(True),
                )
                .values(is_active=False, revoked_at=now, family_revoked_at=now)
            )
            await session.flush()
            raise RefreshTokenReuseError(family_id=old_rt.family_id)

        if old_rt.expires_at.replace(tzinfo=UTC) < now:
            old_rt.is_active = False
            old_rt.revoked_at = now
            await session.flush()
            return None

        # Deactivate the old token
        old_rt.is_active = False
        old_rt.revoked_at = now
        await session.flush()

        # Issue new token in the same family
        return await self.create(
            session,
            user_id=old_rt.user_id,
            organisation_id=old_rt.organisation_id,
            family_id=old_rt.family_id,
            client_ip=client_ip,
        )

    async def revoke_family(
        self,
        session: AsyncSession,
        *,
        family_id: uuid.UUID,
        organisation_id: uuid.UUID | None = None,
    ) -> None:
        """Revoke all tokens in a family (logout)."""
        if organisation_id is not None:
            await self._set_org_context(session, organisation_id)
        now = datetime.now(UTC)
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.is_active.is_(True),
            )
            .values(is_active=False, revoked_at=now)
        )
        await session.flush()

    async def revoke_all_for_user_org(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> None:
        """Revoke all refresh tokens for a user in an organisation (logout-all)."""
        await self._set_org_context(session, organisation_id)
        now = datetime.now(UTC)
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.organisation_id == organisation_id,
                RefreshToken.is_active.is_(True),
            )
            .values(is_active=False, revoked_at=now)
        )
        await session.flush()

    async def find_active_by_raw_token(
        self,
        session: AsyncSession,
        *,
        raw_token: str,
    ) -> RefreshToken | None:
        """Find an active refresh token by raw value.  Returns None if not found or expired."""
        organisation_id = self._extract_organisation_hint(raw_token)
        if organisation_id is None:
            return None
        await self._set_org_context(session, organisation_id)
        token_hash = self._hash(raw_token)
        now = datetime.now(UTC)
        result = await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_active.is_(True),
                RefreshToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()


class RefreshTokenReuseError(Exception):
    """
    Raised when a revoked refresh token is replayed.

    The entire token family has already been revoked at the point this
    exception is raised.  The caller must force the user to re-authenticate.
    """

    def __init__(self, family_id: uuid.UUID) -> None:
        self.family_id = family_id
        super().__init__(f"Refresh token reuse detected for family {family_id}")
