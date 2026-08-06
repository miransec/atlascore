"""
Tests for RefreshTokenService — token rotation and reuse detection.

Coverage:
- create() returns a raw token; only BLAKE2b hash is stored
- find_active_by_raw_token() finds the active token
- find_active_by_raw_token() returns None for an unknown raw token
- rotate() returns new raw token + RefreshToken
- rotate() deactivates the previous token
- rotate() raises RefreshTokenReuseError on stale (already-rotated) token replay
- rotate() revokes the entire family on replay
- revoke_family() marks all family members inactive
- revoke_all_for_user_org() revokes tokens from all families for that user+org
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.refresh import RefreshTokenReuseError, RefreshTokenService
from app.core.config import Settings
from app.db.models.auth import RefreshToken
from app.db.models.organisation import Organisation
from app.db.models.user import User


@pytest.fixture()
def svc(settings: Settings) -> RefreshTokenService:
    return RefreshTokenService(settings)


@pytest_asyncio.fixture()
async def identity(raw_db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    uid = uuid.uuid4()
    oid = uuid.uuid4()
    raw_db.add(
        User(
            id=uid,
            email=f"refresh-{uid.hex[:8]}@test.example",
            full_name="Refresh Test",
            password_hash="hash",
            pepper_version=1,
        )
    )
    await raw_db.flush()
    await raw_db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(oid)},
    )
    raw_db.add(
        Organisation(
            id=oid,
            slug=f"refresh-{oid.hex[:8]}",
            display_name="Refresh Test Org",
        )
    )
    await raw_db.flush()
    return uid, oid


@pytest.mark.asyncio()
async def test_create_returns_raw_token(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw, rt = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    assert isinstance(raw, str)
    assert len(raw) > 20
    # Hash must differ from raw
    assert rt.token_hash != raw


@pytest.mark.asyncio()
async def test_raw_token_not_stored_in_db(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw, rt = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    result = await raw_db.execute(select(RefreshToken).where(RefreshToken.id == rt.id))
    row = result.scalar_one()
    assert row.token_hash != raw
    # BLAKE2b 32-byte = 64 hex chars
    assert len(row.token_hash) == 64


@pytest.mark.asyncio()
async def test_find_active_by_raw_token(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw, _ = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    found = await svc.find_active_by_raw_token(raw_db, raw_token=raw)
    assert found is not None
    assert found.user_id == uid


@pytest.mark.asyncio()
async def test_find_active_unknown_returns_none(
    svc: RefreshTokenService, raw_db: AsyncSession
) -> None:
    result = await svc.find_active_by_raw_token(raw_db, raw_token="totally-fake-token")
    assert result is None


@pytest.mark.asyncio()
async def test_rotate_issues_new_token(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw1, rt1 = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    result = await svc.rotate(raw_db, raw_token=raw1, client_ip="127.0.0.1")
    assert result is not None
    raw2, rt2 = result
    assert raw2 != raw1
    assert rt2.family_id == rt1.family_id  # same family
    assert rt2.is_active is True


@pytest.mark.asyncio()
async def test_rotate_deactivates_old_token(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw1, _ = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    await svc.rotate(raw_db, raw_token=raw1, client_ip="127.0.0.1")
    # Old token must no longer be findable as active.
    old = await svc.find_active_by_raw_token(raw_db, raw_token=raw1)
    assert old is None


@pytest.mark.asyncio()
async def test_rotate_replay_raises_and_revokes_family(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw1, rt1 = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    raw2, rt2 = await svc.rotate(raw_db, raw_token=raw1, client_ip="127.0.0.1")

    # Replay the already-rotated raw1 — must raise.
    with pytest.raises(RefreshTokenReuseError) as exc_info:
        await svc.rotate(raw_db, raw_token=raw1, client_ip="127.0.0.1")

    assert exc_info.value.family_id == rt1.family_id

    # The active second token should also be revoked (family kill).
    killed = await svc.find_active_by_raw_token(raw_db, raw_token=raw2)
    assert killed is None


@pytest.mark.asyncio()
async def test_revoke_family(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    raw1, rt1 = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    raw2, rt2 = await svc.rotate(raw_db, raw_token=raw1, client_ip="127.0.0.1")
    await svc.revoke_family(raw_db, family_id=rt2.family_id, organisation_id=oid)
    found = await svc.find_active_by_raw_token(raw_db, raw_token=raw2)
    assert found is None


@pytest.mark.asyncio()
async def test_revoke_all_for_user_org(
    svc: RefreshTokenService, raw_db: AsyncSession, identity: tuple[uuid.UUID, uuid.UUID]
) -> None:
    uid, oid = identity
    # Two independent families for the same user+org.
    raw_a, _ = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")
    raw_b, _ = await svc.create(raw_db, user_id=uid, organisation_id=oid, client_ip="127.0.0.1")

    await svc.revoke_all_for_user_org(raw_db, user_id=uid, organisation_id=oid)

    assert await svc.find_active_by_raw_token(raw_db, raw_token=raw_a) is None
    assert await svc.find_active_by_raw_token(raw_db, raw_token=raw_b) is None
