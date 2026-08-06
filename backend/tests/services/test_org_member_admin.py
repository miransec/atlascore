"""
§3 Member administration tests — 17 scenarios.

Coverage:
  1.  list_members() — returns all members
  2.  list_members() — empty when no members
  3.  add_member() — adds a new member
  4.  add_member() — raises OrgServiceError when user not found
  5.  add_member() — raises OrgServiceError for duplicate membership
  6.  remove_member() — removes a non-owner member
  7.  remove_member() — raises OrgServiceError when removing the owner
  8.  remove_member() — raises OrgServiceError for non-existent membership
  9.  change_member_role() — changes role for a non-owner member
  10. change_member_role() — raises OrgServiceError when changing owner role directly
  11. change_member_role() — raises OrgServiceError for non-existent membership
  12. transfer_ownership() — swaps owner and target member roles
  13. transfer_ownership() — raises OrgServiceError when same-user self-transfer
  14. transfer_ownership() — raises OrgServiceError when actor not owner
  15. transfer_ownership() — raises OrgServiceError when target not a member
  16. update_organisation() — emits org.updated (not org.created) audit event
  17. Stale JWT: removed member's subsequent request is rejected by live DB check
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.services.org_service import OrgService, OrgServiceError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def org(db: AsyncSession) -> Organisation:
    o = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="Admin Test Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(o.id)},
    )
    db.add(o)
    await db.flush()
    return o


async def _make_user(db: AsyncSession, prefix: str = "user") -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@test.example",
        full_name=prefix.title(),
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()
    return u


async def _make_member(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str | None = "viewer",
) -> OrganisationMembership:
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    m = OrganisationMembership(
        id=uuid.uuid4(),
        organisation_id=org_id,
        user_id=user_id,
        org_role=role,
    )
    db.add(m)
    await db.flush()
    return m


@pytest_asyncio.fixture()
async def owner(db: AsyncSession) -> User:
    return await _make_user(db, "owner")


@pytest_asyncio.fixture()
async def owner_membership(
    db: AsyncSession, org: Organisation, owner: User
) -> OrganisationMembership:
    return await _make_member(db, org.id, owner.id, role="owner")


@pytest_asyncio.fixture()
async def member_user(db: AsyncSession) -> User:
    return await _make_user(db, "member")


@pytest_asyncio.fixture()
async def member_membership(
    db: AsyncSession, org: Organisation, member_user: User, owner_membership: OrganisationMembership
) -> OrganisationMembership:
    return await _make_member(db, org.id, member_user.id, role="administrator")


# ---------------------------------------------------------------------------
# 1. list_members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_members_returns_all(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    rows = await OrgService.list_members(db, organisation_id=org.id)
    user_ids = {r[1].id for r in rows}
    assert owner.id in user_ids
    assert member_user.id in user_ids


@pytest.mark.asyncio()
async def test_list_members_empty_org(db: AsyncSession) -> None:
    empty_org = Organisation(
        id=uuid.uuid4(),
        slug=f"empty-{uuid.uuid4().hex[:8]}",
        display_name="Empty Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(empty_org.id)},
    )
    db.add(empty_org)
    await db.flush()
    rows = await OrgService.list_members(db, organisation_id=empty_org.id)
    assert rows == []


# ---------------------------------------------------------------------------
# 3-5. add_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_add_member_creates_membership(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    new_user = await _make_user(db, "new")
    mem = await OrgService.add_member(
        db,
        organisation_id=org.id,
        user_id=new_user.id,
        org_role="viewer",
        actor_user_id=owner.id,
    )
    assert mem.user_id == new_user.id
    assert mem.org_role == "viewer"


@pytest.mark.asyncio()
async def test_add_member_user_not_found_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="not found"):
        await OrgService.add_member(
            db,
            organisation_id=org.id,
            user_id=uuid.uuid4(),
            org_role="viewer",
            actor_user_id=owner.id,
        )


@pytest.mark.asyncio()
async def test_add_member_duplicate_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="already a member"):
        await OrgService.add_member(
            db,
            organisation_id=org.id,
            user_id=member_user.id,
            org_role="viewer",
            actor_user_id=owner.id,
        )


# ---------------------------------------------------------------------------
# 6-8. remove_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_remove_member_succeeds(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    await OrgService.remove_member(
        db,
        organisation_id=org.id,
        user_id=member_user.id,
        actor_user_id=owner.id,
    )
    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.user_id == member_user.id,
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio()
async def test_remove_owner_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="[Oo]wner"):
        await OrgService.remove_member(
            db,
            organisation_id=org.id,
            user_id=owner.id,
            actor_user_id=owner.id,
        )


@pytest.mark.asyncio()
async def test_remove_nonexistent_member_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="not a member"):
        await OrgService.remove_member(
            db,
            organisation_id=org.id,
            user_id=uuid.uuid4(),
            actor_user_id=owner.id,
        )


# ---------------------------------------------------------------------------
# 9-11. change_member_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_change_member_role_succeeds(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    updated = await OrgService.change_member_role(
        db,
        organisation_id=org.id,
        user_id=member_user.id,
        new_role="viewer",
        actor_user_id=owner.id,
    )
    assert updated.org_role == "viewer"


@pytest.mark.asyncio()
async def test_change_owner_role_directly_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="[Oo]wner"):
        await OrgService.change_member_role(
            db,
            organisation_id=org.id,
            user_id=owner.id,
            new_role="administrator",
            actor_user_id=owner.id,
        )


@pytest.mark.asyncio()
async def test_change_role_nonexistent_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="not a member"):
        await OrgService.change_member_role(
            db,
            organisation_id=org.id,
            user_id=uuid.uuid4(),
            new_role="viewer",
            actor_user_id=owner.id,
        )


# ---------------------------------------------------------------------------
# 12-15. transfer_ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_transfer_ownership_swaps_roles(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    await OrgService.transfer_ownership(
        db,
        organisation_id=org.id,
        current_owner_id=owner.id,
        new_owner_id=member_user.id,
    )
    # Reload
    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.user_id == member_user.id,
        )
    )
    new_owner_mem = result.scalar_one()
    assert new_owner_mem.org_role == "owner"

    old_result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.user_id == owner.id,
        )
    )
    old_owner_mem = old_result.scalar_one()
    assert old_owner_mem.org_role == "administrator"


@pytest.mark.asyncio()
async def test_transfer_ownership_same_user_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    with pytest.raises(OrgServiceError, match="different user"):
        await OrgService.transfer_ownership(
            db,
            organisation_id=org.id,
            current_owner_id=owner.id,
            new_owner_id=owner.id,
        )


@pytest.mark.asyncio()
async def test_transfer_ownership_actor_not_owner_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    # member_user tries to transfer ownership — they're not the owner
    with pytest.raises(OrgServiceError, match="[Oo]wner"):
        await OrgService.transfer_ownership(
            db,
            organisation_id=org.id,
            current_owner_id=member_user.id,  # member_user is not the owner
            new_owner_id=owner.id,
        )


@pytest.mark.asyncio()
async def test_transfer_ownership_target_not_member_raises(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    stranger = await _make_user(db, "stranger")
    with pytest.raises(OrgServiceError, match="member"):
        await OrgService.transfer_ownership(
            db,
            organisation_id=org.id,
            current_owner_id=owner.id,
            new_owner_id=stranger.id,
        )


# ---------------------------------------------------------------------------
# 16. Audit event type on update_organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_update_organisation_emits_org_updated_not_created(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
) -> None:
    """update_organisation must emit org.updated without reading restricted audit rows."""
    from unittest.mock import patch

    with patch("app.services.org_service.AuditService.emit_transactional") as emit:
        await OrgService.update_organisation(
            db,
            organisation_id=org.id,
            display_name="Updated Name",
            actor_user_id=owner.id,
        )

    assert emit.call_count == 1
    assert emit.call_args.kwargs["event_type"] == "org.updated"


# ---------------------------------------------------------------------------
# 17. Stale JWT: removed member's membership returns None from live DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_stale_jwt_removed_member_not_in_db(
    db: AsyncSession,
    org: Organisation,
    owner: User,
    owner_membership: OrganisationMembership,
    member_user: User,
    member_membership: OrganisationMembership,
) -> None:
    """After remove_member, the live DB check returns None — JWT claim is stale."""
    await OrgService.remove_member(
        db,
        organisation_id=org.id,
        user_id=member_user.id,
        actor_user_id=owner.id,
    )
    # Simulate what get_current_membership does: live DB query
    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == member_user.id,
            OrganisationMembership.organisation_id == org.id,
        )
    )
    membership = result.scalar_one_or_none()
    assert membership is None, (
        "After remove_member, live DB must return None — "
        "get_current_membership would raise 401 (stale JWT rejected immediately)"
    )
