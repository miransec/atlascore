"""Focused runtime proof for capability bootstrap under PostgreSQL FORCE RLS.

This script is verification-only. It creates isolated random rows in atlascore_test,
proves invitation acceptance, API-key authentication, refresh-token lookup/rotation,
and tenant-independent audit emission through the restricted application role, then
cleans up through the migration/admin role.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.refresh import RefreshTokenService
from app.core.config import Settings
from app.services.audit import AuditService
from app.services.invitation_service import InvitationService
from app.services.service_account_service import ServiceAccountService


async def main() -> None:
    app_url = os.getenv("DATABASE_URL_TEST") or os.environ["DATABASE_URL"]
    admin_url = os.getenv("DATABASE_ADMIN_URL_TEST") or os.environ["DATABASE_URL_ADMIN"]
    settings = Settings(DATABASE_URL=app_url, ENVIRONMENT="test", SECURE_COOKIES=False)

    app_engine = create_async_engine(app_url)
    admin_engine = create_async_engine(admin_url)
    app_sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    admin_sessions = async_sessionmaker(admin_engine, expire_on_commit=False)

    org_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    invitee_id = uuid.uuid4()
    owner_email = f"owner-{owner_id.hex[:12]}@runtime.test"
    invitee_email = f"invitee-{invitee_id.hex[:12]}@runtime.test"
    slug = f"runtime-{org_id.hex[:12]}"
    request_id = f"runtime-smoke-{uuid.uuid4()}"

    invitation_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    service_account_id: uuid.UUID | None = None

    try:
        # Migration/admin role owns test setup only. The security proofs below use
        # the restricted app engine.
        async with admin_engine.begin() as conn:
            for user_id, email in ((owner_id, owner_email), (invitee_id, invitee_email)):
                await conn.execute(
                    text(
                        "INSERT INTO users "
                        "(id, email, full_name, password_hash, pepper_version) "
                        "VALUES (:id, :email, 'Runtime Smoke', 'unused', 1)"
                    ),
                    {"id": user_id, "email": email},
                )
            await conn.execute(
                text(
                    "INSERT INTO organisations (id, slug, display_name) "
                    "VALUES (:id, :slug, 'Runtime Smoke Org')"
                ),
                {"id": org_id, "slug": slug},
            )
            await conn.execute(
                text(
                    "INSERT INTO organisation_memberships "
                    "(id, user_id, organisation_id, org_role) "
                    "VALUES (:id, :uid, :oid, 'owner')"
                ),
                {"id": uuid.uuid4(), "uid": owner_id, "oid": org_id},
            )

        invitation_service = InvitationService(settings)
        service_account_service = ServiceAccountService(settings)
        refresh_service = RefreshTokenService(settings)

        # Create capabilities under the admin fixture role, then consume them via
        # the real restricted app role without pre-setting tenant GUCs.
        async with admin_sessions() as setup:
            invitation, raw_invitation = await invitation_service.create(
                setup,
                organisation_id=org_id,
                invited_email=invitee_email,
                org_role="viewer",
                created_by_user_id=owner_id,
            )
            invitation_id = invitation.id

            service_account = await service_account_service.create_service_account(
                setup,
                organisation_id=org_id,
                name=f"runtime-{uuid.uuid4().hex[:8]}",
                created_by_user_id=owner_id,
            )
            service_account_id = service_account.id
            api_key, raw_api_key = await service_account_service.create_api_key(
                setup,
                service_account_id=service_account.id,
                organisation_id=org_id,
                name="runtime-smoke-key",
                scopes=["org:read"],
            )
            api_key_id = api_key.id

            raw_refresh, refresh_row = await refresh_service.create(
                setup,
                user_id=owner_id,
                organisation_id=org_id,
                client_ip="127.0.0.1",
            )
            await setup.commit()

        # Invitation token must establish only the candidate tenant scope; the
        # keyed token hash and email match remain the authentication checks.
        async with app_sessions() as app_session:
            accepted = await invitation_service.accept(
                app_session,
                raw_token=raw_invitation,
                accepting_user_id=invitee_id,
                accepting_user_email=invitee_email,
            )
            assert accepted.id == invitation_id
            await app_session.commit()

        # API key must authenticate through FORCE RLS with no caller-set GUC.
        async with app_sessions() as app_session:
            key, service_account = await service_account_service.authenticate_api_key(
                app_session,
                raw_key=raw_api_key,
                required_scopes=["org:read"],
            )
            assert key.id == api_key_id
            assert service_account.id == service_account_id
            await app_session.rollback()

        # Refresh token lookup and rotation must work before any JWT/org context
        # exists on the request connection.
        async with app_sessions() as app_session:
            found = await refresh_service.find_active_by_raw_token(app_session, raw_token=raw_refresh)
            assert found is not None and found.id == refresh_row.id
            rotated = await refresh_service.rotate(app_session, raw_token=raw_refresh, client_ip="127.0.0.1")
            assert rotated is not None
            new_raw, new_row = rotated
            assert new_row.organisation_id == org_id
            assert new_raw != raw_refresh
            await app_session.commit()

        # Durable tenant audit uses an independent restricted app-role session and
        # must explicitly bootstrap FORCE-RLS from a trusted server-side org id.
        await AuditService.emit_tenant_independent(
            app_sessions,
            event_type="invitation.expired",
            organisation_id=org_id,
            actor_user_id=invitee_id,
            event_data={"verification": True},
            request_id=request_id,
            client_ip="127.0.0.1",
            outcome="failure",
        )

        async with admin_engine.connect() as conn:
            accepted_count = await conn.scalar(
                text(
                    "SELECT count(*) FROM organisation_memberships "
                    "WHERE organisation_id=:oid AND user_id=:uid"
                ),
                {"oid": org_id, "uid": invitee_id},
            )
            audit_count = await conn.scalar(
                text("SELECT count(*) FROM audit_events WHERE request_id=:rid"),
                {"rid": request_id},
            )
            assert accepted_count == 1
            assert audit_count == 1

        print("RUNTIME_SECURITY_SMOKE_PASS")
    finally:
        # Cleanup is migration-role only and intentionally independent of app RLS.
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM audit_events WHERE request_id=:rid"),
                {"rid": request_id},
            )
            await conn.execute(
                text("DELETE FROM organisations WHERE id=:oid"),
                {"oid": org_id},
            )
            await conn.execute(
                text("DELETE FROM users WHERE id IN (:owner_id, :invitee_id)"),
                {"owner_id": owner_id, "invitee_id": invitee_id},
            )
        await app_engine.dispose()
        await admin_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
