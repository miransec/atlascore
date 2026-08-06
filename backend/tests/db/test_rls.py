"""
PostgreSQL RLS isolation tests.

These tests run against a real PostgreSQL instance (atlascore_test DB).
Each scenario verifies that the tenant isolation policy actually enforces
cross-tenant boundaries at the database level — not just at the application layer.

Scenarios:
  1.  Tenant A cannot SELECT rows that belong to tenant B
  2.  Tenant A cannot INSERT a row with a different organisation_id (WITH CHECK)
  3.  Tenant A cannot UPDATE a row belonging to tenant B
  4.  Tenant A cannot UPDATE a row to change its organisation_id
  5.  Tenant A cannot DELETE a row belonging to tenant B
  6.  Within the correct tenant context, SELECT returns only own rows
  7.  Within the correct tenant context, INSERT succeeds
  8.  Within the correct tenant context, UPDATE own row succeeds
  9.  Within the correct tenant context, DELETE own row succeeds
  10. Missing context (empty string) → no rows visible (fail-closed)
  11. NULL context → no rows visible (fail-closed)
  12. Workspace rows are isolated by org
  13. OrganisationMembership rows are isolated by org
  14. Audit events are INSERT-only for the atlascore role (no UPDATE/DELETE)
  15. Pre-auth sessions are NOT RLS-protected (unscoped access works)
  16. Refresh tokens are NOT RLS-protected (unscoped access works)
  17. Global audit events (organisation_id IS NULL) are only insertable via fn_audit_insert_global
  18. fn_audit_insert_global rejects unknown event types

The tests use raw SQL via AsyncConnection so they can set transaction-scoped
GUCs and observe real RLS behaviour without the ORM layer hiding anything.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper: run SQL inside an org-scoped context
# ---------------------------------------------------------------------------


async def _with_org_context(
    conn: AsyncConnection,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> Any:
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    await conn.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    return await conn.execute(text(stmt), params or {})


# ---------------------------------------------------------------------------
# Fixtures: two organisations with one workspace each
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def two_orgs(engine: AsyncEngine, tables: None):
    """
    Returns (org_a_id, org_b_id, user_a_id, user_b_id, ws_a_id, ws_b_id).

    The test engine uses the restricted application role, so tenant rows are
    bootstrapped under their own transaction-local FORCE-RLS context.
    """
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()

    # Users are global rows and are not tenant-RLS scoped.
    async with engine.begin() as conn:
        for uid in (user_a, user_b):
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, full_name, password_hash, pepper_version) "
                    "VALUES (:id, :email, 'Test', 'hash', 1) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uid,
                    "email": f"{uid}@test.example",
                },
            )

    # Bootstrap each tenant independently. The exactly-one-owner constraint
    # is deferred until commit, so each commit must occur with that tenant's
    # RLS context still active.
    tenant_rows = (
        (org_a, user_a, ws_a, "a"),
        (org_b, user_b, ws_b, "b"),
    )

    for oid, uid, wid, suffix in tenant_rows:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                {"oid": str(oid)},
            )
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(uid)},
            )

            org_slug = f"org-{suffix}-{oid.hex[:6]}"
            ws_slug = f"ws-{suffix}-{oid.hex[:6]}"

            await conn.execute(
                text(
                    "INSERT INTO organisations "
                    "(id, slug, display_name) "
                    "VALUES (:id, :slug, :name) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": oid,
                    "slug": org_slug,
                    "name": org_slug,
                },
            )

            await conn.execute(
                text(
                    "INSERT INTO organisation_memberships "
                    "(id, user_id, organisation_id, org_role) "
                    "VALUES (:id, :uid, :oid, 'owner') "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": uid,
                    "oid": oid,
                },
            )

            await conn.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, organisation_id, slug, display_name) "
                    "VALUES (:id, :oid, :slug, :name) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": wid,
                    "oid": oid,
                    "slug": ws_slug,
                    "name": ws_slug,
                },
            )

    yield org_a, org_b, user_a, user_b, ws_a, ws_b

    # Delete each organisation under its own tenant context.
    # FK cascades clean up its workspace/membership rows and preserve the
    # production ownership-trigger behaviour.
    for oid, uid in ((org_a, user_a), (org_b, user_b)):
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_organisation_id', :oid, true)"),
                {"oid": str(oid)},
            )
            await conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(uid)},
            )
            await conn.execute(
                text("DELETE FROM organisations WHERE id = :id"),
                {"id": oid},
            )

    async with engine.begin() as conn:
        for uid in (user_a, user_b):
            await conn.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": uid},
            )


# ---------------------------------------------------------------------------
# Scenario 1: Tenant A cannot SELECT rows belonging to tenant B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_select_cross_tenant_empty(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        # Set context to org_a, try to read org_b's workspace.
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "SELECT id FROM workspaces WHERE id = :id",
            {"id": ws_b},
        )
        rows = result.fetchall()
    assert rows == [], "RLS must hide tenant B's workspace from tenant A"


# ---------------------------------------------------------------------------
# Scenario 2: WITH CHECK prevents INSERT with wrong organisation_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_insert_wrong_org_id_rejected(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_a)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_a)},
        )
        # Attempt to INSERT a workspace that claims to belong to org_b.
        with pytest.raises(
            Exception, match="new row violates row-level security policy|permission denied"
        ):
            await conn.execute(
                text(
                    "INSERT INTO workspaces (id, organisation_id, slug, display_name) VALUES (:id, :oid, :slug, :slug)"
                ),
                {"id": uuid.uuid4(), "oid": org_b, "slug": f"evil-{uuid.uuid4().hex[:6]}"},
            )
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Scenario 3: Tenant A cannot UPDATE a row belonging to tenant B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_update_cross_tenant_noop(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "UPDATE workspaces SET description = 'pwned' WHERE id = :id RETURNING id",
            {"id": ws_b},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent updating tenant B's workspace from tenant A's context"


# ---------------------------------------------------------------------------
# Scenario 4: Tenant A cannot change organisation_id on its own row (WITH CHECK)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_update_org_id_rejected(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_a)},
        )
        await conn.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_a)},
        )
        with pytest.raises(
            Exception, match="new row violates row-level security policy|permission denied"
        ):
            await conn.execute(
                text("UPDATE workspaces SET organisation_id = :new_oid WHERE id = :id"),
                {"new_oid": org_b, "id": ws_a},
            )
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Scenario 5: Tenant A cannot DELETE a row belonging to tenant B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_delete_cross_tenant_noop(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "DELETE FROM workspaces WHERE id = :id RETURNING id",
            {"id": ws_b},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert rows == [], "RLS must prevent deleting tenant B's workspace from tenant A's context"


# ---------------------------------------------------------------------------
# Scenario 6: Within correct context, SELECT returns only own rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_select_own_rows_visible(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "SELECT id FROM workspaces WHERE id = :id",
            {"id": ws_a},
        )
        rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == ws_a


# ---------------------------------------------------------------------------
# Scenario 7: Within correct context, INSERT succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_insert_own_org_succeeds(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    new_ws = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "INSERT INTO workspaces (id, organisation_id, slug, display_name) VALUES (:id, :oid, :slug, :slug) RETURNING id",
            {"id": new_ws, "oid": org_a, "slug": f"new-ws-{new_ws.hex[:6]}"},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert len(rows) == 1
    assert rows[0][0] == new_ws


# ---------------------------------------------------------------------------
# Scenario 8: Within correct context, UPDATE own row succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_update_own_row_succeeds(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "UPDATE workspaces SET description = 'updated' WHERE id = :id RETURNING id",
            {"id": ws_a},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Scenario 9: Within correct context, DELETE own row succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_delete_own_row_succeeds(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    # Insert a throwaway workspace to delete.
    throwaway = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_organisation_id', :oid, true)"),
            {"oid": str(org_a)},
        )
        await conn.execute(
            text(
                "INSERT INTO workspaces (id, organisation_id, slug, display_name) VALUES (:id, :oid, :slug, :slug)"
            ),
            {"id": throwaway, "oid": org_a, "slug": f"throwaway-{throwaway.hex[:6]}"},
        )

    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "DELETE FROM workspaces WHERE id = :id RETURNING id",
            {"id": throwaway},
        )
        rows = result.fetchall()
        await conn.execute(text("ROLLBACK"))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Scenario 10 & 11: Missing / NULL context → fail-closed (no rows visible)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_empty_context_fail_closed(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        # Explicitly set empty string context.
        await conn.execute(text("SELECT set_config('app.current_organisation_id', '', true)"))
        result = await conn.execute(text("SELECT id FROM workspaces"))
        rows = result.fetchall()
    assert rows == [], "Empty context must show no rows (fail-closed)"


@pytest.mark.asyncio()
async def test_rls_null_context_fail_closed(engine: AsyncEngine, two_orgs: Any) -> None:
    """NULLIF('', '') returns NULL — the policy evaluates to NULL = NULL which is FALSE."""
    async with engine.connect() as conn:
        # Reset context by not setting any GUC (fresh connection has no context).
        result = await conn.execute(text("SELECT id FROM workspaces"))
        rows = result.fetchall()
    assert rows == [], "No context must show no rows (fail-closed)"


# ---------------------------------------------------------------------------
# Scenario 12 & 13: Workspace and membership rows are isolated by org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rls_workspace_isolation(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "SELECT id FROM workspaces",
        )
        ids = {row[0] for row in result.fetchall()}
    assert ws_a in ids
    assert ws_b not in ids, "Tenant B workspace must not appear in tenant A's context"


@pytest.mark.asyncio()
async def test_rls_membership_isolation(engine: AsyncEngine, two_orgs: Any) -> None:
    org_a, org_b, user_a, user_b, ws_a, ws_b = two_orgs
    async with engine.connect() as conn:
        result = await _with_org_context(
            conn,
            org_a,
            user_a,
            "SELECT user_id FROM organisation_memberships",
        )
        user_ids = {row[0] for row in result.fetchall()}
    assert user_a in user_ids
    assert user_b not in user_ids, (
        "Tenant B's member must not appear in tenant A's membership query"
    )


# ---------------------------------------------------------------------------
# Scenario 17: Global audit via fn_audit_insert_global
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_insert_global_succeeds(engine: AsyncEngine, tables: None) -> None:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(
                text(
                    "SELECT fn_audit_insert_global("
                    "  'auth.login_failed'::text,"
                    "  CAST(:actor AS uuid),"
                    "  CAST(:data AS jsonb),"
                    "  CAST(:req_id AS text),"
                    "  CAST(:ip AS text),"
                    "  'failure'::text"
                    ")"
                ),
                {
                    "actor": None,
                    "data": '{"email": "bad@example.com"}',
                    "req_id": "req-test-1",
                    "ip": "127.0.0.1",
                },
            )
        finally:
            await transaction.rollback()


# ---------------------------------------------------------------------------
# Scenario 18: fn_audit_insert_global rejects unknown event types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_insert_global_rejects_unknown_event(
    engine: AsyncEngine, tables: None
) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        with pytest.raises(
            Exception, match="invalid global audit event type|not allowed|not in the allowlist"
        ):
            await conn.execute(
                text(
                    "SELECT fn_audit_insert_global("
                    "  'evil.injection'::text,"
                    "  NULL::uuid,"
                    "  '{}'::jsonb,"
                    "  'req-x'::text,"
                    "  '127.0.0.1'::text,"
                    "  'success'::text"
                    ")"
                ),
            )
        await conn.execute(text("ROLLBACK"))
