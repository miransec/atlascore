"""
Tests for the exactly-one-owner DEFERRABLE constraint.

The CONSTRAINT TRIGGER trg_exactly_one_owner fires at COMMIT time.

Scenarios:
  1. A newly registered org starts with exactly one owner
  2. Removing the owner without adding another triggers constraint at commit
  3. Transferring ownership in a single transaction (delete old + insert new) succeeds
  4. Adding a second owner in the same transaction triggers constraint at commit
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


async def _set_org_context(conn, org_id: uuid.UUID) -> None:
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )


async def _insert_user(engine: AsyncEngine, uid: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                "VALUES (:id, :email, 'Test', 'hash', 1) ON CONFLICT DO NOTHING"
            ),
            {"id": uid, "email": f"{uid.hex[:8]}@test.example"},
        )


async def _insert_org_with_owner(
    engine: AsyncEngine, org_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    slug = f"org-{org_id.hex[:8]}"
    async with engine.begin() as conn:
        # Bootstrap this tenant under its own transaction-local FORCE-RLS context.
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text("INSERT INTO organisations (id, slug, display_name) VALUES (:id, :slug, :name)"),
            {"id": org_id, "slug": slug, "name": slug},
        )
        await conn.execute(
            text(
                "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
                "VALUES (:id, :uid, :oid, 'owner')"
            ),
            {"id": uuid.uuid4(), "uid": user_id, "oid": org_id},
        )


@pytest.mark.asyncio()
async def test_new_org_has_exactly_one_owner(engine: AsyncEngine, tables: None) -> None:
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _insert_user(engine, user_id)
    await _insert_org_with_owner(engine, org_id, user_id)

    async with engine.connect() as conn:
        await _set_org_context(conn, org_id)
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM organisation_memberships "
                "WHERE organisation_id = :oid AND org_role = 'owner'"
            ),
            {"oid": org_id},
        )
        count = result.scalar()

    assert count == 1


@pytest.mark.asyncio()
async def test_removing_only_owner_fails_at_commit(engine: AsyncEngine, tables: None) -> None:
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _insert_user(engine, user_id)
    await _insert_org_with_owner(engine, org_id, user_id)

    # Attempt to delete the owner row — constraint fires at COMMIT.
    with pytest.raises(
        Exception, match="(?i)organisation .*must have exactly one owner|trg_exactly_one_owner"
    ):
        async with engine.begin() as conn:
            await _set_org_context(conn, org_id)
            await conn.execute(
                text(
                    "DELETE FROM organisation_memberships "
                    "WHERE organisation_id = :oid AND org_role = 'owner'"
                ),
                {"oid": org_id},
            )
            # Trigger fires at COMMIT (DEFERRABLE INITIALLY DEFERRED)


@pytest.mark.asyncio()
async def test_ownership_transfer_in_single_transaction_succeeds(
    engine: AsyncEngine, tables: None
) -> None:
    org_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    for uid in (user_a, user_b):
        await _insert_user(engine, uid)
    await _insert_org_with_owner(engine, org_id, user_a)

    # Transfer: add user_b as owner, demote user_a — both in the same txn.
    async with engine.begin() as conn:
        await _set_org_context(conn, org_id)
        await conn.execute(
            text(
                "UPDATE organisation_memberships "
                "SET org_role = 'administrator' "
                "WHERE organisation_id = :oid AND user_id = :uid"
            ),
            {"oid": org_id, "uid": user_a},
        )
        await conn.execute(
            text(
                "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
                "VALUES (:id, :uid, :oid, 'owner')"
            ),
            {"id": uuid.uuid4(), "uid": user_b, "oid": org_id},
        )
        # COMMIT — trigger checks: exactly one owner → passes

    # Verify
    async with engine.connect() as conn:
        await _set_org_context(conn, org_id)
        result = await conn.execute(
            text(
                "SELECT user_id FROM organisation_memberships "
                "WHERE organisation_id = :oid AND org_role = 'owner'"
            ),
            {"oid": org_id},
        )
        owners = [r[0] for r in result.fetchall()]

    assert user_b in owners
    assert user_a not in owners
    assert len(owners) == 1


@pytest.mark.asyncio()
async def test_adding_second_owner_fails_at_commit(engine: AsyncEngine, tables: None) -> None:
    org_id = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    for uid in (user_a, user_b):
        await _insert_user(engine, uid)
    await _insert_org_with_owner(engine, org_id, user_a)

    with pytest.raises(
        Exception,
        match="(?i)organisation .*must have exactly one owner|trg_exactly_one_owner|uq_org_memberships_one_owner",
    ):
        async with engine.begin() as conn:
            await _set_org_context(conn, org_id)
            # Insert a SECOND owner without removing the first.
            await conn.execute(
                text(
                    "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
                    "VALUES (:id, :uid, :oid, 'owner')"
                ),
                {"id": uuid.uuid4(), "uid": user_b, "oid": org_id},
            )
            # COMMIT — trigger fires → two owners → violation
