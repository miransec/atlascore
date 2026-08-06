"""
Service account and API key service.

API key security design:
  Format: atk_<organisation_uuid>_<key_prefix>_<secret>
    - organisation_uuid: non-secret routing hint used to establish FORCE-RLS context
    - key_prefix: 8 lowercase hexadecimal characters (public, shown in UI)
    - secret: 32 random bytes encoded as URL-safe base64
    - the complete raw key is keyed-hashed and returned exactly once

  Storage:
    - secret_hash = BLAKE2b-256(key=API_KEY_PEPPER, data=raw_key)
      in keyed mode; only this value is persisted.
    - The pepper is used as a BLAKE2b cryptographic key (not concatenated as data).
    - The raw key is returned EXACTLY ONCE at creation and CANNOT be retrieved later.

  Verification at auth time:
    1. Parse key_prefix from presented key.
    2. Look up ApiKey row by key_prefix.
    3. Re-hash presented key and compare to secret_hash with hmac.compare_digest.
    4. Check is_active (not revoked, not expired).
    5. Check service account is_active.
    6. Check scopes if required_scopes provided.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.service_account import ApiKey, ServiceAccount


class ServiceAccountError(Exception):
    """Base class for service account errors."""


class ServiceAccountNotFoundError(ServiceAccountError):
    pass


class ServiceAccountDisabledError(ServiceAccountError):
    pass


class ServiceAccountDuplicateError(ServiceAccountError):
    pass


class ApiKeyError(Exception):
    pass


class ApiKeyNotFoundError(ApiKeyError):
    pass


class ApiKeyInvalidError(ApiKeyError):
    pass


class ApiKeyRevokedError(ApiKeyError):
    pass


class ApiKeyExpiredError(ApiKeyError):
    pass


class ApiKeyScopeError(ApiKeyError):
    pass


class ServiceAccountService:
    def __init__(self, settings: Settings) -> None:
        self._api_key_pepper = settings.API_KEY_PEPPER

    @staticmethod
    async def _set_org_context(session: AsyncSession, organisation_id: uuid.UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(organisation_id)},
        )

    @staticmethod
    def _extract_org_and_prefix(raw_key: str) -> tuple[uuid.UUID, str] | None:
        parts = raw_key.split("_", 3)
        if len(parts) != 4 or parts[0] != "atk":
            return None
        try:
            organisation_id = uuid.UUID(parts[1])
        except ValueError:
            return None
        return organisation_id, parts[2]

    # -------------------------------------------------------------------------
    # API key hashing
    # -------------------------------------------------------------------------

    def _hash_key(self, raw_key: str) -> str:
        """BLAKE2b-256(key=API_KEY_PEPPER, data=raw_key) — hex string.

        The pepper is used as the BLAKE2b cryptographic key parameter, not
        concatenated as data.  This is semantically equivalent to an HMAC but
        uses BLAKE2b's native keyed-hash mode, which avoids length-extension
        attacks and is more efficient.  Maximum pepper length is 64 bytes
        (BLAKE2b key size limit); pepper is truncated to that limit.
        """
        pepper_bytes = self._api_key_pepper.encode()
        return hashlib.blake2b(raw_key.encode(), key=pepper_bytes[:64], digest_size=32).hexdigest()

    def _generate_api_key(self, organisation_id: uuid.UUID) -> tuple[str, str, str]:
        """
        Generate (raw_key, key_prefix, secret_hash).

        raw_key format: atk_<organisation_uuid>_<prefix>_<secret>.
        Returns the triple; only key_prefix and secret_hash are stored.
        """
        # Hex deliberately excludes '_' so parsing the underscore-delimited
        # routing envelope is unambiguous.
        key_prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        raw_key = f"atk_{organisation_id}_{key_prefix}_{secret}"
        secret_hash = self._hash_key(raw_key)
        return raw_key, key_prefix, secret_hash

    def _verify_key(self, raw_key: str, secret_hash: str) -> bool:
        """Constant-time comparison of re-hashed key against stored hash."""
        computed = self._hash_key(raw_key)
        return hmac.compare_digest(computed, secret_hash)

    # -------------------------------------------------------------------------
    # Service accounts
    # -------------------------------------------------------------------------

    async def create_service_account(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        name: str,
        description: str | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID,
    ) -> ServiceAccount:
        await self._set_org_context(session, organisation_id)
        sa = ServiceAccount(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            name=name,
            description=description,
            created_by_user_id=created_by_user_id,
        )
        session.add(sa)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if "uq_service_accounts_org_name" in str(exc):
                raise ServiceAccountDuplicateError(
                    f"A service account named {name!r} already exists in this organisation."
                ) from exc
            raise
        return sa

    async def get_service_account(
        self,
        session: AsyncSession,
        *,
        sa_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> ServiceAccount:
        await self._set_org_context(session, organisation_id)
        result = await session.execute(
            select(ServiceAccount).where(
                ServiceAccount.id == sa_id,
                ServiceAccount.organisation_id == organisation_id,
            )
        )
        sa = result.scalar_one_or_none()
        if sa is None:
            raise ServiceAccountNotFoundError(f"Service account {sa_id} not found.")
        return sa

    async def disable_service_account(
        self,
        session: AsyncSession,
        *,
        sa_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> ServiceAccount:
        sa = await self.get_service_account(session, sa_id=sa_id, organisation_id=organisation_id)
        now = datetime.now(tz=UTC)
        sa.is_active = False
        sa.disabled_at = now
        sa.updated_at = now
        await session.flush()
        return sa

    async def enable_service_account(
        self,
        session: AsyncSession,
        *,
        sa_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> ServiceAccount:
        sa = await self.get_service_account(session, sa_id=sa_id, organisation_id=organisation_id)
        now = datetime.now(tz=UTC)
        sa.is_active = True
        sa.disabled_at = None
        sa.updated_at = now
        await session.flush()
        return sa

    async def list_service_accounts(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ServiceAccount], int]:
        await self._set_org_context(session, organisation_id)
        base_query = select(ServiceAccount).where(ServiceAccount.organisation_id == organisation_id)
        count_result = await session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()
        items_result = await session.execute(
            base_query.order_by(ServiceAccount.name).offset((page - 1) * page_size).limit(page_size)
        )
        return list(items_result.scalars()), total

    # -------------------------------------------------------------------------
    # API keys
    # -------------------------------------------------------------------------

    async def create_api_key(
        self,
        session: AsyncSession,
        *,
        service_account_id: uuid.UUID,
        organisation_id: uuid.UUID,
        name: str,
        scopes: list[str],
        expires_in_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        """
        Create an API key.

        Returns (ApiKey, raw_key). The raw_key is returned ONCE and CANNOT be
        retrieved again. Only the secret_hash is stored.
        """
        await self._set_org_context(session, organisation_id)
        # Verify service account exists and belongs to this org
        sa = await self.get_service_account(
            session, sa_id=service_account_id, organisation_id=organisation_id
        )
        if not sa.is_active:
            raise ServiceAccountDisabledError(
                "Cannot create API keys for a disabled service account."
            )

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.now(tz=UTC) + timedelta(days=expires_in_days)

        # Retry on prefix collision (uq_api_keys_prefix).  The 8-char prefix
        # space is large (~2.8 x 10¹¹ combos) so collisions are exceedingly rare
        # but must be handled gracefully rather than leaking an IntegrityError.
        _MAX_PREFIX_RETRIES = 3
        for attempt in range(_MAX_PREFIX_RETRIES):
            raw_key, key_prefix, secret_hash = self._generate_api_key(organisation_id)
            api_key = ApiKey(
                id=uuid.uuid4(),
                service_account_id=service_account_id,
                organisation_id=organisation_id,
                workspace_id=sa.workspace_id,
                name=name,
                key_prefix=key_prefix,
                secret_hash=secret_hash,
                scopes=scopes,
                expires_at=expires_at,
            )
            session.add(api_key)
            try:
                await session.flush()
                return api_key, raw_key
            except IntegrityError as exc:
                await session.rollback()
                if "uq_api_keys_prefix" in str(exc) and attempt < _MAX_PREFIX_RETRIES - 1:
                    # Prefix collision — regenerate and retry
                    continue
                raise
        # Should never reach here; the loop always returns or raises.
        raise RuntimeError("API key prefix generation failed after retries.")

    async def get_api_key(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> ApiKey:
        await self._set_org_context(session, organisation_id)
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.id == key_id,
                ApiKey.organisation_id == organisation_id,
            )
        )
        key = result.scalar_one_or_none()
        if key is None:
            raise ApiKeyNotFoundError(f"API key {key_id} not found.")
        return key

    async def revoke_api_key(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> ApiKey:
        key = await self.get_api_key(session, key_id=key_id, organisation_id=organisation_id)
        if key.revoked_at is not None:
            raise ApiKeyRevokedError("API key is already revoked.")
        now = datetime.now(tz=UTC)
        key.revoked_at = now
        await session.flush()
        return key

    async def rotate_api_key(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> tuple[ApiKey, str]:
        """
        Rotate an API key.

        Revokes the old key and creates a new one with the same name, scopes,
        and expiry policy. Returns (new_ApiKey, new_raw_key).
        """
        old_key = await self.get_api_key(session, key_id=key_id, organisation_id=organisation_id)
        if old_key.revoked_at is not None:
            raise ApiKeyRevokedError("Cannot rotate a revoked API key.")

        # Revoke old key
        now = datetime.now(tz=UTC)
        old_key.revoked_at = now

        # Create new key
        raw_key, key_prefix, secret_hash = self._generate_api_key(organisation_id)
        new_key = ApiKey(
            id=uuid.uuid4(),
            service_account_id=old_key.service_account_id,
            organisation_id=organisation_id,
            workspace_id=old_key.workspace_id,
            name=old_key.name + " (rotated)",
            key_prefix=key_prefix,
            secret_hash=secret_hash,
            scopes=old_key.scopes,
            expires_at=old_key.expires_at,
        )
        session.add(new_key)
        await session.flush()
        return new_key, raw_key

    async def authenticate_api_key(
        self,
        session: AsyncSession,
        *,
        raw_key: str,
        required_scopes: list[str] | None = None,
    ) -> tuple[ApiKey, ServiceAccount]:
        """
        Authenticate an API key presented in a request.

        Args:
            raw_key: The full raw API key (atk_<organisation_uuid>_<prefix>_<secret>).
            required_scopes: If provided, the key's scopes list must contain ALL
                of the requested scopes.  Raises ApiKeyScopeError if any scope is
                missing.  Pass None (or empty list) to skip scope enforcement.

        Returns (ApiKey, ServiceAccount) on success.
        Raises ApiKeyInvalidError, ApiKeyRevokedError, ApiKeyExpiredError,
        ApiKeyScopeError, or ServiceAccountDisabledError on failure.
        """
        parsed = self._extract_org_and_prefix(raw_key)
        if parsed is None:
            raise ApiKeyInvalidError("Invalid API key format.")
        organisation_id, key_prefix = parsed
        await self._set_org_context(session, organisation_id)

        result = await session.execute(select(ApiKey).where(ApiKey.key_prefix == key_prefix))
        key = result.scalar_one_or_none()
        if key is None or key.organisation_id != organisation_id:
            raise ApiKeyInvalidError("Invalid API key.")

        # Constant-time verify
        if not self._verify_key(raw_key, key.secret_hash):
            raise ApiKeyInvalidError("Invalid API key.")

        now = datetime.now(tz=UTC)
        if key.revoked_at is not None:
            raise ApiKeyRevokedError("API key has been revoked.")
        if key.expires_at is not None and key.expires_at < now:
            raise ApiKeyExpiredError("API key has expired.")

        # Load service account and verify it is active
        sa_result = await session.execute(
            select(ServiceAccount).where(
                ServiceAccount.id == key.service_account_id,
                ServiceAccount.organisation_id == organisation_id,
            )
        )
        sa = sa_result.scalar_one_or_none()
        if sa is None or not sa.is_active:
            raise ServiceAccountDisabledError(
                "The service account associated with this key is disabled."
            )

        # Scope enforcement — check BEFORE updating last_used_at so that
        # an attacker probing with a valid key but wrong scopes does not get
        # activity timestamps bumped.
        if required_scopes:
            key_scopes: list[str] = key.scopes or []
            missing = [s for s in required_scopes if s not in key_scopes]
            if missing:
                raise ApiKeyScopeError(f"API key is missing required scopes: {', '.join(missing)}")

        # Update last_used_at (best-effort — don't fail if this update fails)
        key.last_used_at = now
        sa.last_used_at = now

        return key, sa

    async def list_api_keys(
        self,
        session: AsyncSession,
        *,
        service_account_id: uuid.UUID,
        organisation_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ApiKey], int]:
        await self._set_org_context(session, organisation_id)
        base_query = select(ApiKey).where(
            ApiKey.service_account_id == service_account_id,
            ApiKey.organisation_id == organisation_id,
        )
        count_result = await session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()
        items_result = await session.execute(
            base_query.order_by(ApiKey.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(items_result.scalars()), total
