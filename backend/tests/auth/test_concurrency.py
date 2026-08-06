"""
Concurrency and stale-membership tests.

These tests verify behaviour under conditions that require careful
synchronisation or live-database re-verification:

  C1. Ownership transfer concurrency — two concurrent transactions each
      try to claim ownership of the same organisation; exactly one must
      win and the loser must roll back to the original state.

  C2. Org deletion does not false-fail the ownership trigger — deleting
      an organisation cascades to its membership rows; the deferred
      ownership trigger must NOT raise because there are 0 owners for a
      deleted org.

  C3. Refresh token rotation concurrency — two concurrent rotation
      attempts for the same token: only one may succeed; the loser
      detects reuse and the entire token family is revoked (SELECT FOR
      UPDATE on the token row prevents double-rotation).

  C4. Stale CSRF after token rotation — after rotating a refresh token
      the jti changes; an X-CSRF-Token header derived from the OLD jti
      must be rejected on the next state-changing request.

  C5. Stale-membership JWT invalidation — removing a user's membership
      from an organisation causes the next authenticated request (with
      an otherwise-valid, non-expired JWT) to return 401 immediately,
      without waiting for the token to expire.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.auth.refresh import RefreshTokenReuseError, RefreshTokenService

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_user(engine: AsyncEngine, uid: uuid.UUID, email: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                "VALUES (:id, :email, 'Test', 'hash', 1) ON CONFLICT DO NOTHING"
            ),
            {"id": uid, "email": email},
        )


async def _insert_org_with_owner(
    engine: AsyncEngine, org_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    slug = f"conc-{org_id.hex[:8]}"
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO organisations (id, slug, display_name) "
                "VALUES (:id, :slug, :name) ON CONFLICT DO NOTHING"
            ),
            {"id": org_id, "slug": slug, "name": slug},
        )
        await conn.execute(
            text(
                "INSERT INTO organisation_memberships "
                "(id, user_id, organisation_id, org_role) "
                "VALUES (:id, :uid, :oid, 'owner') ON CONFLICT DO NOTHING"
            ),
            {"id": uuid.uuid4(), "uid": user_id, "oid": org_id},
        )


# ---------------------------------------------------------------------------
# C1 — Ownership transfer concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_concurrent_ownership_transfer_only_one_wins(
    engine: AsyncEngine, tables: None
) -> None:
    """
    Two concurrent transactions each attempt to claim ownership of the same
    organisation by:
      1. Demoting the current owner to 'viewer'
      2. Inserting themselves as the new 'owner'

    Expected: exactly one succeeds.  The other fails with either:
      - a unique constraint violation (unique partial index on owner rows), or
      - the deferred exactly-one-owner trigger exception at commit.

    After both tasks settle, exactly one owner must remain in the DB.
    """
    org_id = uuid.uuid4()
    alice = uuid.uuid4()
    bob = uuid.uuid4()
    carol = uuid.uuid4()

    for uid, email in [
        (alice, f"alice-{alice.hex[:6]}@conc.test"),
        (bob, f"bob-{bob.hex[:6]}@conc.test"),
        (carol, f"carol-{carol.hex[:6]}@conc.test"),
    ]:
        await _insert_user(engine, uid, email)

    # Org starts with Alice as owner
    await _insert_org_with_owner(engine, org_id, alice)

    async def _claim_ownership(new_uid: uuid.UUID) -> None:
        """Attempt to claim ownership in a single DB transaction."""
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                {"oid": str(org_id)},
            )
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(new_uid)},
            )
            # Step 1: demote the original owner only.  A concurrent winner
            # must not be demoted by the losing transaction.
            await conn.execute(
                text(
                    "UPDATE organisation_memberships "
                    "SET org_role = 'viewer' "
                    "WHERE organisation_id = :oid AND user_id = :alice "
                    "AND org_role = 'owner'"
                ),
                {"oid": org_id, "alice": alice},
            )
            # Step 2: claim ownership
            await conn.execute(
                text(
                    "INSERT INTO organisation_memberships "
                    "(id, user_id, organisation_id, org_role) "
                    "VALUES (:id, :uid, :oid, 'owner')"
                ),
                {"id": uuid.uuid4(), "uid": new_uid, "oid": org_id},
            )
            # COMMIT — trigger deferred until here; unique index enforces at INSERT

    results = await asyncio.gather(
        _claim_ownership(bob),
        _claim_ownership(carol),
        return_exceptions=True,
    )

    # Exactly one must have succeeded (no exception), one must have failed.
    successes = [r for r in results if r is None]
    failures = [r for r in results if r is not None]

    assert len(successes) == 1, (
        f"Expected exactly 1 successful ownership transfer, got {len(successes)}. "
        f"Results: {results!r}"
    )
    assert len(failures) == 1, (
        f"Expected exactly 1 failure, got {len(failures)}. Results: {results!r}"
    )

    # Verify DB state: exactly one owner
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        result = await conn.execute(
            text(
                "SELECT user_id FROM organisation_memberships "
                "WHERE organisation_id = :oid AND org_role = 'owner'"
            ),
            {"oid": org_id},
        )
        owners = [row[0] for row in result.fetchall()]

    assert len(owners) == 1, (
        f"DB must have exactly one owner after concurrent transfer; found {len(owners)}: {owners!r}"
    )
    assert owners[0] in (bob, carol), f"The surviving owner must be bob or carol, got {owners[0]!r}"


# ---------------------------------------------------------------------------
# C2 — Org deletion does not false-fail the ownership trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_org_deletion_does_not_false_fail_ownership_trigger(
    engine: AsyncEngine, tables: None
) -> None:
    """
    Deleting an organisation CASCADE-deletes its membership rows.
    The DEFERRABLE exactly-one-owner trigger fires at COMMIT for each
    deleted membership.  It must NOT raise an exception when the
    organisation itself is also being deleted in the same transaction —
    there is no org left to own, so the constraint is vacuously satisfied.

    Without the fix (checking org existence), the trigger fires after all
    CASCADE deletes have completed, sees 0 owners for the deleted org,
    and raises 'Organisation must have exactly one owner (found 0)' —
    making it impossible to delete any organisation.
    """
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _insert_user(engine, user_id, f"del-{user_id.hex[:6]}@conc.test")
    await _insert_org_with_owner(engine, org_id, user_id)

    # Deleting the org must succeed (CASCADE removes memberships, then org row).
    # If the trigger has the bug, this raises:
    #   "Organisation must have exactly one owner (found 0)"
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text("DELETE FROM organisations WHERE id = :id"),
            {"id": org_id},
        )
        # COMMIT — trigger fires at this point; must not raise

    # Verify the org is gone
    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        result = await conn.execute(
            text("SELECT id FROM organisations WHERE id = :id"),
            {"id": org_id},
        )
        assert result.fetchone() is None, "Organisation row must be deleted"

        # Membership rows must also be gone (CASCADE)
        result2 = await conn.execute(
            text("SELECT id FROM organisation_memberships WHERE organisation_id = :id"),
            {"id": org_id},
        )
        assert result2.fetchone() is None, "Membership rows must be cascade-deleted"


# ---------------------------------------------------------------------------
# C3 — Refresh token rotation concurrency (SELECT FOR UPDATE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_concurrent_refresh_rotation_only_one_succeeds(
    engine: AsyncEngine, tables: None, settings: Any
) -> None:
    """
    Two concurrent coroutines both try to rotate the SAME refresh token.

    The rotate() method uses SELECT FOR UPDATE to acquire a row-level lock.
    In asyncio (single event loop, cooperative scheduling):
      - Task A: acquires lock, marks token inactive, creates new token, commits
      - Task B: unblocked after Task A commits, finds token inactive
                → raises RefreshTokenReuseError → revokes entire family
                (including the new token issued by Task A)

    After both tasks settle:
      - Task A returns a new (raw_token, RefreshToken)
      - Task B raises RefreshTokenReuseError
      - The entire token family has zero active tokens (family revoked by B)
    """
    refresh_svc = RefreshTokenService(settings)

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    org_slug = f"conc-rt-{org_id.hex[:6]}"

    # Create minimal DB objects needed for the refresh token FK
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                "VALUES (:id, :email, 'RT User', 'hash', 1) ON CONFLICT DO NOTHING"
            ),
            {"id": user_id, "email": f"rt-{user_id.hex[:6]}@conc.test"},
        )
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO organisations (id, slug, display_name) "
                "VALUES (:id, :slug, :name) ON CONFLICT DO NOTHING"
            ),
            {"id": org_id, "slug": org_slug, "name": org_slug},
        )

    # Issue initial refresh token
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        raw_token, initial_rt = await refresh_svc.create(
            session,
            user_id=user_id,
            organisation_id=org_id,
        )
        await session.commit()
        family_id = initial_rt.family_id

    async def _rotate(client_ip: str) -> Any:
        """Try to rotate the initial token; return result or exception."""
        async with session_factory() as session:
            try:
                result = await refresh_svc.rotate(session, raw_token=raw_token, client_ip=client_ip)
                await session.commit()
                return result
            except RefreshTokenReuseError:
                # Reuse detection deliberately mutates the family.  Persist the
                # revocation before surfacing the security exception.
                await session.commit()
                raise
            except Exception:
                await session.rollback()
                raise

    results = await asyncio.gather(
        _rotate("10.0.0.1"),
        _rotate("10.0.0.2"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    reuse_errors = [r for r in results if isinstance(r, RefreshTokenReuseError)]
    other_errors = [
        r for r in results if isinstance(r, Exception) and not isinstance(r, RefreshTokenReuseError)
    ]

    assert not other_errors, f"Unexpected errors: {other_errors!r}"

    # Exactly one must succeed and one must detect reuse
    assert len(successes) == 1, (
        f"Expected 1 successful rotation, got {len(successes)}. Results: {results!r}"
    )
    assert len(reuse_errors) == 1, (
        f"Expected 1 RefreshTokenReuseError (reuse detected), got {len(reuse_errors)}. "
        f"Results: {results!r}"
    )

    # The reuse detection must have revoked the whole family
    # (including the new token issued by the successful rotation).
    async with session_factory() as session:
        from app.db.models.auth import RefreshToken

        await session.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        result = await session.execute(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id,
                RefreshToken.is_active.is_(True),
            )
        )
        active_tokens = result.scalars().all()

    assert active_tokens == [], (
        f"After reuse detection, the entire family must be revoked. "
        f"Found {len(active_tokens)} still-active token(s)."
    )


# ---------------------------------------------------------------------------
# C4 — Stale CSRF header rejected after token rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_csrf_stable_within_family_after_rotation(
    engine: AsyncEngine, tables: None, settings: Any
) -> None:
    """
    Phase 1B CSRF semantics: the CSRF token is HMAC-SHA256(CSRF_SECRET, family_id).
    After rotating a refresh token within the same family (normal /auth/refresh),
    family_id is preserved, so the CSRF token is STABLE — the frontend does not
    need to re-read the cookie after every token refresh.

    A cross-family event (re-login, creating a new session) produces a new
    family_id, which invalidates the old CSRF token — stolen pre-logout CSRF
    cookies from a different session cannot be replayed into a new session.

    Prior to Phase 1B, the CSRF token was bound to the refresh token's jti
    (which changed on every rotation).  The new design binds to family_id
    (stable across rotations, changes on new login).
    """
    from app.auth.csrf import CSRFService

    csrf_svc = CSRFService(settings)
    refresh_svc = RefreshTokenService(settings)

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    org_slug = f"conc-csrf-{org_id.hex[:6]}"

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
                "VALUES (:id, :email, 'CSRF User', 'hash', 1) ON CONFLICT DO NOTHING"
            ),
            {"id": user_id, "email": f"csrf-{user_id.hex[:6]}@conc.test"},
        )
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text(
                "INSERT INTO organisations (id, slug, display_name) "
                "VALUES (:id, :slug, :name) ON CONFLICT DO NOTHING"
            ),
            {"id": org_id, "slug": org_slug, "name": org_slug},
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Step 1: issue initial refresh token (Session A)
    async with session_factory() as session:
        raw_token, initial_rt = await refresh_svc.create(
            session, user_id=user_id, organisation_id=org_id
        )
        await session.commit()

    family_id_a = str(initial_rt.family_id)
    csrf_session_a = csrf_svc.generate_token(family_id_a)

    # Step 2: rotate — new refresh token, SAME family
    async with session_factory() as session:
        new_raw, new_rt = await refresh_svc.rotate(  # type: ignore[misc]
            session, raw_token=raw_token
        )
        await session.commit()

    # jti changes on rotation (refresh token has its own unique jti)
    assert new_rt.jti != initial_rt.jti, "Refresh token rotation must produce a new jti"
    # family_id is preserved
    assert str(new_rt.family_id) == family_id_a, "family_id must be stable across rotation"

    # CSRF for the same family is identical — frontend cookie stays valid
    csrf_after_rotation = csrf_svc.generate_token(str(new_rt.family_id))
    assert csrf_after_rotation == csrf_session_a, (
        "CSRF token must be stable across refresh token rotation within same family"
    )

    from unittest.mock import MagicMock

    verify_request = MagicMock()
    verify_request.headers = {"X-CSRF-Token": csrf_session_a}
    assert csrf_svc.verify(verify_request, family_id_a) is True, (
        "Original CSRF token must still verify after rotation (same family_id)"
    )

    # Step 3: new login session (different family) — CSRF must change
    async with session_factory() as session:
        _raw_b, rt_b = await refresh_svc.create(session, user_id=user_id, organisation_id=org_id)
        await session.commit()

    family_id_b = str(rt_b.family_id)
    assert family_id_b != family_id_a, "A new login session must produce a new family_id"

    csrf_session_b = csrf_svc.generate_token(family_id_b)
    assert csrf_session_b != csrf_session_a, "CSRF tokens for different login sessions must differ"

    # Old CSRF from session A must NOT verify against session B's family_id
    stale_request = MagicMock()
    stale_request.headers = {"X-CSRF-Token": csrf_session_a}
    assert csrf_svc.verify(stale_request, family_id_b) is False, (
        "CSRF token from session A must be rejected when verifying session B"
    )


# ---------------------------------------------------------------------------
# C5 — Stale-membership JWT invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_stale_membership_jwt_rejected_immediately(
    client: Any, engine: AsyncEngine, tables: None
) -> None:
    """
    After removing a user's organisation membership, the next request that
    carries a valid (non-expired) JWT for that user+org must be rejected
    immediately — without waiting for the JWT to expire.

    This is enforced by get_current_membership() in deps.py, which re-queries
    the live database on every authenticated request.

    Expected HTTP status: 401 (membership not found / revoked).
    The JWT is structurally valid and not expired; the rejection is solely
    because the live membership record no longer exists.

    Test uses HTTP endpoints throughout so that all data is committed (not
    inside a SAVEPOINT) and visible across connections.
    """
    uid_suffix = uuid.uuid4().hex[:8]
    email = f"stale-{uid_suffix}@test.example"
    password = "Correct-Horse-Battery-Staple-88"
    org_slug = f"stale-org-{uid_suffix}"

    # Step 1: register via HTTP — committed to DB by the handler
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "Stale Member",
            "organisation_name": f"Stale Org {uid_suffix}",
            "organisation_slug": org_slug,
        },
    )
    assert reg_resp.status_code == 201, (
        f"Registration must succeed. Got {reg_resp.status_code}: {reg_resp.text}"
    )

    # Step 2: login step 1 via HTTP — pre-auth cookie set automatically
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200, (
        f"Login step 1 must succeed. Got {login_resp.status_code}: {login_resp.text}"
    )
    orgs = login_resp.json()["organisations"]
    assert orgs, "Login step 1 must return at least one organisation"
    org_id = uuid.UUID(orgs[0]["id"])

    # Step 3: select organisation — access token in response body
    select_resp = await client.post(
        "/api/v1/auth/select-organisation",
        json={"organisation_id": str(org_id)},
    )
    assert select_resp.status_code == 200, (
        f"Select-org must succeed. Got {select_resp.status_code}: {select_resp.text}"
    )
    access_token = select_resp.json()["access_token"]

    # Step 4: verify the token works (membership intact)
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_resp.status_code == 200, (
        f"GET /auth/me must succeed before membership is removed. "
        f"Got {me_resp.status_code}: {me_resp.text}"
    )
    user_id = uuid.UUID(me_resp.json()["user_id"])

    # Step 5: remove this membership while preserving the database invariant
    # that every live organisation has exactly one owner.  Transfer ownership
    # to a replacement user in the same transaction, then remove the stale user.
    replacement_user_id = uuid.uuid4()
    await _insert_user(
        engine,
        replacement_user_id,
        f"replacement-{replacement_user_id.hex[:8]}@test.example",
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_id)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        await conn.execute(
            text(
                "UPDATE organisation_memberships SET org_role = 'viewer' "
                "WHERE user_id = :uid AND organisation_id = :oid"
            ),
            {"uid": user_id, "oid": org_id},
        )
        await conn.execute(
            text(
                "INSERT INTO organisation_memberships "
                "(id, user_id, organisation_id, org_role) "
                "VALUES (:id, :uid, :oid, 'owner')"
            ),
            {"id": uuid.uuid4(), "uid": replacement_user_id, "oid": org_id},
        )
        await conn.execute(
            text(
                "DELETE FROM organisation_memberships "
                "WHERE user_id = :uid AND organisation_id = :oid"
            ),
            {"uid": user_id, "oid": org_id},
        )

    # Step 6: same JWT (valid, not expired) — but live membership is gone.
    # Must be rejected IMMEDIATELY with 401 (not at JWT expiry).
    me_after = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_after.status_code == 401, (
        f"Request with valid-but-orphaned JWT must return 401 immediately. "
        f"Got {me_after.status_code}: {me_after.text}\n"
        "Live membership re-verification in get_current_membership() must "
        "reject this request without waiting for the JWT to expire."
    )
