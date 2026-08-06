"""
Org/workspace runtime context selector endpoints.

These endpoints allow an already-authenticated user to switch their active
organisation or workspace context without re-authenticating.  The user must
already hold a valid access token.

Design:
- GET  /me/context             — return current org/workspace context from JWT + live DB
- POST /me/switch-org          — issue new access token scoped to target organisation
- POST /me/switch-workspace    — issue new access token scoped to target workspace

The POST endpoints:
1. Verify the current access token (JWT + live DB membership).
2. Verify the user is a member of the target org/workspace (live DB).
3. Re-issue the access token with the new org/workspace claims.
   A fresh jti is generated per issue() call (RFC 7519 §4.1.7).
   The CSRF binding uses the 'fid' (family_id) claim which is stable
   across context switches within the same login session.
4. Emit an audit event transactionally.

These endpoints do NOT rotate the refresh token — only the short-lived access
token changes.  The refresh token's org claim will be stale after a switch;
the next /auth/refresh will restore it.  Callers should refresh promptly.

Security invariants:
- workspace context is validated against the CURRENT organisation context —
  a user cannot switch to a workspace belonging to a different organisation
  without first switching to that organisation.
- cross-org workspace access is enforced by:
    a. explicit WHERE workspace.organisation_id == payload.organisation_id
    b. WorkspaceMembership composite FK (workspace_id, organisation_id)
       references workspaces(id, organisation_id) — DB-level guarantee.
- org membership is always re-verified from the live DB on every request
  via get_current_membership (see deps.py).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CurrentMembership,
    CurrentPayload,
    RawDB,
)
from app.auth.tokens import JWTService
from app.db.models.membership import OrganisationMembership, WorkspaceMembership
from app.db.models.organisation import Organisation
from app.db.models.workspace import Workspace
from app.services.audit import AuditService

router = APIRouter(prefix="/me", tags=["selector"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ContextResponse(BaseModel):
    user_id: uuid.UUID
    organisation_id: uuid.UUID
    org_role: str | None
    organisation_slug: str
    organisation_display_name: str
    workspace_id: uuid.UUID | None = None
    workspace_role: str | None = None
    workspace_slug: str | None = None


class SwitchOrgRequest(BaseModel):
    organisation_id: uuid.UUID


class SwitchOrgResponse(BaseModel):
    access_token: str
    expires_in: int
    organisation_id: uuid.UUID
    org_role: str | None


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: uuid.UUID


class SwitchWorkspaceResponse(BaseModel):
    access_token: str
    expires_in: int
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_role: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/context", response_model=ContextResponse)
async def get_context(
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
) -> ContextResponse:
    """
    Return the current user's active org + workspace context.

    Verifies the JWT org claim against the live database to reflect any
    administrative changes since the token was issued.  If a workspace
    context is present in the JWT, live workspace details are returned.
    """
    result = await db.execute(
        select(Organisation).where(Organisation.id == payload.organisation_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found.",
        )

    workspace_slug: str | None = None
    if payload.workspace_id is not None:
        ws_result = await db.execute(
            select(Workspace).where(
                Workspace.id == payload.workspace_id,
                Workspace.organisation_id == payload.organisation_id,
            )
        )
        ws = ws_result.scalar_one_or_none()
        if ws is not None:
            workspace_slug = ws.slug

    return ContextResponse(
        user_id=payload.user_id,
        organisation_id=payload.organisation_id,
        org_role=membership.org_role,
        organisation_slug=org.slug,
        organisation_display_name=org.display_name,
        workspace_id=payload.workspace_id,
        workspace_role=payload.workspace_role,
        workspace_slug=workspace_slug,
    )


@router.post("/switch-org", response_model=SwitchOrgResponse)
async def switch_org(
    request: Request,
    body: SwitchOrgRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
) -> SwitchOrgResponse:
    """
    Switch the active organisation context.

    Issues a new access token scoped to the target organisation.  The user
    must already be a member of that organisation.  The refresh token is NOT
    rotated; callers should refresh promptly after switching.

    Security:
    - Source of truth for the user's memberships is the live database.
    - The current session's JWT is verified before any switch is attempted.
    - A fresh jti is generated for the new access token (RFC 7519 §4.1.7).
    - CSRF binding is preserved via the family_id claim (fid), which is
      unchanged by org switching.
    - Workspace context is cleared — the new org token carries no workspace claim.
    - Audit event org.context_switched is emitted transactionally.
    """
    # Prevent trivial no-op switches (not a security gate, just UX hygiene).
    if body.organisation_id == payload.organisation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already in the requested organisation context.",
        )

    # Live DB check — user must be an active member of the target org.
    target_membership_result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == payload.user_id,
            OrganisationMembership.organisation_id == body.organisation_id,
        )
    )
    target_membership = target_membership_result.scalar_one_or_none()
    if target_membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the requested organisation.",
        )

    # Issue new access token.  Fresh jti per RFC 7519 §4.1.7.
    # family_id preserved — CSRF binding remains valid.
    # Workspace claims are cleared; user must re-issue switch-workspace after.
    jwt_service = JWTService(settings)
    new_access_token = jwt_service.issue(
        user_id=payload.user_id,
        organisation_id=body.organisation_id,
        org_role=target_membership.org_role,
        family_id=payload.family_id,
    )

    # Emit audit event transactionally (target org context).
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    AuditService.emit_transactional(
        db,
        event_type="org.context_switched",
        organisation_id=body.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "from_organisation_id": str(payload.organisation_id),
            "to_organisation_id": str(body.organisation_id),
        },
        request_id=request_id,
        client_ip=client_ip,
    )
    await db.flush()

    return SwitchOrgResponse(
        access_token=new_access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        organisation_id=body.organisation_id,
        org_role=target_membership.org_role,
    )


@router.post("/switch-workspace", response_model=SwitchWorkspaceResponse)
async def switch_workspace(
    request: Request,
    body: SwitchWorkspaceRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
) -> SwitchWorkspaceResponse:
    """
    Switch the active workspace context within the current organisation.

    Issues a new access token that includes workspace_id and workspace_role
    claims.  The refresh token is NOT rotated.

    Security model:
    1. User must have a valid access token with an active org context.
    2. The target workspace must exist AND belong to the CURRENT organisation
       (payload.organisation_id).  This prevents cross-org workspace access
       even if the workspace_id is known.
    3. The user must have an active WorkspaceMembership in this workspace.
    4. workspace_role is loaded from the live DB — never from the request.
    5. A fresh jti is generated per RFC 7519 §4.1.7.
    6. CSRF binding is preserved via the family_id (fid) claim.
    7. Audit event workspace.context_switched is emitted transactionally.

    Cross-org prevention layers:
    a. Explicit WHERE workspace.organisation_id == payload.organisation_id.
    b. DB-level: WorkspaceMembership FK (workspace_id, organisation_id) →
       workspaces(id, organisation_id) — a workspace can never be joined with
       a mismatched org at the DB level.
    """
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    # Verify workspace exists in the CURRENT organisation.
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == body.workspace_id,
            Workspace.organisation_id == payload.organisation_id,
        )
    )
    workspace = ws_result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found in the current organisation.",
        )

    if not workspace.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace is not active.",
        )

    # Verify the user has a WorkspaceMembership in this workspace.
    wm_result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == body.workspace_id,
            WorkspaceMembership.organisation_id == payload.organisation_id,
            WorkspaceMembership.user_id == payload.user_id,
        )
    )
    ws_membership = wm_result.scalar_one_or_none()
    if ws_membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the requested workspace.",
        )

    # Issue new access token with workspace claims.  Fresh jti; family_id preserved.
    jwt_service = JWTService(settings)
    new_access_token = jwt_service.issue(
        user_id=payload.user_id,
        organisation_id=payload.organisation_id,
        org_role=membership.org_role,
        family_id=payload.family_id,
        workspace_id=workspace.id,
        workspace_role=ws_membership.workspace_role,
    )

    # Emit audit event transactionally.
    AuditService.emit_transactional(
        db,
        event_type="workspace.context_switched",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "workspace_id": str(workspace.id),
            "workspace_role": ws_membership.workspace_role,
        },
        request_id=request_id,
        client_ip=client_ip,
    )
    await db.flush()

    return SwitchWorkspaceResponse(
        access_token=new_access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        organisation_id=payload.organisation_id,
        workspace_id=workspace.id,
        workspace_role=ws_membership.workspace_role,
    )
