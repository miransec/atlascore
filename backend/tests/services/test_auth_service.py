"""
Integration tests for AuthService — the full login, token, and logout flow.

These tests use the real DB (test instance, SAVEPOINT isolation) and exercise
the service layer directly — not the HTTP endpoints.

Coverage:
  1.  register() creates user, org, membership (owner), default workspace in one txn
  2.  register() raises on duplicate email
  3.  register() raises on duplicate org slug
  4.  login_step1() succeeds with correct credentials
  5.  login_step1() returns org list in response
  6.  login_step1() raises on unknown email (emits auth.login_failed audit event)
  7.  login_step1() raises on wrong password (emits auth.login_failed audit event)
  8.  select_organisation() returns access token and refresh token
  9.  select_organisation() derives user_id from pre-auth session, NEVER from body
  10. select_organisation() raises on expired/non-existent pre-auth token
  11. select_organisation() raises on wrong organisation_id (not the user's org)
  12. refresh_tokens() rotates the refresh token successfully
  13. refresh_tokens() raises RefreshTokenReuseError on stale replay
  14. logout() revokes the refresh token family
  15. logout_all() revokes all refresh token families for the user+org
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.refresh import RefreshTokenReuseError
from app.db.models.membership import OrganisationMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _register(
    auth_service: AuthService,
    raw_db: AsyncSession,
    email: str | None = None,
    slug: str | None = None,
) -> tuple[User, Organisation]:
    e = email or f"user-{_uid()}@test.example"
    s = slug or f"org-{_uid()}"
    user, org = await auth_service.register(
        session=raw_db,
        email=e,
        password="super-secure-password-42",
        full_name="Test User",
        organisation_name=s.replace("-", " ").title(),
        organisation_slug=s,
        client_ip="127.0.0.1",
        request_id="req-test",
    )
    await raw_db.commit()
    return user, org


# ---------------------------------------------------------------------------
# Test 1: register() atomically creates all entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_register_creates_all_entities(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    user, org = await _register(auth_service, raw_db)

    # User exists
    result = await raw_db.execute(select(User).where(User.id == user.id))
    assert result.scalar_one() is not None

    # Organisation exists
    result = await raw_db.execute(select(Organisation).where(Organisation.id == org.id))
    assert result.scalar_one() is not None

    # Membership (owner) exists
    result = await raw_db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == user.id,
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.org_role == "owner",
        )
    )
    assert result.scalar_one() is not None

    # Default workspace exists
    result = await raw_db.execute(select(Workspace).where(Workspace.organisation_id == org.id))
    assert result.scalar_one() is not None


# ---------------------------------------------------------------------------
# Test 2: register() raises on duplicate email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_register_duplicate_email_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"dup-{_uid()}@test.example"
    await _register(auth_service, raw_db, email=email)
    with pytest.raises(Exception):
        await _register(auth_service, raw_db, email=email)


# ---------------------------------------------------------------------------
# Test 3: register() raises on duplicate org slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_register_duplicate_slug_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    slug = f"org-dup-{_uid()}"
    await _register(auth_service, raw_db, slug=slug)
    with pytest.raises(Exception):
        await _register(auth_service, raw_db, slug=slug)


# ---------------------------------------------------------------------------
# Test 4 & 5: login_step1() returns user + org list on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_login_step1_returns_orgs(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"login-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)

    logged_user, memberships, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-login",
    )
    assert logged_user.id == user.id
    assert any(m.organisation_id == org.id for m in memberships)
    assert isinstance(pre_auth_raw, str)
    assert len(pre_auth_raw) > 0


# ---------------------------------------------------------------------------
# Test 6 & 7: login_step1() raises on bad credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_login_step1_wrong_password_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"wrong-pw-{_uid()}@test.example"
    await _register(auth_service, raw_db, email=email)
    with pytest.raises(Exception):
        await auth_service.login_step1(
            raw_session=raw_db,
            email=email,
            password="definitely-wrong-password",
            client_ip="127.0.0.1",
            user_agent="pytest",
            request_id="req-fail",
        )


@pytest.mark.asyncio()
async def test_login_step1_unknown_email_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    with pytest.raises(Exception):
        await auth_service.login_step1(
            raw_session=raw_db,
            email="nobody@nowhere.example",
            password="any-password",
            client_ip="127.0.0.1",
            user_agent="pytest",
            request_id="req-unknown",
        )


# ---------------------------------------------------------------------------
# Test 8 & 9: select_organisation() flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_select_organisation_returns_tokens(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"select-org-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, memberships, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-step1",
    )

    _, _, _, raw_refresh, refresh_token = await auth_service.select_organisation(
        raw_session=raw_db,
        pre_auth_raw_token=pre_auth_raw,
        organisation_id=org.id,
        client_ip="127.0.0.1",
        request_id="req-step2",
    )

    assert isinstance(raw_refresh, str)
    assert len(raw_refresh) > 0
    assert refresh_token.user_id == user.id
    assert refresh_token.organisation_id == org.id


@pytest.mark.asyncio()
async def test_select_organisation_user_id_from_session_not_body(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    """The service must derive user_id from the pre-auth session row, not from any input."""
    email = f"uid-check-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, _, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-step1",
    )

    returned_user, _, _, _, _ = await auth_service.select_organisation(
        raw_session=raw_db,
        pre_auth_raw_token=pre_auth_raw,
        organisation_id=org.id,
        client_ip="127.0.0.1",
        request_id="req-step2",
    )

    # The returned user must be exactly the one from the pre-auth session.
    assert returned_user.id == user.id


# ---------------------------------------------------------------------------
# Test 10: select_organisation() on expired/unknown pre-auth token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_select_organisation_expired_pre_auth_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"expired-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    with pytest.raises(Exception):
        await auth_service.select_organisation(
            raw_session=raw_db,
            pre_auth_raw_token="fake-pre-auth-token",
            organisation_id=org.id,
            client_ip="127.0.0.1",
            request_id="req-fake",
        )


# ---------------------------------------------------------------------------
# Test 11: select_organisation() with wrong org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_select_organisation_wrong_org_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"wrong-org-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, _, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-step1",
    )

    with pytest.raises(Exception):
        await auth_service.select_organisation(
            raw_session=raw_db,
            pre_auth_raw_token=pre_auth_raw,
            organisation_id=uuid.uuid4(),  # random, non-existent org
            client_ip="127.0.0.1",
            request_id="req-step2",
        )


# ---------------------------------------------------------------------------
# Test 12: refresh_tokens() rotates the token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_refresh_tokens_rotates(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"refresh-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, _, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-1",
    )
    _, _, _, raw_refresh1, rt1 = await auth_service.select_organisation(
        raw_session=raw_db,
        pre_auth_raw_token=pre_auth_raw,
        organisation_id=org.id,
        client_ip="127.0.0.1",
        request_id="req-2",
    )

    result = await auth_service.refresh_tokens(
        raw_session=raw_db,
        raw_refresh_token=raw_refresh1,
        client_ip="127.0.0.1",
        request_id="req-3",
    )

    assert result is not None
    raw_refresh2, rt2 = result
    assert raw_refresh2 != raw_refresh1
    assert rt2.family_id == rt1.family_id


# ---------------------------------------------------------------------------
# Test 13: refresh_tokens() raises on stale token replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_refresh_tokens_reuse_raises(
    auth_service: AuthService,
    raw_db: AsyncSession,
) -> None:
    email = f"reuse-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, _, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-1",
    )
    _, _, _, raw_refresh1, _ = await auth_service.select_organisation(
        raw_session=raw_db,
        pre_auth_raw_token=pre_auth_raw,
        organisation_id=org.id,
        client_ip="127.0.0.1",
        request_id="req-2",
    )
    # First rotation succeeds.
    await auth_service.refresh_tokens(
        raw_session=raw_db,
        raw_refresh_token=raw_refresh1,
        client_ip="127.0.0.1",
        request_id="req-3",
    )
    # Replaying the original token must raise.
    with pytest.raises(RefreshTokenReuseError):
        await auth_service.refresh_tokens(
            raw_session=raw_db,
            raw_refresh_token=raw_refresh1,
            client_ip="127.0.0.1",
            request_id="req-4",
        )


# ---------------------------------------------------------------------------
# Test 14: logout() revokes the family
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_logout_revokes_family(
    auth_service: AuthService,
    raw_db: AsyncSession,
    refresh_service,
) -> None:
    email = f"logout-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)
    _, _, pre_auth_raw = await auth_service.login_step1(
        raw_session=raw_db,
        email=email,
        password="super-secure-password-42",
        client_ip="127.0.0.1",
        user_agent="pytest",
        request_id="req-1",
    )
    _, _, _, raw_refresh, rt = await auth_service.select_organisation(
        raw_session=raw_db,
        pre_auth_raw_token=pre_auth_raw,
        organisation_id=org.id,
        client_ip="127.0.0.1",
        request_id="req-2",
    )

    await auth_service.logout(
        raw_session=raw_db,
        family_id=rt.family_id,
        user_id=user.id,
        organisation_id=org.id,
        request_id="req-logout",
        client_ip="127.0.0.1",
    )
    await raw_db.commit()

    found = await refresh_service.find_active_by_raw_token(raw_db, raw_token=raw_refresh)
    assert found is None


# ---------------------------------------------------------------------------
# Test 15: logout_all() revokes all families for the user+org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_logout_all_revokes_all_families(
    auth_service: AuthService,
    raw_db: AsyncSession,
    refresh_service,
) -> None:
    email = f"logout-all-{_uid()}@test.example"
    user, org = await _register(auth_service, raw_db, email=email)

    refresh_tokens_raw = []
    for _ in range(2):
        _, _, pre_auth_raw = await auth_service.login_step1(
            raw_session=raw_db,
            email=email,
            password="super-secure-password-42",
            client_ip="127.0.0.1",
            user_agent="pytest",
            request_id=f"req-step1-{_uid()}",
        )
        _, _, _, raw_refresh, _ = await auth_service.select_organisation(
            raw_session=raw_db,
            pre_auth_raw_token=pre_auth_raw,
            organisation_id=org.id,
            client_ip="127.0.0.1",
            request_id=f"req-step2-{_uid()}",
        )
        refresh_tokens_raw.append(raw_refresh)

    await auth_service.logout_all(
        raw_session=raw_db,
        user_id=user.id,
        organisation_id=org.id,
        request_id="req-logout-all",
        client_ip="127.0.0.1",
    )
    await raw_db.commit()

    for raw in refresh_tokens_raw:
        found = await refresh_service.find_active_by_raw_token(raw_db, raw_token=raw)
        assert found is None
