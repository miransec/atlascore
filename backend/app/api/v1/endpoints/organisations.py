"""Organisation management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import (
    AppSettings,
    CurrentMembership,
    CurrentPayload,
    RawDB,
    RequirePermission,
)
from app.auth.permissions import Permission
from app.schemas.organisation import (
    InviteMemberRequest,
    MembershipResponse,
    OrganisationResponse,
    OrganisationUpdateRequest,
    TransferOwnershipRequest,
    UpdateMemberRoleRequest,
)
from app.services.org_service import OrgService, OrgServiceError

router = APIRouter(prefix="/organisations", tags=["organisations"])


@router.get("/current", response_model=OrganisationResponse)
async def get_current_organisation(
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
) -> OrganisationResponse:
    """Get the current organisation's details."""
    from sqlalchemy import select

    from app.db.models.organisation import Organisation

    result = await db.execute(
        select(Organisation).where(Organisation.id == payload.organisation_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found.")

    return OrganisationResponse(
        id=org.id,
        slug=org.slug,
        display_name=org.display_name,
        is_active=org.is_active,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.patch("/current", response_model=OrganisationResponse)
async def update_current_organisation(
    request: Request,
    body: OrganisationUpdateRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_UPDATE))],
) -> OrganisationResponse:
    """Update the current organisation's display name."""
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        org = await OrgService.update_organisation(
            db,
            organisation_id=payload.organisation_id,
            display_name=body.display_name,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except OrgServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    return OrganisationResponse(
        id=org.id,
        slug=org.slug,
        display_name=org.display_name,
        is_active=org.is_active,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.get("/current/members", response_model=list[MembershipResponse])
async def list_members(
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_MEMBER_LIST))],
) -> list[MembershipResponse]:
    """List all members of the current organisation."""
    members = await OrgService.list_members(db, organisation_id=payload.organisation_id)
    return [
        MembershipResponse(
            id=mem.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            org_role=mem.org_role,
            created_at=mem.created_at,
        )
        for mem, user in members
    ]


@router.post("/current/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    request: Request,
    body: InviteMemberRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_MEMBER_INVITE))],
) -> MembershipResponse:
    """Add a user to the current organisation."""
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        new_mem = await OrgService.add_member(
            db,
            organisation_id=payload.organisation_id,
            user_id=body.user_id,
            org_role=body.org_role,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except OrgServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()

    from sqlalchemy import select

    from app.db.models.user import User

    user_result = await db.execute(select(User).where(User.id == body.user_id))
    user = user_result.scalar_one()

    return MembershipResponse(
        id=new_mem.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        org_role=new_mem.org_role,
        created_at=new_mem.created_at,
    )


@router.delete("/current/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    request: Request,
    user_id: uuid.UUID,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_MEMBER_REMOVE))],
) -> None:
    """Remove a user from the current organisation."""
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        await OrgService.remove_member(
            db,
            organisation_id=payload.organisation_id,
            user_id=user_id,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except OrgServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()


@router.patch("/current/members/{user_id}/role")
async def change_member_role(
    request: Request,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_MEMBER_ROLE_CHANGE))],
) -> MembershipResponse:
    """Change a member's role in the current organisation."""
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        updated = await OrgService.change_member_role(
            db,
            organisation_id=payload.organisation_id,
            user_id=user_id,
            new_role=body.org_role,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except OrgServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()

    from sqlalchemy import select

    from app.db.models.user import User

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    return MembershipResponse(
        id=updated.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        org_role=updated.org_role,
        created_at=updated.created_at,
    )


@router.post("/current/transfer-ownership", status_code=status.HTTP_204_NO_CONTENT)
async def transfer_ownership(
    request: Request,
    body: TransferOwnershipRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.ORG_TRANSFER_OWNERSHIP))],
) -> None:
    """Transfer organisation ownership to another member."""
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        await OrgService.transfer_ownership(
            db,
            organisation_id=payload.organisation_id,
            current_owner_id=payload.user_id,
            new_owner_id=body.new_owner_user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except OrgServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
