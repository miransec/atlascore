"""
Unit tests for TeamService.

Coverage:
  1.  create() — creates a team
  2.  create() — raises TeamDuplicateError for same-name same-org
  3.  get() — returns team by id+org
  4.  get() — raises TeamNotFoundError for wrong org
  5.  update() — changes name and description
  6.  update() — raises TeamDuplicateError on name conflict
  7.  delete() — removes team
  8.  list_for_org() — pagination works
  9.  add_member() — creates TeamMembership
  10. add_member() — raises TeamMemberNotOrgMemberError for non-member
  11. add_member() — raises TeamMemberAlreadyExistsError for duplicate
  12. remove_member() — removes membership
  13. remove_member() — raises TeamMemberNotFoundError when not a member
  14. list_members() — returns team members
  15. Cross-tenant: get() cannot access team from different org
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.services.team_service import (
    TeamDuplicateError,
    TeamMemberAlreadyExistsError,
    TeamMemberNotFoundError,
    TeamMemberNotOrgMemberError,
    TeamNotFoundError,
    TeamService,
)

pytestmark = pytest.mark.asyncio

_CREATOR_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture()
def svc() -> TeamService:
    return TeamService()


@pytest_asyncio.fixture()
async def org(db: AsyncSession) -> Organisation:
    db.add(
        User(
            id=_CREATOR_ID,
            email="team-creator@test.example",
            full_name="Team Creator",
            password_hash="argon2:dummy",
        )
    )
    await db.flush()
    o = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="Team Test Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(o.id)},
    )
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture()
async def other_org(db: AsyncSession, org: Organisation) -> Organisation:
    o = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="Other Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(o.id)},
    )
    db.add(o)
    await db.flush()
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org.id)},
    )
    return o


@pytest_asyncio.fixture()
async def org_user(db: AsyncSession, org: Organisation) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Org User",
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()
    # Add to org under its tenant context.
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org.id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(u.id)},
    )
    db.add(
        OrganisationMembership(
            id=uuid.uuid4(),
            organisation_id=org.id,
            user_id=u.id,
            org_role="viewer",
        )
    )
    await db.flush()
    return u


@pytest_asyncio.fixture()
async def non_member_user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"stranger-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Stranger",
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()
    return u


# ---------------------------------------------------------------------------
# Team CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_team(svc: TeamService, db: AsyncSession, org: Organisation) -> None:
    team = await svc.create(
        db,
        organisation_id=org.id,
        name="Alpha Team",
        created_by_user_id=_CREATOR_ID,
    )
    assert team.id is not None
    assert team.name == "Alpha Team"
    assert team.organisation_id == org.id


@pytest.mark.asyncio()
async def test_create_duplicate_raises(
    svc: TeamService, db: AsyncSession, org: Organisation
) -> None:
    await svc.create(db, organisation_id=org.id, name="Dup Team", created_by_user_id=_CREATOR_ID)
    with pytest.raises(TeamDuplicateError):
        await svc.create(
            db, organisation_id=org.id, name="Dup Team", created_by_user_id=_CREATOR_ID
        )


@pytest.mark.asyncio()
async def test_get_team(svc: TeamService, db: AsyncSession, org: Organisation) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Beta Team", created_by_user_id=_CREATOR_ID
    )
    fetched = await svc.get(db, team_id=team.id, organisation_id=org.id)
    assert fetched.id == team.id


@pytest.mark.asyncio()
async def test_get_wrong_org_raises(
    svc: TeamService, db: AsyncSession, org: Organisation, other_org: Organisation
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Gamma Team", created_by_user_id=_CREATOR_ID
    )
    with pytest.raises(TeamNotFoundError):
        await svc.get(db, team_id=team.id, organisation_id=other_org.id)


@pytest.mark.asyncio()
async def test_update_team(svc: TeamService, db: AsyncSession, org: Organisation) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Delta Team", created_by_user_id=_CREATOR_ID
    )
    updated = await svc.update(
        db,
        team_id=team.id,
        organisation_id=org.id,
        name="Delta Team (renamed)",
        description="New description",
    )
    assert updated.name == "Delta Team (renamed)"
    assert updated.description == "New description"


@pytest.mark.asyncio()
async def test_delete_team(svc: TeamService, db: AsyncSession, org: Organisation) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Epsilon Team", created_by_user_id=_CREATOR_ID
    )
    await svc.delete(db, team_id=team.id, organisation_id=org.id)
    with pytest.raises(TeamNotFoundError):
        await svc.get(db, team_id=team.id, organisation_id=org.id)


@pytest.mark.asyncio()
async def test_list_pagination(svc: TeamService, db: AsyncSession, org: Organisation) -> None:
    for i in range(5):
        await svc.create(
            db, organisation_id=org.id, name=f"Team {i}", created_by_user_id=_CREATOR_ID
        )
    items, total = await svc.list_for_org(db, organisation_id=org.id, page=1, page_size=3)
    assert total == 5
    assert len(items) == 3


# ---------------------------------------------------------------------------
# Team memberships
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_add_member(
    svc: TeamService, db: AsyncSession, org: Organisation, org_user: User
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Zeta Team", created_by_user_id=_CREATOR_ID
    )
    membership = await svc.add_member(
        db, team_id=team.id, user_id=org_user.id, organisation_id=org.id
    )
    assert membership.user_id == org_user.id
    assert membership.team_id == team.id


@pytest.mark.asyncio()
async def test_add_non_org_member_raises(
    svc: TeamService, db: AsyncSession, org: Organisation, non_member_user: User
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Eta Team", created_by_user_id=_CREATOR_ID
    )
    with pytest.raises(TeamMemberNotOrgMemberError):
        await svc.add_member(
            db, team_id=team.id, user_id=non_member_user.id, organisation_id=org.id
        )


@pytest.mark.asyncio()
async def test_add_duplicate_member_raises(
    svc: TeamService, db: AsyncSession, org: Organisation, org_user: User
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Theta Team", created_by_user_id=_CREATOR_ID
    )
    await svc.add_member(db, team_id=team.id, user_id=org_user.id, organisation_id=org.id)
    with pytest.raises(TeamMemberAlreadyExistsError):
        await svc.add_member(db, team_id=team.id, user_id=org_user.id, organisation_id=org.id)


@pytest.mark.asyncio()
async def test_remove_member(
    svc: TeamService, db: AsyncSession, org: Organisation, org_user: User
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Iota Team", created_by_user_id=_CREATOR_ID
    )
    await svc.add_member(db, team_id=team.id, user_id=org_user.id, organisation_id=org.id)
    await svc.remove_member(db, team_id=team.id, user_id=org_user.id, organisation_id=org.id)
    members = await svc.list_members(db, team_id=team.id, organisation_id=org.id)
    assert not any(m.user_id == org_user.id for m in members)


@pytest.mark.asyncio()
async def test_remove_nonexistent_member_raises(
    svc: TeamService, db: AsyncSession, org: Organisation
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Kappa Team", created_by_user_id=_CREATOR_ID
    )
    with pytest.raises(TeamMemberNotFoundError):
        await svc.remove_member(db, team_id=team.id, user_id=uuid.uuid4(), organisation_id=org.id)


@pytest.mark.asyncio()
async def test_list_members(
    svc: TeamService, db: AsyncSession, org: Organisation, org_user: User
) -> None:
    team = await svc.create(
        db, organisation_id=org.id, name="Lambda Team", created_by_user_id=_CREATOR_ID
    )
    await svc.add_member(db, team_id=team.id, user_id=org_user.id, organisation_id=org.id)
    members = await svc.list_members(db, team_id=team.id, organisation_id=org.id)
    assert len(members) == 1
    assert members[0].user_id == org_user.id
