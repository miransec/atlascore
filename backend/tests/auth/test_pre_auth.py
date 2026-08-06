"""
Tests for PreAuthSessionService.

Coverage:
- create() returns a raw token and stores only the hash in DB
- consume() returns the session and marks it consumed
- consume() returns None for a non-existent token
- consume() raises PreAuthSessionReuseError on second consumption attempt
- consume() returns None for an expired session
- raw token is never stored in DB (only SHA-256 hash)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.pre_auth import PreAuthSessionReuseError, PreAuthSessionService
from app.core.config import Settings
from app.db.models.auth import PreAuthSession
from app.db.models.user import User


@pytest.fixture()
def svc(settings: Settings) -> PreAuthSessionService:
    return PreAuthSessionService(settings)


@pytest_asyncio.fixture()
async def user_id(raw_db: AsyncSession) -> uuid.UUID:
    uid = uuid.uuid4()
    raw_db.add(
        User(
            id=uid,
            email=f"preauth-{uid.hex[:8]}@test.example",
            full_name="PreAuth Test",
            password_hash="hash",
            pepper_version=1,
        )
    )
    await raw_db.flush()
    return uid


@pytest.mark.asyncio()
async def test_create_returns_raw_token(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    raw = await svc.create(raw_db, user_id=user_id, client_ip="127.0.0.1", user_agent="pytest")
    assert isinstance(raw, str)
    assert len(raw) > 20


@pytest.mark.asyncio()
async def test_raw_token_not_in_db(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    raw = await svc.create(raw_db, user_id=user_id, client_ip="127.0.0.1", user_agent="pytest")
    result = await raw_db.execute(select(PreAuthSession).where(PreAuthSession.user_id == user_id))
    row = result.scalar_one()
    # The DB must store the hash, NOT the raw token.
    assert row.token_hash != raw
    assert len(row.token_hash) == 64  # SHA-256 hex = 64 chars


@pytest.mark.asyncio()
async def test_consume_success(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    raw = await svc.create(raw_db, user_id=user_id, client_ip="127.0.0.1", user_agent="pytest")
    session = await svc.consume(raw_db, raw_token=raw)
    assert session is not None
    assert session.user_id == user_id
    assert session.consumed_at is not None


@pytest.mark.asyncio()
async def test_consume_wrong_token_returns_none(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
) -> None:
    result = await svc.consume(raw_db, raw_token="does-not-exist-token")
    assert result is None


@pytest.mark.asyncio()
async def test_consume_replay_raises(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    raw = await svc.create(raw_db, user_id=user_id, client_ip="127.0.0.1", user_agent="pytest")
    await svc.consume(raw_db, raw_token=raw)
    with pytest.raises(PreAuthSessionReuseError):
        await svc.consume(raw_db, raw_token=raw)


@pytest.mark.asyncio()
async def test_consume_expired_returns_none(
    svc: PreAuthSessionService,
    raw_db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    raw = await svc.create(raw_db, user_id=user_id, client_ip="127.0.0.1", user_agent="pytest")
    # Back-date the session's expires_at so it looks expired.
    await raw_db.execute(
        update(PreAuthSession)
        .where(PreAuthSession.user_id == user_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await raw_db.flush()
    result = await svc.consume(raw_db, raw_token=raw)
    assert result is None
