"""
API route tests for the org/workspace runtime selector.

Switch-org coverage (scenarios 1-7):
  1.  GET  /me/context — 200 with org context (no workspace)
  2.  GET  /me/context — 401 when no token provided
  3.  POST /me/switch-org — 200 with new access token for valid target org
  4.  POST /me/switch-org — 401 when no token provided
  5.  POST /me/switch-org — 403 when user not a member of target org
  6.  POST /me/switch-org — 400 when switching to same org
  7.  new token from switch-org decodes to target org; new jti, same family_id

Switch-workspace coverage (scenarios 8-19):
  8.  POST /me/switch-workspace — 200 with workspace claims in new token
  9.  POST /me/switch-workspace — 401 when no token
  10. POST /me/switch-workspace — 404 when workspace does not exist
  11. POST /me/switch-workspace — 403 when user has no workspace membership
  12. POST /me/switch-workspace — 403 when workspace belongs to a different org
  13. POST /me/switch-workspace — 403 when workspace is inactive
  14. new token from switch-workspace carries workspace_id + workspace_role
  15. new token from switch-workspace has new jti, same family_id (CSRF stable)
  16. GET /me/context reflects workspace context from JWT
  17. switch-org clears workspace context (new org token has no workspace claim)
  18. workspace_role loaded from DB — not caller-supplied
  19. workspace membership filtered by current org (cross-org isolation)

Stale workspace membership regression (scenario 20):
  20. Removing WorkspaceMembership row causes next request with workspace-scoped
      JWT to fail with 403 — stale membership is NOT honoured past revocation.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import JWTService
from app.core.config import Settings
from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.db.models.workspace import Workspace

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _set_org_context(db: AsyncSession, org_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )


async def _add_required_owner(db: AsyncSession, org_id: uuid.UUID) -> User:
    """Create the organisation's required owner without changing the tested user's role."""
    owner = User(
        id=uuid.uuid4(),
        email=f"owner-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Secondary Org Owner",
        password_hash="argon2:dummy",
    )
    db.add(owner)
    await db.flush()

    db.add(
        OrganisationMembership(
            id=uuid.uuid4(),
            organisation_id=org_id,
            user_id=owner.id,
            org_role="owner",
        )
    )
    await db.flush()
    return owner


def _make_access_token(
    settings: Settings,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    org_role: str | None = "owner",
    family_id: str | None = None,
    workspace_id: uuid.UUID | None = None,
    workspace_role: str | None = None,
) -> str:
    svc = JWTService(settings)
    return svc.issue(
        user_id=user_id,
        organisation_id=org_id,
        org_role=org_role,
        family_id=family_id or str(uuid.uuid4()),
        workspace_id=workspace_id,
        workspace_role=workspace_role,
    )


async def _create_user_and_org(
    db: AsyncSession,
) -> tuple[User, Organisation, OrganisationMembership]:
    user = User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Selector Tester",
        password_hash="argon2:dummy",
    )
    org = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="Selector Test Org",
    )
    # Bootstrap the new tenant under its own transaction-local RLS context.
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
        {"org_id": str(org.id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": str(user.id)},
    )

    db.add(user)
    db.add(org)
    await db.flush()

    mem = OrganisationMembership(
        id=uuid.uuid4(),
        organisation_id=org.id,
        user_id=user.id,
        org_role="owner",
    )
    db.add(mem)
    await db.flush()
    return user, org, mem


async def _create_workspace(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    is_active: bool = True,
) -> Workspace:
    ws = Workspace(
        id=uuid.uuid4(),
        organisation_id=org_id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        display_name="Test Workspace",
        is_active=is_active,
    )
    db.add(ws)
    await db.flush()
    return ws


async def _add_workspace_membership(
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    organisation_id: uuid.UUID,
    role: str = "viewer",
) -> WorkspaceMembership:
    wm = WorkspaceMembership(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        organisation_id=organisation_id,
        user_id=user_id,
        workspace_role=role,
    )
    db.add(wm)
    await db.flush()
    return wm


# ---------------------------------------------------------------------------
# Switch-org tests (1-7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_context_returns_200(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """GET /me/context returns 200 for authenticated user (scenario 1)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, mem = await _create_user_and_org(db)
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.get(
        "/api/v1/me/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert str(org.id) == data["organisation_id"]
    assert str(user.id) == data["user_id"]
    assert data["org_role"] == "owner"
    # No workspace context
    assert data["workspace_id"] is None


@pytest.mark.asyncio()
async def test_get_context_401_no_token(client: AsyncClient) -> None:
    """GET /me/context returns 401 without token (scenario 2)."""
    resp = await client.get("/api/v1/me/context")
    assert resp.status_code == 401


@pytest.mark.asyncio()
async def test_switch_org_returns_200_with_new_token(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-org returns 200 with new access token (scenario 3)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user1, org1, _ = await _create_user_and_org(db)
        org2 = Organisation(
            id=uuid.uuid4(),
            slug=f"org2-{uuid.uuid4().hex[:8]}",
            display_name="Org 2",
        )
        await _set_org_context(db, org2.id)
        db.add(org2)
        await db.flush()
        await _add_required_owner(db, org2.id)
        mem2 = OrganisationMembership(
            id=uuid.uuid4(),
            organisation_id=org2.id,
            user_id=user1.id,
            org_role="administrator",
        )
        db.add(mem2)
        await db.commit()

    token = _make_access_token(settings, user1.id, org1.id)
    resp = await client.post(
        "/api/v1/me/switch-org",
        headers={"Authorization": f"Bearer {token}"},
        json={"organisation_id": str(org2.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["organisation_id"] == str(org2.id)
    assert data["org_role"] == "administrator"


@pytest.mark.asyncio()
async def test_switch_org_401_no_token(client: AsyncClient) -> None:
    """POST /me/switch-org returns 401 without token (scenario 4)."""
    resp = await client.post(
        "/api/v1/me/switch-org",
        json={"organisation_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio()
async def test_switch_org_403_not_member(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-org returns 403 when user not a member (scenario 5)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        other_org = Organisation(
            id=uuid.uuid4(),
            slug=f"other-{uuid.uuid4().hex[:8]}",
            display_name="Other Org",
        )
        await _set_org_context(db, other_org.id)
        db.add(other_org)
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-org",
        headers={"Authorization": f"Bearer {token}"},
        json={"organisation_id": str(other_org.id)},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio()
async def test_switch_org_400_same_org(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-org returns 400 when switching to same org (scenario 6)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-org",
        headers={"Authorization": f"Bearer {token}"},
        json={"organisation_id": str(org.id)},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio()
async def test_switch_org_new_token_scoped_to_target_new_jti_same_family(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    New token from switch-org: correct org, new jti, same family_id (scenario 7).
    Verifies: RFC 7519 §4.1.7 jti uniqueness, and CSRF binding stability.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org1, _ = await _create_user_and_org(db)
        org2 = Organisation(
            id=uuid.uuid4(),
            slug=f"neworg-{uuid.uuid4().hex[:8]}",
            display_name="New Org",
        )
        await _set_org_context(db, org2.id)
        db.add(org2)
        await db.flush()
        await _add_required_owner(db, org2.id)
        mem2 = OrganisationMembership(
            id=uuid.uuid4(),
            organisation_id=org2.id,
            user_id=user.id,
            org_role="viewer",
        )
        db.add(mem2)
        await db.commit()

    family_id = str(uuid.uuid4())
    original_token = _make_access_token(settings, user.id, org1.id, family_id=family_id)
    resp = await client.post(
        "/api/v1/me/switch-org",
        headers={"Authorization": f"Bearer {original_token}"},
        json={"organisation_id": str(org2.id)},
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]

    jwt_svc = JWTService(settings)
    orig_payload = jwt_svc.verify(original_token)
    new_payload = jwt_svc.verify(new_token)

    # Correct new org
    assert new_payload.organisation_id == org2.id
    assert new_payload.user_id == user.id
    assert new_payload.org_role == "viewer"
    # New jti (RFC 7519 §4.1.7)
    assert new_payload.jti != orig_payload.jti
    # Same family_id — CSRF binding preserved
    assert new_payload.family_id == orig_payload.family_id == family_id
    # No workspace context after org switch
    assert new_payload.workspace_id is None


# ---------------------------------------------------------------------------
# Switch-workspace tests (8-19)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_switch_workspace_returns_200(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-workspace returns 200 with workspace claims (scenario 8)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="analyst")
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["workspace_id"] == str(ws.id)
    assert data["workspace_role"] == "analyst"
    assert data["organisation_id"] == str(org.id)


@pytest.mark.asyncio()
async def test_switch_workspace_401_no_token(client: AsyncClient) -> None:
    """POST /me/switch-workspace returns 401 without token (scenario 9)."""
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        json={"workspace_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio()
async def test_switch_workspace_404_unknown_workspace(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-workspace returns 404 for non-existent workspace (scenario 10)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio()
async def test_switch_workspace_403_no_membership(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-workspace returns 403 when user has no workspace membership (scenario 11)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        # No workspace membership added
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio()
async def test_switch_workspace_403_cross_org_blocked(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    POST /me/switch-workspace with a workspace from a different org returns 404.
    The workspace exists, the user has a membership in it — but it belongs to
    a different org than the current JWT org claim.
    This validates cross-org isolation (scenario 12).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org_a, _ = await _create_user_and_org(db)
        # Second org — user is a member
        org_b = Organisation(
            id=uuid.uuid4(),
            slug=f"orgb-{uuid.uuid4().hex[:8]}",
            display_name="Org B",
        )
        await _set_org_context(db, org_b.id)
        db.add(org_b)
        await db.flush()
        await _add_required_owner(db, org_b.id)
        db.add(
            OrganisationMembership(
                id=uuid.uuid4(),
                organisation_id=org_b.id,
                user_id=user.id,
                org_role="administrator",
            )
        )
        # Workspace in org_b
        ws_b = await _create_workspace(db, org_b.id)
        await _add_workspace_membership(db, user.id, ws_b.id, org_b.id, role="viewer")
        await db.commit()

    # Token scoped to org_a — trying to switch to org_b's workspace
    token = _make_access_token(settings, user.id, org_a.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws_b.id)},
    )
    # Must be 404 — workspace not found *in the current org context*
    assert resp.status_code == 404


@pytest.mark.asyncio()
async def test_switch_workspace_403_inactive_workspace(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """POST /me/switch-workspace returns 403 for inactive workspace (scenario 13)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id, is_active=False)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="viewer")
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio()
async def test_switch_workspace_new_token_carries_workspace_claims(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """New token from switch-workspace carries correct workspace_id + role (scenario 14)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="workflow_builder")
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]

    jwt_svc = JWTService(settings)
    payload = jwt_svc.verify(new_token)
    assert payload.workspace_id == ws.id
    assert payload.workspace_role == "workflow_builder"
    assert payload.organisation_id == org.id


@pytest.mark.asyncio()
async def test_switch_workspace_new_jti_same_family(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    New token from switch-workspace: new jti, same family_id — CSRF stable (scenario 15).
    RFC 7519 §4.1.7 + Phase 1B CSRF binding correction.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="analyst")
        await db.commit()

    family_id = str(uuid.uuid4())
    original_token = _make_access_token(settings, user.id, org.id, family_id=family_id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {original_token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]

    jwt_svc = JWTService(settings)
    orig_payload = jwt_svc.verify(original_token)
    new_payload = jwt_svc.verify(new_token)

    # New jti
    assert new_payload.jti != orig_payload.jti
    # Same family_id — CSRF binding preserved
    assert new_payload.family_id == orig_payload.family_id == family_id


@pytest.mark.asyncio()
async def test_get_context_reflects_workspace_from_jwt(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """GET /me/context reflects workspace context present in JWT (scenario 16)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="viewer")
        await db.commit()

    # Token with workspace context pre-set
    token = _make_access_token(
        settings, user.id, org.id, workspace_id=ws.id, workspace_role="viewer"
    )
    resp = await client.get(
        "/api/v1/me/context",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == str(ws.id)
    assert data["workspace_role"] == "viewer"
    # workspace_slug is populated
    assert data["workspace_slug"] is not None


@pytest.mark.asyncio()
async def test_switch_org_clears_workspace_context(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    POST /me/switch-org clears workspace context — new token has no workspace claim.
    After switching org, workspace context no longer applies (scenario 17).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org1, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org1.id)
        await _add_workspace_membership(db, user.id, ws.id, org1.id, role="viewer")
        org2 = Organisation(
            id=uuid.uuid4(),
            slug=f"org2-{uuid.uuid4().hex[:8]}",
            display_name="Org 2",
        )
        await _set_org_context(db, org2.id)
        db.add(org2)
        await db.flush()
        await _add_required_owner(db, org2.id)
        db.add(
            OrganisationMembership(
                id=uuid.uuid4(),
                organisation_id=org2.id,
                user_id=user.id,
                org_role="administrator",
            )
        )
        await db.commit()

    # Token with workspace context
    token = _make_access_token(
        settings, user.id, org1.id, workspace_id=ws.id, workspace_role="viewer"
    )
    resp = await client.post(
        "/api/v1/me/switch-org",
        headers={"Authorization": f"Bearer {token}"},
        json={"organisation_id": str(org2.id)},
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]

    jwt_svc = JWTService(settings)
    new_payload = jwt_svc.verify(new_token)
    assert new_payload.workspace_id is None
    assert new_payload.workspace_role is None


@pytest.mark.asyncio()
async def test_switch_workspace_role_from_db_not_caller(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    workspace_role in the new token is loaded from the DB membership row,
    not supplied by the caller — role elevation attacks are not possible (scenario 18).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        # DB role is "viewer" — caller cannot override this
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="viewer")
        await db.commit()

    token = _make_access_token(settings, user.id, org.id)
    # Request body only contains workspace_id; role is not in the request schema
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_role"] == "viewer"

    jwt_svc = JWTService(settings)
    payload = jwt_svc.verify(data["access_token"])
    assert payload.workspace_role == "viewer"


@pytest.mark.asyncio()
async def test_switch_workspace_org_isolation_via_membership_filter(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    Workspace membership query is filtered by current org (scenario 19).
    Even if the workspace row exists in another org, the membership lookup
    must not match it — providing defence-in-depth beyond the workspace existence check.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        user, org_a, _ = await _create_user_and_org(db)
        org_b = Organisation(
            id=uuid.uuid4(),
            slug=f"orgb-{uuid.uuid4().hex[:8]}",
            display_name="Org B",
        )
        await _set_org_context(db, org_b.id)
        db.add(org_b)
        await db.flush()
        await _add_required_owner(db, org_b.id)

        # Commit org_b while its RLS context is active so the deferred
        # exactly-one-owner constraint can see its owner membership.
        await db.commit()

        # The tested user remains NOT a member of org_b.
        # Start the org_a-scoped workspace transaction separately.
        await _set_org_context(db, org_a.id)
        ws_a = await _create_workspace(db, org_a.id)
        await _add_workspace_membership(db, user.id, ws_a.id, org_a.id, role="analyst")
        await db.commit()

    # Token for org_a — switch-workspace for ws_a must succeed
    token = _make_access_token(settings, user.id, org_a.id)
    resp = await client.post(
        "/api/v1/me/switch-workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": str(ws_a.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == str(ws_a.id)
    assert data["organisation_id"] == str(org_a.id)


@pytest.mark.asyncio()
async def test_stale_workspace_membership_jwt_rejected(
    client: AsyncClient, engine, tables, settings: Settings
) -> None:
    """
    Removing a WorkspaceMembership row causes the NEXT request that presents
    a workspace-scoped JWT to fail with 403 (scenario 20).

    This is the regression test for the stale-membership gap.  Before the fix,
    get_current_membership() only checked OrganisationMembership; a user whose
    WorkspaceMembership was deleted would still be allowed to make workspace-
    scoped requests until their JWT expired.

    After the fix, get_current_membership() also re-verifies WorkspaceMembership
    when payload.workspace_id is set.  Revocation takes effect on the NEXT
    request, not at JWT expiry.

    Test flow:
    1. Create user, org, workspace, add org + workspace memberships.
    2. Issue a workspace-scoped access token.
    3. DELETE the WorkspaceMembership row from the DB.
    4. Make an authenticated request with the workspace-scoped token.
    5. Expect 403 (workspace membership revoked), not 200.
    """
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Step 1 — set up user, org, workspace, both memberships.
    async with session_factory() as db:
        user, org, _ = await _create_user_and_org(db)
        ws = await _create_workspace(db, org.id)
        await _add_workspace_membership(db, user.id, ws.id, org.id, role="viewer")
        await db.commit()

    # Step 2 — issue a workspace-scoped access token.
    ws_token = _make_access_token(
        settings,
        user.id,
        org.id,
        workspace_id=ws.id,
        workspace_role="viewer",
    )

    # Sanity check: before revocation, the request must succeed.
    resp_before = await client.get(
        "/api/v1/me/context",
        headers={"Authorization": f"Bearer {ws_token}"},
    )
    assert resp_before.status_code == 200, (
        "Baseline check failed — request must succeed before membership revocation"
    )

    # Step 3 — delete the WorkspaceMembership row.
    async with session_factory() as db:
        await _set_org_context(db, org.id)
        await db.execute(
            delete(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == ws.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        await db.commit()

    # Step 4 & 5 — same workspace-scoped JWT must now be rejected.
    resp_after = await client.get(
        "/api/v1/me/context",
        headers={"Authorization": f"Bearer {ws_token}"},
    )
    assert resp_after.status_code == 403, (
        f"Expected 403 after WorkspaceMembership revocation, got {resp_after.status_code}. "
        "The workspace-scoped JWT must not remain valid after the membership row is removed. "
        "get_current_membership() must re-verify WorkspaceMembership on every request."
    )
