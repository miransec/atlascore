"""Tests for WorkspaceService create → creator administrator membership."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.services.workspace_service import WorkspaceService, WorkspaceServiceError

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture()
async def org_owner(db: AsyncSession) -> tuple[Organisation, User]:
    owner = User(
        id=uuid.uuid4(),
        email=f"ws-owner-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Workspace Owner",
        password_hash="argon2:dummy",
    )
    db.add(owner)
    await db.flush()

    org = Organisation(
        id=uuid.uuid4(),
        slug=f"ws-org-{uuid.uuid4().hex[:8]}",
        display_name="Workspace Test Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org.id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(owner.id)},
    )
    db.add(org)
    await db.flush()
    db.add(
        OrganisationMembership(
            id=uuid.uuid4(),
            organisation_id=org.id,
            user_id=owner.id,
            org_role="owner",
        )
    )
    await db.flush()
    return org, owner


async def test_create_workspace_adds_creator_as_administrator(
    db: AsyncSession, org_owner: tuple[Organisation, User]
) -> None:
    org, owner = org_owner
    ws = await WorkspaceService.create_workspace(
        db,
        organisation_id=org.id,
        slug=f"eng-{uuid.uuid4().hex[:6]}",
        display_name="Engineering",
        description="Primary eng workspace",
        actor_user_id=owner.id,
    )
    assert isinstance(ws, Workspace)

    result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == ws.id,
            WorkspaceMembership.user_id == owner.id,
        )
    )
    membership = result.scalar_one()
    assert membership.organisation_id == org.id
    assert membership.workspace_role == "administrator"


async def test_create_workspace_duplicate_slug_raises(
    db: AsyncSession, org_owner: tuple[Organisation, User]
) -> None:
    org, owner = org_owner
    slug = f"dup-{uuid.uuid4().hex[:6]}"
    await WorkspaceService.create_workspace(
        db,
        organisation_id=org.id,
        slug=slug,
        display_name="First",
        actor_user_id=owner.id,
    )
    with pytest.raises(WorkspaceServiceError, match="already taken"):
        await WorkspaceService.create_workspace(
            db,
            organisation_id=org.id,
            slug=slug,
            display_name="Second",
            actor_user_id=owner.id,
        )


async def test_list_members_returns_creator(
    db: AsyncSession, org_owner: tuple[Organisation, User]
) -> None:
    org, owner = org_owner
    ws = await WorkspaceService.create_workspace(
        db,
        organisation_id=org.id,
        slug=f"list-{uuid.uuid4().hex[:6]}",
        display_name="List Me",
        actor_user_id=owner.id,
    )
    members = await WorkspaceService.list_members(
        db, workspace_id=ws.id, organisation_id=org.id
    )
    assert len(members) == 1
    assert members[0].user_id == owner.id
    assert members[0].workspace_role == "administrator"


async def test_list_members_wrong_org_raises(
    db: AsyncSession, org_owner: tuple[Organisation, User]
) -> None:
    org, owner = org_owner
    ws = await WorkspaceService.create_workspace(
        db,
        organisation_id=org.id,
        slug=f"iso-{uuid.uuid4().hex[:6]}",
        display_name="Isolated",
        actor_user_id=owner.id,
    )
    other_org_id = uuid.uuid4()
    with pytest.raises(WorkspaceServiceError, match="not found"):
        await WorkspaceService.list_members(
            db, workspace_id=ws.id, organisation_id=other_org_id
        )
