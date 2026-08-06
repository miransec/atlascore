#!/usr/bin/env python3
"""
AtlasCore Phase 1A seed script.

Creates idempotent test data:
  - 4 users across 2 organisations
  - Org A: owner, administrator, analyst, viewer  (4 users, 4 roles)
  - Org B: owner, workflow_builder, operator      (3 users, 3 roles)
  - One member with org_role=NULL (no named role) in Org A
  - 2 workspaces per organisation
  - Covers all 7 OrgRole values across the two orgs

All inserts use ON CONFLICT DO NOTHING so the script is safe to run
multiple times against the same database.

Usage:
  python -m scripts.seed
  # or, from the backend root:
  DATABASE_URL=postgresql+asyncpg://... python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Ensure the backend package is importable when running directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.password import PasswordService
from app.core.config import Settings

_DEFAULT_PASSWORD = "AtlasCore-Seed-2024!"


# ---------------------------------------------------------------------------
# Seed data definition
# ---------------------------------------------------------------------------

_ORGS = [
    {
        "id": "11111111-0000-0000-0000-000000000001",
        "slug": "acme-corp",
        "display_name": "Acme Corporation",
    },
    {
        "id": "22222222-0000-0000-0000-000000000001",
        "slug": "globex-inc",
        "display_name": "Globex Industries",
    },
]

_USERS = [
    # Org A — Acme Corp
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "email": "alice@acme.example",
        "full_name": "Alice Nguyen",
    },
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000002",
        "email": "bob@acme.example",
        "full_name": "Bob Okafor",
    },
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000003",
        "email": "cara@acme.example",
        "full_name": "Cara Espinoza",
    },
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000004",
        "email": "dave@acme.example",
        "full_name": "Dave Müller",
    },
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000005",
        "email": "eve@acme.example",
        "full_name": "Eve Tanaka",
    },
    # Org B — Globex Industries
    {
        "id": "bbbbbbbb-0000-0000-0000-000000000001",
        "email": "frank@globex.example",
        "full_name": "Frank Petrov",
    },
    {
        "id": "bbbbbbbb-0000-0000-0000-000000000002",
        "email": "grace@globex.example",
        "full_name": "Grace Otieno",
    },
    {
        "id": "bbbbbbbb-0000-0000-0000-000000000003",
        "email": "hector@globex.example",
        "full_name": "Hector Silva",
    },
    # Cross-org platform admin (not a member of any org via membership)
    {
        "id": "cccccccc-0000-0000-0000-000000000001",
        "email": "admin@atlascore.example",
        "full_name": "Platform Admin",
        "is_platform_admin": True,
    },
]

_MEMBERSHIPS = [
    # Acme Corp (Org A) — all 7 roles represented across the two orgs
    {
        "org_id": "11111111-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "role": "owner",
    },
    {
        "org_id": "11111111-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000002",
        "role": "administrator",
    },
    {
        "org_id": "11111111-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000003",
        "role": "analyst",
    },
    {
        "org_id": "11111111-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000004",
        "role": "viewer",
    },
    {
        "org_id": "11111111-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000005",
        "role": None,
    },  # null role — member without named role
    # Globex Industries (Org B)
    {
        "org_id": "22222222-0000-0000-0000-000000000001",
        "user_id": "bbbbbbbb-0000-0000-0000-000000000001",
        "role": "owner",
    },
    {
        "org_id": "22222222-0000-0000-0000-000000000001",
        "user_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "role": "workflow_builder",
    },
    {
        "org_id": "22222222-0000-0000-0000-000000000001",
        "user_id": "bbbbbbbb-0000-0000-0000-000000000003",
        "role": "operator",
    },
    # auditor role: dave is also a member of Globex (cross-org member)
    {
        "org_id": "22222222-0000-0000-0000-000000000001",
        "user_id": "aaaaaaaa-0000-0000-0000-000000000004",
        "role": "auditor",
    },
]

_WORKSPACES = [
    # Acme Corp
    {
        "id": "d0000001-0000-0000-0000-000000000001",
        "org_id": "11111111-0000-0000-0000-000000000001",
        "slug": "default",
        "description": "Default workspace",
    },
    {
        "id": "d0000001-0000-0000-0000-000000000002",
        "org_id": "11111111-0000-0000-0000-000000000001",
        "slug": "research-lab",
        "description": "R&D AI experiments",
    },
    # Globex Industries
    {
        "id": "d0000002-0000-0000-0000-000000000001",
        "org_id": "22222222-0000-0000-0000-000000000001",
        "slug": "default",
        "description": "Default workspace",
    },
    {
        "id": "d0000002-0000-0000-0000-000000000002",
        "org_id": "22222222-0000-0000-0000-000000000001",
        "slug": "ops-automation",
        "description": "Operations automation hub",
    },
]


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------


async def seed(session: AsyncSession, password_hash: str, pepper_version: int) -> None:
    print("Seeding organisations…")
    for org in _ORGS:
        await session.execute(
            text(
                "INSERT INTO organisations (id, slug, display_name) "
                "VALUES (:id::uuid, :slug, :display_name) ON CONFLICT (id) DO NOTHING"
            ),
            org,
        )

    print("Seeding users…")
    for user in _USERS:
        await session.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, pepper_version, is_platform_admin) "
                "VALUES (:id::uuid, :email, :full_name, :hash, :pv, :admin) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": user["id"],
                "email": user["email"],
                "full_name": user["full_name"],
                "hash": password_hash,
                "pv": pepper_version,
                "admin": user.get("is_platform_admin", False),
            },
        )

    print("Seeding memberships…")
    for m in _MEMBERSHIPS:
        # Generate a deterministic UUID from the org+user combo.
        namespace = uuid.UUID("00000000-0000-0000-0000-000000000000")
        mid = uuid.uuid5(namespace, f"{m['org_id']}-{m['user_id']}")
        await session.execute(
            text(
                "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
                "VALUES (:id::uuid, :uid::uuid, :oid::uuid, :role) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": str(mid),
                "uid": m["user_id"],
                "oid": m["org_id"],
                "role": m["role"],
            },
        )

    print("Seeding workspaces…")
    for ws in _WORKSPACES:
        await session.execute(
            text(
                "INSERT INTO workspaces (id, organisation_id, slug, description) "
                "VALUES (:id::uuid, :org_id::uuid, :slug, :description) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            ws,
        )

    await session.commit()
    print("Seed complete.")


async def main() -> None:
    settings = Settings()
    pw_svc = PasswordService(
        pepper=settings.ARGON2_PEPPER,
        pepper_version=settings.ARGON2_PEPPER_VERSION,
    )
    password_hash = pw_svc.hash(_DEFAULT_PASSWORD)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed(session, password_hash, settings.ARGON2_PEPPER_VERSION)

    await engine.dispose()

    print()
    print("Seed accounts (all use the same password):")
    print(f"  Password: {_DEFAULT_PASSWORD}")
    print()
    print("  Acme Corp (acme-corp):")
    for u in _USERS[:5]:
        role = next(
            (
                m["role"]
                for m in _MEMBERSHIPS
                if m["user_id"] == u["id"] and m["org_id"] == "11111111-0000-0000-0000-000000000001"
            ),
            "—",
        )
        print(f"    {u['email']:40s}  role={role}")
    print()
    print("  Globex Industries (globex-inc):")
    for uid in [
        "bbbbbbbb-0000-0000-0000-000000000001",
        "bbbbbbbb-0000-0000-0000-000000000002",
        "bbbbbbbb-0000-0000-0000-000000000003",
    ]:
        u = next(x for x in _USERS if x["id"] == uid)
        role = next(
            (
                m["role"]
                for m in _MEMBERSHIPS
                if m["user_id"] == uid and m["org_id"] == "22222222-0000-0000-0000-000000000001"
            ),
            "—",
        )
        print(f"    {u['email']:40s}  role={role}")
    print()
    print("  Platform admin (no org membership):")
    print("    admin@atlascore.example                    is_platform_admin=True")


if __name__ == "__main__":
    asyncio.run(main())
