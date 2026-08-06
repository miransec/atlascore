"""Workspace and workspace membership service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.db.models.workspace import Workspace
from app.services.audit import AuditService


class WorkspaceServiceError(Exception):
    """Domain error from workspace operations."""


class WorkspaceService:
    """Business logic for workspace management."""

    @staticmethod
    async def create_workspace(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        slug: str,
        display_name: str,
        description: str | None = None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> Workspace:
        """Create a workspace within an organisation."""
        # Check slug uniqueness within org
        slug_check = await session.execute(
            select(Workspace.id).where(
                Workspace.organisation_id == organisation_id,
                Workspace.slug == slug,
            )
        )
        if slug_check.scalar_one_or_none() is not None:
            raise WorkspaceServiceError(
                f"Workspace slug {slug!r} is already taken in this organisation."
            )

        workspace = Workspace(
            organisation_id=organisation_id,
            slug=slug,
            display_name=display_name,
            description=description,
            is_active=True,
        )
        session.add(workspace)
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="workspace.created",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"workspace_id": str(workspace.id), "slug": slug},
            request_id=request_id,
            client_ip=client_ip,
        )
        return workspace

    @staticmethod
    async def list_workspaces(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
    ) -> list[Workspace]:
        """List all workspaces in an organisation (RLS applied)."""
        result = await session.execute(
            select(Workspace)
            .where(
                Workspace.organisation_id == organisation_id,
                Workspace.is_active.is_(True),
            )
            .order_by(Workspace.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_workspace(
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> Workspace | None:
        """Fetch a single workspace (RLS applied)."""
        result = await session.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.organisation_id == organisation_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_workspace(
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        organisation_id: uuid.UUID,
        display_name: str | None = None,
        description: str | None = None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> Workspace:
        """Update workspace metadata."""
        result = await session.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.organisation_id == organisation_id,
            )
        )
        ws = result.scalar_one_or_none()
        if ws is None:
            raise WorkspaceServiceError("Workspace not found.")

        if display_name is not None:
            ws.display_name = display_name
        if description is not None:
            ws.description = description
        await session.flush()
        return ws

    @staticmethod
    async def add_member(
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        workspace_role: str,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> WorkspaceMembership:
        """Add a user to a workspace."""
        # User must be an org member
        org_mem = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        if org_mem.scalar_one_or_none() is None:
            raise WorkspaceServiceError("User must be an organisation member to join a workspace.")

        # Check workspace exists in org
        ws_check = await session.execute(
            select(Workspace.id).where(
                Workspace.id == workspace_id,
                Workspace.organisation_id == organisation_id,
            )
        )
        if ws_check.scalar_one_or_none() is None:
            raise WorkspaceServiceError("Workspace not found.")

        # Check not already a member
        existing = await session.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise WorkspaceServiceError("User is already a member of this workspace.")

        wm = WorkspaceMembership(
            workspace_id=workspace_id,
            organisation_id=organisation_id,
            user_id=user_id,
            workspace_role=workspace_role,
        )
        session.add(wm)
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="workspace.membership_added",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={
                "workspace_id": str(workspace_id),
                "target_user_id": str(user_id),
                "workspace_role": workspace_role,
            },
            request_id=request_id,
            client_ip=client_ip,
        )
        return wm

    @staticmethod
    async def remove_member(
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Remove a user from a workspace."""
        result = await session.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
        )
        wm = result.scalar_one_or_none()
        if wm is None:
            raise WorkspaceServiceError("User is not a member of this workspace.")

        await session.delete(wm)
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="workspace.membership_removed",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={
                "workspace_id": str(workspace_id),
                "target_user_id": str(user_id),
            },
            request_id=request_id,
            client_ip=client_ip,
        )
