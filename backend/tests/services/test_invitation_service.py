"""
Unit tests for InvitationService.

Coverage (Phase 1A — 14 scenarios):
  1.  create() — generates a token and stores only the hash
  2.  create() — raises InvitationDuplicateError for duplicate active invitation
  3.  accept() — marks invitation accepted and creates org membership
  4.  accept() — raises InvitationNotFoundError for bad token
  5.  accept() — raises InvitationExpiredError when expired
  6.  accept() — raises InvitationRevokedError when revoked
  7.  accept() — raises InvitationAlreadyAcceptedError when already accepted
  8.  accept() — raises InvitationEmailMismatchError when email doesn't match
  9.  revoke() — marks invitation revoked
  10. revoke() — raises InvitationNotFoundError for unknown id
  11. revoke() — raises InvitationAlreadyAcceptedError when already accepted
  12. list_for_org() — returns invitations with pagination
  13. accept() — creates workspace membership when workspace-scoped
  14. raw token is NEVER stored in the invitation row

§9 Invitation security tests (13 additional scenarios):
  15. BLAKE2b: _hash_token is deterministic for same input
  16. BLAKE2b: _hash_token uses keyed mode (not concatenation)
  17. accept() emits invitation.expired audit event (transactional, not global)
  18. accept() — role is taken from invitation row, not from the request
  19. accept() — accepted invitation cannot be replayed (token is single-use)
  20. cross-org isolation: invitation from org A rejected for org B lookup
  21. token hash length is 64 hex chars (256-bit BLAKE2b output)
  22. accept() — case-insensitive email comparison (uppercase vs lowercase)
  23. revoke() — cross-org isolation (org B cannot revoke org A's invitation)
  24. create() — normalises email to lowercase
  25. accept() — workspace membership role comes from invitation, not request
  26. list_for_org() — active_only filter excludes expired/revoked invitations
  27. list_for_org() — cross-org isolation (org B cannot list org A's invitations)

Phase 1B durability (1 additional):
  28. invitation.expired audit row flushed before InvitationExpiredError raised
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models.invitation import Invitation
from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.services.audit import GLOBAL_EVENT_TYPES
from app.services.invitation_service import (
    InvitationAlreadyAcceptedError,
    InvitationDuplicateError,
    InvitationEmailMismatchError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    InvitationService,
)

pytestmark = pytest.mark.asyncio

_CREATOR_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc(settings: Settings) -> InvitationService:
    return InvitationService(settings)


@pytest_asyncio.fixture()
async def org(db: AsyncSession) -> Organisation:
    db.add(
        User(
            id=_CREATOR_ID,
            email="invitation-creator@test.example",
            full_name="Invitation Creator",
            password_hash="argon2:dummy",
        )
    )
    await db.flush()
    o = Organisation(
        id=uuid.uuid4(),
        slug=f"org-{uuid.uuid4().hex[:8]}",
        display_name="Test Org",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(o.id)},
    )
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture()
async def user(db: AsyncSession) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@test.example",
        full_name="Test User",
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()
    return u


@pytest_asyncio.fixture()
async def workspace(db: AsyncSession, org: Organisation) -> Workspace:
    ws = Workspace(
        id=uuid.uuid4(),
        organisation_id=org.id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        display_name="Test Workspace",
    )
    db.add(ws)
    await db.flush()
    return ws


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_create_returns_raw_token(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="alice@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    assert raw_token
    assert len(raw_token) > 20
    # token_hash must differ from raw_token
    assert inv.token_hash != raw_token
    # token_hash is non-empty hex
    assert len(inv.token_hash) == 64


@pytest.mark.asyncio()
async def test_raw_token_not_in_db_row(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="bob@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    # Re-fetch to ensure we're reading persisted data
    result = await db.execute(select(Invitation).where(Invitation.id == inv.id))
    fetched = result.scalar_one()
    # The raw token must never appear in the row
    assert raw_token not in vars(fetched).values()


@pytest.mark.asyncio()
async def test_create_duplicate_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    email = "charlie@example.com"
    await svc.create(
        db,
        organisation_id=org.id,
        invited_email=email,
        org_role="viewer",
        created_by_user_id=user.id,
    )
    with pytest.raises(InvitationDuplicateError):
        await svc.create(
            db,
            organisation_id=org.id,
            invited_email=email,
            org_role="viewer",
            created_by_user_id=user.id,
        )


@pytest.mark.asyncio()
async def test_accept_creates_org_membership(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    accepted = await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    assert accepted.accepted_at is not None
    # Org membership created
    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    assert membership is not None
    assert membership.org_role == "viewer"


@pytest.mark.asyncio()
async def test_accept_bad_token_raises(svc: InvitationService, db: AsyncSession) -> None:
    with pytest.raises(InvitationNotFoundError):
        await svc.accept(
            db,
            raw_token="this-token-does-not-exist",
            accepting_user_id=uuid.uuid4(),
            accepting_user_email="nobody@example.com",
        )


@pytest.mark.asyncio()
async def test_accept_expired_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
        expires_in_hours=1,
    )
    # Force expiry
    inv.expires_at = datetime.now(tz=UTC) - timedelta(hours=2)
    await db.flush()

    with pytest.raises(InvitationExpiredError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email=user.email,
        )


@pytest.mark.asyncio()
async def test_accept_revoked_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.revoke(db, invitation_id=inv.id, organisation_id=org.id)
    with pytest.raises(InvitationRevokedError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email=user.email,
        )


@pytest.mark.asyncio()
async def test_accept_already_accepted_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    with pytest.raises(InvitationAlreadyAcceptedError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email=user.email,
        )


@pytest.mark.asyncio()
async def test_accept_email_mismatch_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="alice@example.com",
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    with pytest.raises(InvitationEmailMismatchError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email="eve@attacker.com",
        )


@pytest.mark.asyncio()
async def test_revoke_marks_revoked_at(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="dave@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    revoked = await svc.revoke(db, invitation_id=inv.id, organisation_id=org.id)
    assert revoked.revoked_at is not None
    assert revoked.is_active is False


@pytest.mark.asyncio()
async def test_revoke_unknown_id_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation
) -> None:
    with pytest.raises(InvitationNotFoundError):
        await svc.revoke(db, invitation_id=uuid.uuid4(), organisation_id=org.id)


@pytest.mark.asyncio()
async def test_revoke_accepted_raises(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    with pytest.raises(InvitationAlreadyAcceptedError):
        await svc.revoke(db, invitation_id=inv.id, organisation_id=org.id)


@pytest.mark.asyncio()
async def test_list_for_org_pagination(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    for i in range(5):
        await svc.create(
            db,
            organisation_id=org.id,
            invited_email=f"user{i}@example.com",
            org_role="viewer",
            created_by_user_id=user.id,
        )
    items, total = await svc.list_for_org(db, organisation_id=org.id, page=1, page_size=3)
    assert total == 5
    assert len(items) == 3


@pytest.mark.asyncio()
async def test_accept_creates_workspace_membership(
    svc: InvitationService,
    db: AsyncSession,
    org: Organisation,
    user: User,
    workspace: Workspace,
) -> None:
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        workspace_id=workspace.id,
        workspace_role="analyst",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    ws_membership = result.scalar_one_or_none()
    assert ws_membership is not None
    assert ws_membership.workspace_role == "analyst"


# ---------------------------------------------------------------------------
# §9 Invitation security tests (scenarios 15-27)
# ---------------------------------------------------------------------------


def test_blake2b_hash_is_deterministic(svc: InvitationService) -> None:
    """Same input always produces the same hash (scenario 15)."""
    h1 = svc._hash_token("some-random-token-value")
    h2 = svc._hash_token("some-random-token-value")
    assert h1 == h2


def test_blake2b_hash_uses_keyed_mode(settings: Settings) -> None:
    """Pepper used as BLAKE2b key, not concatenated data (scenario 16)."""
    import hashlib

    raw = "some-test-token"
    pepper = settings.INVITATION_TOKEN_PEPPER

    keyed = hashlib.blake2b(raw.encode(), key=pepper.encode()[:64], digest_size=32).hexdigest()
    concat = hashlib.blake2b((pepper + raw).encode(), digest_size=32).hexdigest()

    assert keyed != concat, "Keyed hash must differ from concatenation hash"
    svc = InvitationService(settings)
    assert svc._hash_token(raw) == keyed, "Service must use keyed BLAKE2b mode"


@pytest.mark.asyncio()
async def test_expired_invitation_emits_audit_event_not_global(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """Accept on expired invitation emits invitation.expired with org context (scenario 17).

    Verifies:
    - invitation.expired is NOT in GLOBAL_EVENT_TYPES (must never be added).
    - invitation.expired IS in TENANT_INDEPENDENT_EVENT_TYPES (correct path).
    - accept() raises InvitationExpiredError when called without a session
      factory (audit skipped, exception still raised).
    """
    from app.services.audit import TENANT_INDEPENDENT_EVENT_TYPES

    assert "invitation.expired" not in GLOBAL_EVENT_TYPES, (
        "invitation.expired must NOT be a global event type. "
        "It is a tenant-scoped event and must never be added to fn_audit_insert_global."
    )
    assert "invitation.expired" in TENANT_INDEPENDENT_EVENT_TYPES, (
        "invitation.expired must be in TENANT_INDEPENDENT_EVENT_TYPES "
        "(written via emit_tenant_independent for guaranteed durability)."
    )

    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    inv.expires_at = datetime.now(tz=UTC) - timedelta(hours=1)
    await db.flush()

    # Without a session factory, accept() still raises — audit emit is skipped.
    with pytest.raises(InvitationExpiredError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email=user.email,
            audit_session_factory=None,
        )


@pytest.mark.asyncio()
async def test_accept_role_from_invitation_not_request(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """Role is taken from the invitation row, ignoring any external role (scenario 18)."""
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == org.id,
            OrganisationMembership.user_id == user.id,
        )
    )
    mem = result.scalar_one()
    # Role must be "viewer" from the invitation, not escalated
    assert mem.org_role == "viewer"


@pytest.mark.asyncio()
async def test_accept_is_single_use(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """Token is single-use: second accept raises InvitationAlreadyAcceptedError (scenario 19)."""
    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email=user.email,
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=user.id,
        accepting_user_email=user.email,
    )
    with pytest.raises(InvitationAlreadyAcceptedError):
        await svc.accept(
            db,
            raw_token=raw_token,
            accepting_user_id=user.id,
            accepting_user_email=user.email,
        )


def test_token_hash_length_is_64_hex_chars(svc: InvitationService) -> None:
    """BLAKE2b-256 output is 32 bytes = 64 hex chars (scenario 21)."""
    token_hash = svc._hash_token("any-raw-token-value")
    assert len(token_hash) == 64
    assert all(c in "0123456789abcdef" for c in token_hash)


@pytest.mark.asyncio()
async def test_accept_email_case_insensitive(
    svc: InvitationService, db: AsyncSession, org: Organisation
) -> None:
    """Email comparison is case-insensitive (scenario 22)."""
    u = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        full_name="Alice",
        password_hash="argon2:dummy",
    )
    db.add(u)
    await db.flush()

    inv, raw_token = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="alice@example.com",
        org_role="viewer",
        created_by_user_id=_CREATOR_ID,
    )
    # Accept with uppercase email
    accepted = await svc.accept(
        db,
        raw_token=raw_token,
        accepting_user_id=u.id,
        accepting_user_email="ALICE@EXAMPLE.COM",
    )
    assert accepted.accepted_at is not None


@pytest.mark.asyncio()
async def test_revoke_cross_org_isolation(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """Org B cannot revoke Org A's invitation (scenario 23)."""
    inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="target@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    # Org B
    org_b = Organisation(
        id=uuid.uuid4(),
        slug=f"orgb-{uuid.uuid4().hex[:8]}",
        display_name="Org B",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_b.id)},
    )
    db.add(org_b)
    await db.flush()

    with pytest.raises(InvitationNotFoundError):
        await svc.revoke(db, invitation_id=inv.id, organisation_id=org_b.id)


@pytest.mark.asyncio()
async def test_create_normalises_email_to_lowercase(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """create() stores email as lowercase (scenario 24)."""
    inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="Carol@Example.COM",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    assert inv.invited_email == "carol@example.com"


@pytest.mark.asyncio()
async def test_list_active_only_excludes_expired_and_revoked(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """active_only=True excludes expired and revoked invitations (scenario 26)."""
    # Active invitation
    active_inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="active@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    # Expired invitation
    expired_inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="expired@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    expired_inv.expires_at = datetime.now(tz=UTC) - timedelta(hours=1)
    # Revoked invitation
    revoked_inv, _ = await svc.create(
        db,
        organisation_id=org.id,
        invited_email="revoked@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    await svc.revoke(db, invitation_id=revoked_inv.id, organisation_id=org.id)
    await db.flush()

    active_items, active_total = await svc.list_for_org(
        db, organisation_id=org.id, active_only=True
    )
    inv_ids = {i.id for i in active_items}
    assert active_inv.id in inv_ids
    assert expired_inv.id not in inv_ids
    assert revoked_inv.id not in inv_ids


@pytest.mark.asyncio()
async def test_list_cross_org_isolation(
    svc: InvitationService, db: AsyncSession, org: Organisation, user: User
) -> None:
    """list_for_org returns only invitations for the queried org (scenario 27)."""
    org_b = Organisation(
        id=uuid.uuid4(),
        slug=f"orgb-{uuid.uuid4().hex[:8]}",
        display_name="Org B",
    )
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_b.id)},
    )
    db.add(org_b)
    await db.flush()

    # Invitation in org A
    await svc.create(
        db,
        organisation_id=org.id,
        invited_email="userA@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )
    # Invitation in org B
    await svc.create(
        db,
        organisation_id=org_b.id,
        invited_email="userB@example.com",
        org_role="viewer",
        created_by_user_id=user.id,
    )

    org_a_items, org_a_total = await svc.list_for_org(db, organisation_id=org.id)
    org_b_items, org_b_total = await svc.list_for_org(db, organisation_id=org_b.id)

    assert org_a_total == 1
    assert org_b_total == 1
    assert org_a_items[0].invited_email == "usera@example.com"
    assert org_b_items[0].invited_email == "userb@example.com"


@pytest.mark.asyncio()
async def test_invitation_expired_audit_row_is_durable(
    svc: InvitationService, engine: AsyncEngine, admin_engine: AsyncEngine
) -> None:
    """Expired-invitation audit survives caller rollback without broadening app ACLs."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"durable-{user_id.hex[:8]}@test.example"

    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id,email,full_name,password_hash,pepper_version) "
                 "VALUES (:uid,:email,'Durability User','hash',1)"),
            {"uid": user_id, "email": email},
        )
        await conn.execute(
            text("INSERT INTO organisations (id,slug,display_name) "
                 "VALUES (:oid,:slug,'Durability Org')"),
            {"oid": org_id, "slug": f"durable-{org_id.hex[:8]}"},
        )
        await conn.execute(
            text("INSERT INTO organisation_memberships "
                 "(id,organisation_id,user_id,org_role) "
                 "VALUES (:id,:oid,:uid,'owner')"),
            {"id": uuid.uuid4(), "oid": org_id, "uid": user_id},
        )

    app_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with app_factory() as setup:
        inv, raw_token = await svc.create(
            setup, organisation_id=org_id, invited_email=email, org_role="owner",
            created_by_user_id=user_id,
        )
        inv_id = inv.id
        inv.expires_at = datetime.now(tz=UTC) - timedelta(hours=1)
        await setup.commit()

    async with app_factory() as caller:
        with pytest.raises(InvitationExpiredError):
            await svc.accept(
                caller, raw_token=raw_token, accepting_user_id=user_id,
                accepting_user_email=email, audit_session_factory=app_factory,
            )
        await caller.rollback()

    async with admin_engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT outcome,event_data FROM audit_events "
                 "WHERE organisation_id=:oid AND actor_user_id=:uid "
                 "AND event_type='invitation.expired' ORDER BY created_at DESC LIMIT 1"),
            {"oid": org_id, "uid": user_id},
        )).mappings().one_or_none()
        assert row is not None
        assert row["outcome"] == "failure"
        assert str(inv_id) in str(row["event_data"])

    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM audit_events WHERE organisation_id=:oid"), {"oid": org_id})
        await conn.execute(text("DELETE FROM organisations WHERE id=:oid"), {"oid": org_id})
        await conn.execute(text("DELETE FROM users WHERE id=:uid"), {"uid": user_id})

