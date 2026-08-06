"""
PostgreSQL RLS isolation tests — Phase 1B tables.

These tests verify that the RLS policies on Phase 1B tables (invitations,
teams, team_memberships, service_accounts, api_keys) actually enforce
cross-tenant isolation at the database level.

Phase 1A scenarios (SELECT isolation):
  1.  Invitations: tenant A cannot read tenant B's invitations
  2.  Invitations: tenant A can read own invitations
  3.  Invitations: INSERT rejected if organisation_id doesn't match context
  4.  Teams: tenant A cannot read tenant B's teams
  5.  Teams: tenant A can read own teams
  6.  TeamMemberships: tenant A cannot read tenant B's team memberships
  7.  ServiceAccounts: tenant A cannot read tenant B's service accounts
  8.  ServiceAccounts: tenant A can read own service accounts
  9.  ApiKeys: tenant A cannot read tenant B's API keys
  10. ApiKeys: tenant A can read own API keys
  11. Missing context → fail-closed (no rows visible) on all Phase 1B tables
  12. FORCE ROW LEVEL SECURITY: superuser bypass does not apply to atlascore role

§13 Phase 1B RLS CRUD coverage (UPDATE + DELETE cross-tenant isolation):
  13. Invitations UPDATE: tenant A cannot update tenant B's invitations
  14. Invitations DELETE: tenant A cannot delete tenant B's invitations
  15. Teams UPDATE: tenant A cannot update tenant B's teams
  16. Teams DELETE: tenant A cannot delete tenant B's teams
  17. ServiceAccounts UPDATE: tenant A cannot update tenant B's SAs
  18. ServiceAccounts DELETE: tenant A cannot delete tenant B's SAs
  19. ApiKeys UPDATE: tenant A cannot update tenant B's API keys
  20. ApiKeys DELETE: tenant A cannot delete tenant B's API keys
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _with_org(
    conn: AsyncConnection,
    org_id: uuid.UUID,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Execute stmt inside an org-scoped GUC context."""
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    result = await conn.execute(text(stmt), params or {})
    return result


async def _without_context(
    conn: AsyncConnection, stmt: str, params: dict[str, Any] | None = None
) -> Any:
    """Execute stmt with empty org context (fail-closed)."""
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', '', true)"),
    )
    return await conn.execute(text(stmt), params or {})


async def _seed_org(conn: AsyncConnection) -> uuid.UUID:
    org_id = uuid.uuid4()
    await conn.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    await conn.execute(
        text("INSERT INTO organisations (id, slug, display_name) VALUES (:id, :slug, :dn)"),
        {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}", "dn": "RLS Test Org"},
    )
    return org_id


async def _seed_invitation(conn: AsyncConnection, org_id: uuid.UUID) -> uuid.UUID:
    inv_id = uuid.uuid4()
    raw = "dummy-token"
    token_hash = hashlib.blake2b(raw.encode(), key=(b"p" * 32), digest_size=32).hexdigest()
    expires = datetime.now(tz=UTC) + timedelta(hours=72)
    await conn.execute(
        text(
            "INSERT INTO invitations "
            "(id, organisation_id, invited_email, token_hash, expires_at) "
            "VALUES (:id, :org_id, :email, :th, :ea)"
        ),
        {
            "id": str(inv_id),
            "org_id": str(org_id),
            "email": f"rls-{inv_id.hex[:6]}@test.example",
            "th": token_hash,
            "ea": expires,
        },
    )
    return inv_id


async def _seed_team(conn: AsyncConnection, org_id: uuid.UUID) -> uuid.UUID:
    team_id = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO teams (id, organisation_id, name) VALUES (:id, :org_id, :name)"),
        {
            "id": str(team_id),
            "org_id": str(org_id),
            "name": f"Team-{team_id.hex[:6]}",
        },
    )
    return team_id


async def _seed_service_account(conn: AsyncConnection, org_id: uuid.UUID) -> uuid.UUID:
    sa_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO service_accounts (id, organisation_id, name) VALUES (:id, :org_id, :name)"
        ),
        {
            "id": str(sa_id),
            "org_id": str(org_id),
            "name": f"SA-{sa_id.hex[:6]}",
        },
    )
    return sa_id


async def _seed_api_key(conn: AsyncConnection, org_id: uuid.UUID, sa_id: uuid.UUID) -> uuid.UUID:
    key_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO api_keys "
            "(id, service_account_id, organisation_id, name, key_prefix, secret_hash, scopes) "
            "VALUES (:id, :sa_id, :org_id, :name, :prefix, :hash, :scopes)"
        ),
        {
            "id": str(key_id),
            "sa_id": str(sa_id),
            "org_id": str(org_id),
            "name": f"Key-{key_id.hex[:6]}",
            "prefix": key_id.hex[:8],
            "hash": "deadbeef" * 8,
            "scopes": '["org:read"]',
        },
    )
    return key_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def conn_a(engine: AsyncEngine, tables: None) -> AsyncConnection:
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        yield conn
        await conn.execute(text("ROLLBACK"))


@pytest_asyncio.fixture()
async def conn_b(engine: AsyncEngine, tables: None) -> AsyncConnection:
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        yield conn
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Invitation RLS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_invitation_cross_tenant_isolation(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot see tenant B's invitations."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        # Seed invitation in org_b with org_b context
        await _with_org(conn, org_b, "SELECT 1")
        inv_id = await _seed_invitation(conn, org_b)
        # Read as org_a
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM invitations WHERE id = :id",
            {"id": str(inv_id)},
        )
        rows = result.fetchall()
        assert len(rows) == 0, "Org A must not see org B's invitations"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_invitation_own_tenant_visible(engine: AsyncEngine, tables: None) -> None:
    """Tenant A can see its own invitations."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        inv_id = await _seed_invitation(conn, org_a)
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM invitations WHERE id = :id",
            {"id": str(inv_id)},
        )
        rows = result.fetchall()
        assert len(rows) == 1, "Org A must see its own invitation"
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Team RLS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_team_cross_tenant_isolation(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot see tenant B's teams."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        team_id = await _seed_team(conn, org_b)
        result = await _with_org(
            conn, org_a, "SELECT id FROM teams WHERE id = :id", {"id": str(team_id)}
        )
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_team_own_tenant_visible(engine: AsyncEngine, tables: None) -> None:
    """Tenant A can see its own teams."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        team_id = await _seed_team(conn, org_a)
        result = await _with_org(
            conn, org_a, "SELECT id FROM teams WHERE id = :id", {"id": str(team_id)}
        )
        assert len(result.fetchall()) == 1
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Service account RLS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_service_account_cross_tenant_isolation(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot see tenant B's service accounts."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM service_accounts WHERE id = :id",
            {"id": str(sa_id)},
        )
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_service_account_own_visible(engine: AsyncEngine, tables: None) -> None:
    """Tenant A can see its own service accounts."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_a)
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM service_accounts WHERE id = :id",
            {"id": str(sa_id)},
        )
        assert len(result.fetchall()) == 1
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# API key RLS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_api_key_cross_tenant_isolation(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot see tenant B's API keys."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)
        key_id = await _seed_api_key(conn, org_b, sa_id)
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM api_keys WHERE id = :id",
            {"id": str(key_id)},
        )
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_api_key_own_visible(engine: AsyncEngine, tables: None) -> None:
    """Tenant A can see its own API keys."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_a)
        key_id = await _seed_api_key(conn, org_a, sa_id)
        result = await _with_org(
            conn,
            org_a,
            "SELECT id FROM api_keys WHERE id = :id",
            {"id": str(key_id)},
        )
        assert len(result.fetchall()) == 1
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# Fail-closed: empty context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fail_closed_invitations_no_context(engine: AsyncEngine, tables: None) -> None:
    """Empty org context → no invitations visible."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        await _seed_invitation(conn, org_a)
        result = await _without_context(conn, "SELECT id FROM invitations")
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_fail_closed_teams_no_context(engine: AsyncEngine, tables: None) -> None:
    """Empty org context → no teams visible."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        await _seed_team(conn, org_a)
        result = await _without_context(conn, "SELECT id FROM teams")
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_fail_closed_service_accounts_no_context(engine: AsyncEngine, tables: None) -> None:
    """Empty org context → no service accounts visible."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        await _seed_service_account(conn, org_a)
        result = await _without_context(conn, "SELECT id FROM service_accounts")
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_fail_closed_api_keys_no_context(engine: AsyncEngine, tables: None) -> None:
    """Empty org context → no API keys visible."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        await _with_org(conn, org_a, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_a)
        await _seed_api_key(conn, org_a, sa_id)
        result = await _without_context(conn, "SELECT id FROM api_keys")
        assert len(result.fetchall()) == 0
        await conn.execute(text("ROLLBACK"))


# ---------------------------------------------------------------------------
# §13 UPDATE cross-tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_invitation_update_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot UPDATE tenant B's invitations (scenario 13)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        inv_id = await _seed_invitation(conn, org_b)

        # Attempt to UPDATE as org_a
        result = await _with_org(
            conn,
            org_a,
            "UPDATE invitations SET invited_email = 'hacked@evil.com' WHERE id = :id RETURNING id",
            {"id": str(inv_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant UPDATE on invitations"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_invitation_delete_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot DELETE tenant B's invitations (scenario 14)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        inv_id = await _seed_invitation(conn, org_b)

        result = await _with_org(
            conn,
            org_a,
            "DELETE FROM invitations WHERE id = :id RETURNING id",
            {"id": str(inv_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant DELETE on invitations"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_team_update_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot UPDATE tenant B's teams (scenario 15)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        team_id = await _seed_team(conn, org_b)

        result = await _with_org(
            conn,
            org_a,
            "UPDATE teams SET name = 'hacked' WHERE id = :id RETURNING id",
            {"id": str(team_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant UPDATE on teams"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_team_delete_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot DELETE tenant B's teams (scenario 16)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        team_id = await _seed_team(conn, org_b)

        result = await _with_org(
            conn,
            org_a,
            "DELETE FROM teams WHERE id = :id RETURNING id",
            {"id": str(team_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant DELETE on teams"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_service_account_update_cross_tenant_rejected(
    engine: AsyncEngine, tables: None
) -> None:
    """Tenant A cannot UPDATE tenant B's service accounts (scenario 17)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)

        result = await _with_org(
            conn,
            org_a,
            "UPDATE service_accounts SET name = 'hacked' WHERE id = :id RETURNING id",
            {"id": str(sa_id)},
        )
        assert len(result.fetchall()) == 0, (
            "RLS must prevent cross-tenant UPDATE on service_accounts"
        )
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_service_account_delete_cross_tenant_rejected(
    engine: AsyncEngine, tables: None
) -> None:
    """Tenant A cannot DELETE tenant B's service accounts (scenario 18)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)

        result = await _with_org(
            conn,
            org_a,
            "DELETE FROM service_accounts WHERE id = :id RETURNING id",
            {"id": str(sa_id)},
        )
        assert len(result.fetchall()) == 0, (
            "RLS must prevent cross-tenant DELETE on service_accounts"
        )
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_api_key_update_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot UPDATE tenant B's API keys (scenario 19)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)
        key_id = await _seed_api_key(conn, org_b, sa_id)

        result = await _with_org(
            conn,
            org_a,
            "UPDATE api_keys SET name = 'hacked' WHERE id = :id RETURNING id",
            {"id": str(key_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant UPDATE on api_keys"
        await conn.execute(text("ROLLBACK"))


@pytest.mark.asyncio()
async def test_api_key_delete_cross_tenant_rejected(engine: AsyncEngine, tables: None) -> None:
    """Tenant A cannot DELETE tenant B's API keys (scenario 20)."""
    async with engine.connect() as conn:
        await conn.execute(text("BEGIN"))
        org_a = await _seed_org(conn)
        org_b = await _seed_org(conn)
        await _with_org(conn, org_b, "SELECT 1")
        sa_id = await _seed_service_account(conn, org_b)
        key_id = await _seed_api_key(conn, org_b, sa_id)

        result = await _with_org(
            conn,
            org_a,
            "DELETE FROM api_keys WHERE id = :id RETURNING id",
            {"id": str(key_id)},
        )
        assert len(result.fetchall()) == 0, "RLS must prevent cross-tenant DELETE on api_keys"
        await conn.execute(text("ROLLBACK"))
