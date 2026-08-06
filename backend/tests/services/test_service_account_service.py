"""
Unit tests for ServiceAccountService.

Coverage:
  1.  create_service_account() — creates a service account (no password_hash)
  2.  create_service_account() — raises ServiceAccountDuplicateError for same name+org
  3.  disable_service_account() — sets is_active=False
  4.  enable_service_account() — sets is_active=True
  5.  create_api_key() — returns (ApiKey, raw_key); secret_hash not equal to raw_key
  6.  create_api_key() — raises ServiceAccountDisabledError for disabled SA
  7.  revoke_api_key() — sets revoked_at
  8.  revoke_api_key() — raises ApiKeyRevokedError when already revoked
  9.  rotate_api_key() — revokes old key, returns new key with different prefix
  10. authenticate_api_key() — succeeds with correct key
  11. authenticate_api_key() — raises ApiKeyInvalidError with wrong key
  12. authenticate_api_key() — raises ApiKeyRevokedError after revocation
  13. authenticate_api_key() — raises ApiKeyExpiredError when expired
  14. authenticate_api_key() — raises ServiceAccountDisabledError when SA disabled
  15. raw_key is NEVER stored in ApiKey row
  16. list_api_keys() — pagination works
  17. BLAKE2b: _hash_key is deterministic for same input
  18. BLAKE2b: _hash_key uses keyed mode (different from plain hash)
  19. BLAKE2b: different input → different hash
  20. authenticate_api_key() — raises ApiKeyScopeError when required scope missing
  21. authenticate_api_key() — succeeds when all required scopes present
  22. authenticate_api_key() — no scope enforcement when required_scopes=None
  23. service account has no org_role column (cannot be owner)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.organisation import Organisation
from app.db.models.service_account import ApiKey, ServiceAccount
from app.db.models.user import User
from app.services.service_account_service import (
    ApiKeyExpiredError,
    ApiKeyInvalidError,
    ApiKeyRevokedError,
    ApiKeyScopeError,
    ServiceAccountDisabledError,
    ServiceAccountDuplicateError,
    ServiceAccountService,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc(settings: Settings) -> ServiceAccountService:
    return ServiceAccountService(settings)


@pytest_asyncio.fixture()
async def org(db: AsyncSession) -> Organisation:
    o = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="SA Test Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(o.id)},
    )
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture()
async def creator(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"creator-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Creator",
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()
    return u


@pytest_asyncio.fixture()
async def active_sa(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, creator: User
) -> ServiceAccount:
    return await svc.create_service_account(
        db,
        organisation_id=org.id,
        name=f"Bot-{uuid.uuid4().hex[:6]}",
        created_by_user_id=creator.id,
    )


# ---------------------------------------------------------------------------
# Service account tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_service_account_no_password(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, creator: User
) -> None:
    sa = await svc.create_service_account(
        db,
        organisation_id=org.id,
        name="MyBot",
        created_by_user_id=creator.id,
    )
    assert sa.id is not None
    assert sa.is_active is True
    # Service accounts have no password_hash column — verify at model level
    assert not hasattr(sa, "password_hash")


@pytest.mark.asyncio()
async def test_create_duplicate_sa_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, creator: User
) -> None:
    await svc.create_service_account(
        db, organisation_id=org.id, name="DupBot", created_by_user_id=creator.id
    )
    with pytest.raises(ServiceAccountDuplicateError):
        await svc.create_service_account(
            db, organisation_id=org.id, name="DupBot", created_by_user_id=creator.id
        )


@pytest.mark.asyncio()
async def test_disable_service_account(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    disabled = await svc.disable_service_account(db, sa_id=active_sa.id, organisation_id=org.id)
    assert disabled.is_active is False
    assert disabled.disabled_at is not None


@pytest.mark.asyncio()
async def test_enable_service_account(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    await svc.disable_service_account(db, sa_id=active_sa.id, organisation_id=org.id)
    enabled = await svc.enable_service_account(db, sa_id=active_sa.id, organisation_id=org.id)
    assert enabled.is_active is True
    assert enabled.disabled_at is None


# ---------------------------------------------------------------------------
# API key tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_api_key_returns_raw(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Key 1",
        scopes=["org:read"],
    )
    assert raw_key.startswith("atk_")
    assert len(key.key_prefix) == 8
    # secret_hash must not equal raw_key
    assert key.secret_hash != raw_key


@pytest.mark.asyncio()
async def test_raw_key_not_stored(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Key 2",
        scopes=["org:read"],
    )
    result = await db.execute(select(ApiKey).where(ApiKey.id == key.id))
    fetched = result.scalar_one()
    assert raw_key != fetched.secret_hash
    assert raw_key not in str(vars(fetched))


@pytest.mark.asyncio()
async def test_create_key_disabled_sa_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    await svc.disable_service_account(db, sa_id=active_sa.id, organisation_id=org.id)
    with pytest.raises(ServiceAccountDisabledError):
        await svc.create_api_key(
            db,
            service_account_id=active_sa.id,
            organisation_id=org.id,
            name="Key 3",
            scopes=["org:read"],
        )


@pytest.mark.asyncio()
async def test_revoke_api_key(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, _ = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Key 4",
        scopes=["org:read"],
    )
    revoked = await svc.revoke_api_key(db, key_id=key.id, organisation_id=org.id)
    assert revoked.revoked_at is not None
    assert revoked.is_active is False


@pytest.mark.asyncio()
async def test_revoke_already_revoked_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, _ = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Key 5",
        scopes=["org:read"],
    )
    await svc.revoke_api_key(db, key_id=key.id, organisation_id=org.id)
    with pytest.raises(ApiKeyRevokedError):
        await svc.revoke_api_key(db, key_id=key.id, organisation_id=org.id)


@pytest.mark.asyncio()
async def test_rotate_api_key(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    old_key, old_raw = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Key 6",
        scopes=["org:read", "workflow:read"],
    )
    new_key, new_raw = await svc.rotate_api_key(db, key_id=old_key.id, organisation_id=org.id)
    assert new_key.id != old_key.id
    assert new_key.key_prefix != old_key.key_prefix
    assert new_raw.startswith("atk_")
    # Old key revoked
    result = await db.execute(select(ApiKey).where(ApiKey.id == old_key.id))
    old_fetched = result.scalar_one()
    assert old_fetched.revoked_at is not None


@pytest.mark.asyncio()
async def test_authenticate_api_key_success(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    _, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Auth Key",
        scopes=["org:read"],
    )
    key, sa = await svc.authenticate_api_key(db, raw_key=raw_key)
    assert sa.id == active_sa.id
    assert key.is_active is True


@pytest.mark.asyncio()
async def test_authenticate_invalid_key_raises(
    svc: ServiceAccountService, db: AsyncSession
) -> None:
    with pytest.raises(ApiKeyInvalidError):
        await svc.authenticate_api_key(db, raw_key="atk_badpref_invalidsecret")


@pytest.mark.asyncio()
async def test_authenticate_wrong_format_raises(
    svc: ServiceAccountService, db: AsyncSession
) -> None:
    with pytest.raises(ApiKeyInvalidError):
        await svc.authenticate_api_key(db, raw_key="not-an-atk-key")


@pytest.mark.asyncio()
async def test_authenticate_revoked_key_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Revoke Auth Key",
        scopes=["org:read"],
    )
    await svc.revoke_api_key(db, key_id=key.id, organisation_id=org.id)
    with pytest.raises(ApiKeyRevokedError):
        await svc.authenticate_api_key(db, raw_key=raw_key)


@pytest.mark.asyncio()
async def test_authenticate_expired_key_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    key, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Expiry Auth Key",
        scopes=["org:read"],
        expires_in_days=1,
    )
    # Force expiry
    key.expires_at = datetime.now(tz=UTC) - timedelta(hours=1)
    await db.flush()

    with pytest.raises(ApiKeyExpiredError):
        await svc.authenticate_api_key(db, raw_key=raw_key)


@pytest.mark.asyncio()
async def test_authenticate_disabled_sa_raises(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    _, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Disabled SA Key",
        scopes=["org:read"],
    )
    await svc.disable_service_account(db, sa_id=active_sa.id, organisation_id=org.id)
    with pytest.raises(ServiceAccountDisabledError):
        await svc.authenticate_api_key(db, raw_key=raw_key)


@pytest.mark.asyncio()
async def test_list_api_keys_pagination(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    for i in range(5):
        await svc.create_api_key(
            db,
            service_account_id=active_sa.id,
            organisation_id=org.id,
            name=f"Key {i}",
            scopes=["org:read"],
        )
    items, total = await svc.list_api_keys(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        page=1,
        page_size=3,
    )
    assert total == 5
    assert len(items) == 3


# ---------------------------------------------------------------------------
# §5 BLAKE2b keyed-hash tests
# ---------------------------------------------------------------------------


def test_blake2b_hash_is_deterministic(svc: ServiceAccountService) -> None:
    """Same input + same pepper always produces same hash."""
    h1 = svc._hash_key("atk_prefix12_somesecretvalue")
    h2 = svc._hash_key("atk_prefix12_somesecretvalue")
    assert h1 == h2


def test_blake2b_hash_uses_keyed_mode(settings: Settings) -> None:
    """Verify the pepper is used as BLAKE2b key, not concatenated data.

    A hash computed by concatenation (wrong) differs from keyed mode (correct).
    If they are equal the implementation is broken.
    """
    import hashlib

    raw = "atk_testpfx_testsecret"
    pepper = settings.API_KEY_PEPPER

    # Keyed mode — correct
    keyed = hashlib.blake2b(raw.encode(), key=pepper.encode()[:64], digest_size=32).hexdigest()

    # Concatenation mode — incorrect (what the old code did)
    concat = hashlib.blake2b((pepper + raw).encode(), digest_size=32).hexdigest()

    # They must differ; if equal, the service is using concatenation not keying
    assert keyed != concat, (
        "BLAKE2b keyed-mode hash must differ from concatenation-mode hash. "
        "The service is using the wrong hashing approach."
    )

    # Verify the service produces the keyed variant
    svc = ServiceAccountService(settings)
    assert svc._hash_key(raw) == keyed


def test_blake2b_different_input_different_hash(svc: ServiceAccountService) -> None:
    """Different raw keys produce different hashes."""
    h1 = svc._hash_key("atk_aaa_secret1")
    h2 = svc._hash_key("atk_bbb_secret2")
    assert h1 != h2


# ---------------------------------------------------------------------------
# §8 Scope enforcement tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_authenticate_raises_scope_error_when_scope_missing(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    """API key missing a required scope raises ApiKeyScopeError."""
    _, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Scope Test Key",
        scopes=["org:read"],
    )
    with pytest.raises(ApiKeyScopeError):
        await svc.authenticate_api_key(db, raw_key=raw_key, required_scopes=["org:write"])


@pytest.mark.asyncio()
async def test_authenticate_succeeds_when_all_scopes_present(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    """API key with all required scopes passes scope check."""
    _, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="Multi Scope Key",
        scopes=["org:read", "org:write"],
    )
    key, sa = await svc.authenticate_api_key(
        db, raw_key=raw_key, required_scopes=["org:read", "org:write"]
    )
    assert sa.id == active_sa.id


@pytest.mark.asyncio()
async def test_authenticate_no_scope_enforcement_when_none(
    svc: ServiceAccountService, db: AsyncSession, org: Organisation, active_sa: ServiceAccount
) -> None:
    """required_scopes=None skips scope enforcement entirely."""
    _, raw_key = await svc.create_api_key(
        db,
        service_account_id=active_sa.id,
        organisation_id=org.id,
        name="No Scope Key",
        scopes=[],
    )
    key, sa = await svc.authenticate_api_key(db, raw_key=raw_key, required_scopes=None)
    assert sa.id == active_sa.id


# ---------------------------------------------------------------------------
# §16 Service account boundary: no org_role, cannot become owner
# ---------------------------------------------------------------------------


def test_service_account_has_no_org_role_column() -> None:
    """ServiceAccount model has no org_role attribute — cannot be promoted to owner."""
    sa = ServiceAccount()
    assert not hasattr(sa, "org_role"), (
        "ServiceAccount must not have an org_role column. "
        "Service accounts cannot hold org ownership."
    )
