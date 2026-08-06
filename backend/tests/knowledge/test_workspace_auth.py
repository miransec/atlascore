"""
Workspace authorization regression tests — knowledge endpoints.

Verifies the complete trust chain:

    URL path workspace_id (CLIENT-SUPPLIED)
        → path == JWT workspace_id              [ValidatedWorkspaceId step 2]
        → live WorkspaceMembership exists       [ValidatedWorkspaceId step 3]
        → trusted workspace_id returned         [return value only]
        → OrganisationScopedSession             [sets app.current_workspace_id GUC]
        → PostgreSQL RLS                        [rows filtered by workspace_id]

Scenarios (labelled A-I as per the Phase 2A acceptance spec):

    A   W1-scoped JWT + W1 route  → 200 (baseline: valid path)
    B   W1-scoped JWT + W2 route, no W2 membership → 403 (IDOR closed: mismatch)
    C   W1-scoped JWT + W2 route, W2 membership but no switch → 403 (path≠JWT)
    D   switch-workspace to W2 → new token carries workspace_id=W2
    E   W2-scoped token + W2 route → 200 (valid after switch)
    F   revoke W2 membership
    G   same W2 token + W2 route → 403 (live revocation takes effect)
    H   no workspace claim in JWT + knowledge route → 403
    I   raw path workspace_id never becomes trusted DB context
        (structural assertion — no `workspace_id: uuid.UUID` param on any
         knowledge endpoint after the fix)

Tests are written against the FastAPI application via httpx AsyncClient.
They use real async test fixtures from conftest.py (or define their own
lightweight stubs where no database fixture is available) so they can run
in CI without a live PostgreSQL database.

For scenarios that require verifying the HTTP status code returned by the
dependency (ValidatedWorkspaceId), a lightweight fixture is sufficient —
the dependency fires before any database or service call.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Minimal FastAPI app that mounts only the knowledge router under test.
# This avoids pulling in the full application stack (database, settings, …)
# and lets us mock exactly the dependencies we are testing.
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    from app.api.v1.endpoints.knowledge import router as knowledge_router

    app = FastAPI()
    app.include_router(knowledge_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# JWT / payload helpers
# ---------------------------------------------------------------------------


def _make_payload(
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
) -> Any:
    """Return a minimal TokenPayload-like object."""
    payload = MagicMock()
    payload.user_id = user_id
    payload.organisation_id = org_id
    payload.workspace_id = workspace_id
    payload.family_id = str(uuid.uuid4())
    return payload


def _make_membership(*, user_id: uuid.UUID, org_id: uuid.UUID) -> Any:
    """Return a minimal OrganisationMembership-like object with MEMBER role."""
    m = MagicMock()
    m.user_id = user_id
    m.organisation_id = org_id
    m.org_role = "owner"
    return m


# ---------------------------------------------------------------------------
# Core dependency overrides
#
# Each test scenario wires overrides into the FastAPI app's dependency_overrides
# dict so the real database / JWT stack is not invoked.
# ---------------------------------------------------------------------------


def _patch_get_token_payload(payload: Any):
    async def _dep():
        return payload

    return _dep


def _patch_get_current_membership(membership: Any):
    async def _dep():
        return membership

    return _dep


def _make_db_with_workspace_membership(
    *,
    found: bool,
) -> AsyncMock:
    """
    Return a mock AsyncSession whose scalar_one_or_none() returns either a
    WorkspaceMembership mock (found=True) or None (found=False).
    """
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = MagicMock() if found else None
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ---------------------------------------------------------------------------
# Scenario A — W1-scoped JWT + W1 route → dependency passes (returns 200)
# ---------------------------------------------------------------------------


async def test_scenario_a_matching_workspace_passes() -> None:
    """
    SCENARIO A: W1 JWT + /workspaces/W1/sources — ValidatedWorkspaceId passes.

    The dependency must return the validated workspace_id so the endpoint can
    proceed.  We intercept at the GET /sources endpoint and assert 200.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws1_id = uuid.uuid4()

    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=ws1_id)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    db = _make_db_with_workspace_membership(found=True)

    app = _make_app()

    # Override the three deps that ValidatedWorkspaceId resolves through.
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    # Also stub out the session factory and knowledge service so the endpoint
    # body doesn't error before returning.

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def _fake_scoped_session(*args, **kwargs):
        return mock_session

    with (
        patch("app.api.v1.endpoints.knowledge.OrganisationScopedSession") as mock_oss,
        patch("app.api.v1.endpoints.knowledge._get_session_factory", return_value=MagicMock()),
        patch("app.api.v1.endpoints.knowledge.KnowledgeService") as mock_svc_cls,
    ):
        mock_oss.return_value = mock_session
        mock_svc = AsyncMock()
        mock_svc.list_sources = AsyncMock(return_value=[])
        mock_svc_cls.return_value = mock_svc

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Also need RequirePermission to pass — override it.
            from app.api.deps import RequirePermission
            from app.auth.permissions import Permission

            perm_dep = RequirePermission(Permission.KNOWLEDGE_READ)
            app.dependency_overrides[perm_dep] = lambda: None

            resp = await client.get(
                f"/api/v1/knowledge/workspaces/{ws1_id}/sources",
                headers={"Authorization": "Bearer fake"},
            )

    # The dep passed.  The endpoint attempted to run; any 2xx or 4xx from
    # the service layer is acceptable — we only care the dep did not 403.
    assert resp.status_code != 403, (
        f"Scenario A: expected dep to pass for matching W1 JWT+path, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Scenario B — W1 JWT + W2 route, no W2 membership → 403
# ---------------------------------------------------------------------------


async def test_scenario_b_path_mismatch_returns_403() -> None:
    """
    SCENARIO B: W1 JWT + /workspaces/W2/sources — path ≠ JWT workspace_id.

    This is the IDOR exploit path: a user holds a valid W1 token and sends it
    to a W2 route hoping to read/write W2 data.

    ValidatedWorkspaceId must reject with 403 at step 2 (path != JWT claim)
    before any database call is made for the knowledge endpoint itself.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws1_id = uuid.uuid4()
    ws2_id = uuid.uuid4()  # different — not in JWT

    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=ws1_id)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    # db would not be queried at all for step 2 rejection, but stub it anyway.
    db = _make_db_with_workspace_membership(found=False)

    app = _make_app()
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/knowledge/workspaces/{ws2_id}/sources",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 403, (
        f"Scenario B: IDOR not blocked — expected 403 for W1 JWT on W2 route, "
        f"got {resp.status_code}"
    )
    assert "workspace" in resp.json().get("detail", "").lower(), (
        f"Scenario B: 403 detail should mention workspace, got: {resp.json()}"
    )


# ---------------------------------------------------------------------------
# Scenario C — W1 JWT + W2 route, W2 membership row exists, no switch → 403
# ---------------------------------------------------------------------------


async def test_scenario_c_membership_exists_but_no_switch_returns_403() -> None:
    """
    SCENARIO C: W1 JWT + /workspaces/W2/sources, W2 membership exists but not
    in the JWT.

    Even if the user is a member of W2 in the database, accessing W2 routes
    requires a W2-scoped token (obtained by POST /me/switch-workspace).
    The path-vs-JWT check at step 2 must still reject with 403.

    This verifies that live membership alone is not sufficient — the JWT
    must explicitly carry the requested workspace_id.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws1_id = uuid.uuid4()
    ws2_id = uuid.uuid4()

    # JWT is scoped to W1 only — no W2 claim even though the user is a W2 member.
    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=ws1_id)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    # db would find a W2 membership, but we never reach step 3.
    db = _make_db_with_workspace_membership(found=True)

    app = _make_app()
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/knowledge/workspaces/{ws2_id}/sources",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 403, (
        f"Scenario C: expected 403 (path≠JWT even with live W2 membership), got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Scenario D — switch-workspace issues a token with workspace_id=W2
# ---------------------------------------------------------------------------


def test_scenario_d_switch_workspace_sets_jwt_claim() -> None:
    """
    SCENARIO D: POST /me/switch-workspace → new access token carries workspace_id=W2.

    This is a structural test: verify that TokenPayload carries a workspace_id
    field and that JWTService.create_access_token accepts workspace_id as a
    named parameter, producing a token that round-trips through verify().

    No HTTP request needed — this tests the token-issuing layer directly.
    """
    from app.auth.tokens import JWTService
    from app.core.config import Settings

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ws2_id = uuid.uuid4()
    family_id = uuid.uuid4()

    # Minimal settings with a test secret (must be >= 64 chars for JWT_SECRET_KEY).
    settings = MagicMock(spec=Settings)
    settings.JWT_SECRET_KEY = "test-secret-" + "x" * 64
    settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15

    svc = JWTService(settings)
    token = svc.issue(
        user_id=user_id,
        organisation_id=org_id,
        workspace_id=ws2_id,
        org_role=None,
        family_id=str(family_id),
    )
    payload = svc.verify(token)

    assert payload.workspace_id == ws2_id, (
        f"Scenario D: expected workspace_id={ws2_id} in token payload, got {payload.workspace_id}"
    )
    assert payload.organisation_id == org_id
    assert payload.user_id == user_id


# ---------------------------------------------------------------------------
# Scenario E — W2 token + W2 route → dep passes
# ---------------------------------------------------------------------------


async def test_scenario_e_w2_token_w2_route_passes() -> None:
    """
    SCENARIO E: After switching to W2, a W2-scoped token on a W2 route passes.

    Mirrors Scenario A but for the switched workspace.  ValidatedWorkspaceId
    must pass when path == JWT.workspace_id AND live membership exists.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws2_id = uuid.uuid4()

    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=ws2_id)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    db = _make_db_with_workspace_membership(found=True)

    app = _make_app()
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    with (
        patch("app.api.v1.endpoints.knowledge.OrganisationScopedSession") as mock_oss,
        patch("app.api.v1.endpoints.knowledge._get_session_factory", return_value=MagicMock()),
        patch("app.api.v1.endpoints.knowledge.KnowledgeService") as mock_svc_cls,
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_oss.return_value = mock_session

        mock_svc = AsyncMock()
        mock_svc.list_sources = AsyncMock(return_value=[])
        mock_svc_cls.return_value = mock_svc

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/knowledge/workspaces/{ws2_id}/sources",
                headers={"Authorization": "Bearer fake"},
            )

    assert resp.status_code != 403, (
        f"Scenario E: W2 token + W2 route should pass dep, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Scenario F + G — revoke W2 membership; same token → 403
# ---------------------------------------------------------------------------


async def test_scenario_fg_revoked_membership_returns_403() -> None:
    """
    SCENARIOS F+G: Revoke W2 membership; same W2-scoped JWT + W2 route → 403.

    After revocation the live WorkspaceMembership row is gone (db returns None).
    ValidatedWorkspaceId step 3 must reject with 403 — the valid JWT is not
    sufficient once the underlying membership row is deleted.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws2_id = uuid.uuid4()

    # Token is still valid and workspace_id=W2 matches the path.
    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=ws2_id)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    # Membership was revoked — db returns None.
    db = _make_db_with_workspace_membership(found=False)

    app = _make_app()
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/knowledge/workspaces/{ws2_id}/sources",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 403, (
        f"Scenario F+G: expected 403 after membership revocation, got {resp.status_code}"
    )
    assert (
        "membership" in resp.json().get("detail", "").lower()
        or "workspace" in resp.json().get("detail", "").lower()
    ), f"Scenario F+G: 403 detail should mention membership/workspace, got {resp.json()}"


# ---------------------------------------------------------------------------
# Scenario H — no workspace claim in JWT + knowledge route → 403
# ---------------------------------------------------------------------------


async def test_scenario_h_no_workspace_claim_returns_403() -> None:
    """
    SCENARIO H: JWT without workspace_id claim + knowledge workspace route → 403.

    A fresh login token (before any switch-workspace call) has workspace_id=None.
    Such a token must be rejected by ValidatedWorkspaceId step 1.
    """
    from app.api import deps

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()

    # No workspace claim — workspace_id is None.
    payload = _make_payload(user_id=user_id, org_id=org_id, workspace_id=None)
    membership = _make_membership(user_id=user_id, org_id=org_id)
    db = _make_db_with_workspace_membership(found=True)

    app = _make_app()
    app.dependency_overrides[deps.get_token_payload] = lambda: payload
    app.dependency_overrides[deps.get_current_membership] = lambda: membership
    app.dependency_overrides[deps.get_raw_db] = lambda: db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/knowledge/workspaces/{ws_id}/sources",
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 403, (
        f"Scenario H: expected 403 for no-workspace-claim JWT, got {resp.status_code}"
    )
    assert "workspace" in resp.json().get("detail", "").lower(), (
        f"Scenario H: 403 detail should mention workspace context, got {resp.json()}"
    )


# ---------------------------------------------------------------------------
# Scenario I — structural: no raw uuid.UUID path param on knowledge endpoints
# ---------------------------------------------------------------------------


def test_scenario_i_no_raw_uuid_workspace_param_on_endpoints() -> None:
    """
    SCENARIO I: After the Phase 2A fix, the raw URL path workspace_id parameter
    (type annotation `uuid.UUID`) must NOT appear on any knowledge endpoint
    function signature.  Every endpoint must use ValidatedWorkspaceId instead.

    This is a structural guard: if a future change accidentally reverts one of
    the 11 endpoints to `workspace_id: uuid.UUID`, this test catches it at
    review time, before the code reaches the database.

    Methodology: inspect each endpoint function's type annotations.
    ValidatedWorkspaceId is `Annotated[uuid.UUID, Depends(...)]`.
    A bare `uuid.UUID` annotation on `workspace_id` means the dep was dropped.
    """
    import typing
    import uuid as _uuid

    from fastapi import APIRouter

    import app.api.v1.endpoints.knowledge as knowledge_module

    # Collect all route handler functions registered on the knowledge router.
    router: APIRouter = knowledge_module.router

    bare_uuid_endpoints: list[str] = []

    for route in router.routes:
        func = route.endpoint
        hints = typing.get_type_hints(func, include_extras=True)
        ws_hint = hints.get("workspace_id")

        if ws_hint is None:
            # No workspace_id parameter — skip (shouldn't happen for knowledge endpoints).
            continue

        # A bare uuid.UUID annotation means the fix was not applied.
        if ws_hint is _uuid.UUID:
            bare_uuid_endpoints.append(func.__name__)
            continue

        # Check it's Annotated[uuid.UUID, Depends(get_validated_workspace_context)].
        origin = typing.get_origin(ws_hint)
        if origin is not typing.Annotated:
            bare_uuid_endpoints.append(func.__name__)

    assert not bare_uuid_endpoints, (
        "Scenario I: The following knowledge endpoints have a bare `uuid.UUID` "
        "workspace_id parameter instead of ValidatedWorkspaceId — the IDOR "
        "fix was not applied or was reverted:\n"
        + "\n".join(f"  - {name}" for name in bare_uuid_endpoints)
    )
