"""
Pre-authentication session service.

SECURITY DESIGN:
Between login step 1 (credential verification) and step 2 (org selection),
user_id is carried in a server-side session stored by SHA-256 hash.

Step 1 → issues a raw token (HttpOnly cookie) and stores SHA-256(pepper + raw).
Step 2 → atomically consumes the session (UPDATE … WHERE consumed_at IS NULL)
          and returns the user_id from the stored row.

The step-2 request body contains ONLY organisation_id.  user_id is NEVER
accepted from request data.  This prevents user_id injection attacks.

Cookie attributes:
  HttpOnly: true
  Secure: true (required in production)
  SameSite: Strict
  Path: /api/v1/auth/select-organisation
  Max-Age: PRE_AUTH_SESSION_EXPIRE_MINUTES * 60

Replay detection:
  If consumed_at IS NOT NULL, the session was already used.
  The anomaly is logged via the audit system (emit_independent) and the
  request is rejected with 401.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.auth import PreAuthSession


def _hash_token(pepper: str, raw_token: str) -> str:
    """SHA-256(pepper + raw_token) — hex digest."""
    return hashlib.sha256((pepper + raw_token).encode()).hexdigest()


class PreAuthSessionService:
    """Create, consume, and validate pre-authentication sessions."""

    def __init__(self, settings: Settings) -> None:
        self._pepper = settings.PRE_AUTH_SESSION_PEPPER
        self._expire_minutes = settings.PRE_AUTH_SESSION_EXPIRE_MINUTES

    def _hash(self, raw_token: str) -> str:
        return _hash_token(self._pepper, raw_token)

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """
        Create a pre-auth session and return the raw token.

        The raw token is set as the pre-auth cookie.
        Only the hash is stored.
        """
        raw_token = secrets.token_urlsafe(48)
        token_hash = self._hash(raw_token)
        expires_at = datetime.now(UTC) + timedelta(minutes=self._expire_minutes)

        pas = PreAuthSession(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        session.add(pas)
        await session.flush()
        return raw_token

    async def consume(
        self,
        session: AsyncSession,
        *,
        raw_token: str,
    ) -> PreAuthSession | None:
        """
        Atomically consume a pre-auth session.

        Returns the PreAuthSession row on success.
        Returns None if the token is not found or has expired.
        Raises PreAuthSessionReuseError if consumed_at IS NOT NULL (replay attack).

        Atomicity: the UPDATE … WHERE consumed_at IS NULL RETURNING pattern
        ensures exactly one successful consumption even under concurrent requests.
        """
        token_hash = self._hash(raw_token)
        now = datetime.now(UTC)

        # First, look up the session row
        result = await session.execute(
            select(PreAuthSession).where(PreAuthSession.token_hash == token_hash)
        )
        pas = result.scalar_one_or_none()

        if pas is None:
            return None

        if pas.consumed_at is not None:
            # Replay attack: session already consumed
            raise PreAuthSessionReuseError(session_id=pas.id, user_id=pas.user_id)

        if pas.expires_at.replace(tzinfo=UTC) < now:
            # Expired — not consumed, just expired
            return None

        # Atomically mark as consumed
        update_result = await session.execute(
            update(PreAuthSession)
            .where(
                PreAuthSession.id == pas.id,
                PreAuthSession.consumed_at.is_(None),
            )
            .values(consumed_at=now)
            .returning(PreAuthSession.id)
        )
        consumed_id = update_result.scalar_one_or_none()

        if consumed_id is None:
            # Race: another request consumed it first
            raise PreAuthSessionReuseError(session_id=pas.id, user_id=pas.user_id)

        pas.consumed_at = now
        return pas


class PreAuthSessionReuseError(Exception):
    """
    Raised when a pre-auth session token is replayed.

    The session was already consumed — either by a legitimate request that
    already selected an org, or by an attacker replaying the cookie.
    The caller must log this via the audit system and reject the request.
    """

    def __init__(self, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session_id = session_id
        self.user_id = user_id
        super().__init__(f"Pre-auth session {session_id} already consumed for user {user_id}")
