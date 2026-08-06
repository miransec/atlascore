"""Invitation endpoints — /api/v1/invitations."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    AppSettings,
    CurrentPayload,
    RawDB,
    RequirePermission,
    get_session_factory,
)
from app.auth.permissions import Permission
from app.schemas.invitation import (
    InvitationAcceptRequest,
    InvitationCreatedResponse,
    InvitationCreateRequest,
    InvitationResponse,
    InvitationRevokeRequest,
)
from app.services.audit import AuditService
from app.services.invitation_service import (
    InvitationAlreadyAcceptedError,
    InvitationDuplicateError,
    InvitationEmailMismatchError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    InvitationService,
)

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _get_invitation_service(settings: AppSettings) -> InvitationService:
    return InvitationService(settings)


InvSvc = Annotated[InvitationService, Depends(_get_invitation_service)]


def _invitation_to_response(inv: object) -> InvitationResponse:
    from app.db.models.invitation import Invitation

    assert isinstance(inv, Invitation)
    return InvitationResponse(
        id=inv.id,
        organisation_id=inv.organisation_id,
        workspace_id=inv.workspace_id,
        invited_email=inv.invited_email,
        org_role=inv.org_role,
        workspace_role=inv.workspace_role,
        created_by_user_id=inv.created_by_user_id,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked_at=inv.revoked_at,
        created_at=inv.created_at,
        is_active=inv.is_active,
    )


@router.post(
    "",
    response_model=InvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    body: InvitationCreateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.INVITATION_CREATE))],
    db: RawDB,
    svc: InvSvc,
) -> InvitationCreatedResponse:
    """
    Create an organisation invitation.

    The response includes the raw invitation token EXACTLY ONCE.
    In production, deliver this token to the invitee via email.
    """
    try:
        invitation, raw_token = await svc.create(
            db,
            organisation_id=payload.organisation_id,
            invited_email=body.invited_email,
            org_role=body.org_role,
            workspace_id=body.workspace_id,
            workspace_role=body.workspace_role,
            created_by_user_id=payload.user_id,
            expires_in_hours=body.expires_in_hours,
        )
    except InvitationDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="invitation.created",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "invitation_id": str(invitation.id),
            "invited_email": invitation.invited_email,
            "org_role": invitation.org_role,
            "workspace_id": str(invitation.workspace_id) if invitation.workspace_id else None,
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()

    return InvitationCreatedResponse(
        invitation=_invitation_to_response(invitation),
        raw_token=raw_token,
    )


@router.get("", response_model=list[InvitationResponse])
async def list_invitations(
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.INVITATION_LIST))],
    db: RawDB,
    svc: InvSvc,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> list[InvitationResponse]:
    """List invitations for the current organisation."""
    invitations, _ = await svc.list_for_org(
        db,
        organisation_id=payload.organisation_id,
        active_only=active_only,
        page=page,
        page_size=page_size,
    )
    return [_invitation_to_response(inv) for inv in invitations]


@router.post("/{invitation_id}/revoke", response_model=InvitationResponse)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    body: InvitationRevokeRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.INVITATION_REVOKE))],
    db: RawDB,
    svc: InvSvc,
) -> InvitationResponse:
    """Revoke an active invitation."""
    try:
        invitation = await svc.revoke(
            db,
            invitation_id=invitation_id,
            organisation_id=payload.organisation_id,
        )
    except InvitationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvitationRevokedError, InvitationAlreadyAcceptedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="invitation.revoked",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"invitation_id": str(invitation_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _invitation_to_response(invitation)


@router.post("/accept", response_model=InvitationResponse)
async def accept_invitation(
    body: InvitationAcceptRequest,
    request: Request,
    payload: CurrentPayload,
    db: RawDB,
    svc: InvSvc,
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> InvitationResponse:
    """
    Accept an invitation using a raw token.

    The accepting user's email (from their verified JWT/profile) must match
    the invited_email on the invitation.  Role is taken from the invitation row.

    invitation.expired audit events are written via a separate committed
    session (emit_tenant_independent) — durable regardless of this request's
    transaction outcome.
    """
    # Fetch the authenticated user's email
    from sqlalchemy import select

    from app.db.models.user import User

    user_result = await db.execute(select(User).where(User.id == payload.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    try:
        invitation = await svc.accept(
            db,
            raw_token=body.token,
            accepting_user_id=payload.user_id,
            accepting_user_email=user.email,
            audit_session_factory=session_factory,
        )
    except InvitationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvitationExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except InvitationRevokedError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except InvitationAlreadyAcceptedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvitationEmailMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="invitation.accepted",
        organisation_id=invitation.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"invitation_id": str(invitation.id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _invitation_to_response(invitation)
