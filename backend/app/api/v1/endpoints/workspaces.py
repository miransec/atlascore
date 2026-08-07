"""Workspace management endpoints."""

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
from app.schemas.workspace import (
    AddWorkspaceMemberRequest,
    WorkspaceCreateRequest,
    WorkspaceMembershipResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from app.services.workspace_service import WorkspaceService, WorkspaceServiceError

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkspaceResponse)
async def create_workspace(
    request: Request,
    body: WorkspaceCreateRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_CREATE))],
) -> WorkspaceResponse:
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        ws = await WorkspaceService.create_workspace(
            db,
            organisation_id=payload.organisation_id,
            slug=body.slug,
            display_name=body.display_name,
            description=body.description,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except WorkspaceServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    return WorkspaceResponse(
        id=ws.id,
        organisation_id=ws.organisation_id,
        slug=ws.slug,
        display_name=ws.display_name,
        description=ws.description,
        is_active=ws.is_active,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_READ))],
) -> list[WorkspaceResponse]:
    workspaces = await WorkspaceService.list_workspaces(db, organisation_id=payload.organisation_id)
    return [
        WorkspaceResponse(
            id=ws.id,
            organisation_id=ws.organisation_id,
            slug=ws.slug,
            display_name=ws.display_name,
            description=ws.description,
            is_active=ws.is_active,
            created_at=ws.created_at,
            updated_at=ws.updated_at,
        )
        for ws in workspaces
    ]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: uuid.UUID,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_READ))],
) -> WorkspaceResponse:
    ws = await WorkspaceService.get_workspace(
        db,
        workspace_id=workspace_id,
        organisation_id=payload.organisation_id,
    )
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return WorkspaceResponse(
        id=ws.id,
        organisation_id=ws.organisation_id,
        slug=ws.slug,
        display_name=ws.display_name,
        description=ws.description,
        is_active=ws.is_active,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    request: Request,
    workspace_id: uuid.UUID,
    body: WorkspaceUpdateRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_UPDATE))],
) -> WorkspaceResponse:
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        ws = await WorkspaceService.update_workspace(
            db,
            workspace_id=workspace_id,
            organisation_id=payload.organisation_id,
            display_name=body.display_name,
            description=body.description,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except WorkspaceServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    return WorkspaceResponse(
        id=ws.id,
        organisation_id=ws.organisation_id,
        slug=ws.slug,
        display_name=ws.display_name,
        description=ws.description,
        is_active=ws.is_active,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMembershipResponse])
async def list_workspace_members(
    workspace_id: uuid.UUID,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_READ))],
) -> list[WorkspaceMembershipResponse]:
    try:
        members = await WorkspaceService.list_members(
            db,
            workspace_id=workspace_id,
            organisation_id=payload.organisation_id,
        )
    except WorkspaceServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from sqlalchemy import select

    from app.db.models.user import User

    if not members:
        return []

    user_ids = [m.user_id for m in members]
    user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in user_result.scalars().all()}

    responses: list[WorkspaceMembershipResponse] = []
    for wm in members:
        user = users_by_id.get(wm.user_id)
        if user is None:
            continue
        responses.append(
            WorkspaceMembershipResponse(
                id=wm.id,
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                workspace_role=wm.workspace_role,
                created_at=wm.created_at,
            )
        )
    return responses


@router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
async def add_workspace_member(
    request: Request,
    workspace_id: uuid.UUID,
    body: AddWorkspaceMemberRequest,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_MEMBER_MANAGE))],
) -> WorkspaceMembershipResponse:
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        wm = await WorkspaceService.add_member(
            db,
            workspace_id=workspace_id,
            organisation_id=payload.organisation_id,
            user_id=body.user_id,
            workspace_role=body.workspace_role,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except WorkspaceServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()

    from sqlalchemy import select

    from app.db.models.user import User

    user_result = await db.execute(select(User).where(User.id == body.user_id))
    user = user_result.scalar_one()

    return WorkspaceMembershipResponse(
        id=wm.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        workspace_role=wm.workspace_role,
        created_at=wm.created_at,
    )


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_workspace_member(
    request: Request,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
    settings: AppSettings,
    _perm: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_MEMBER_MANAGE))],
) -> None:
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        await WorkspaceService.remove_member(
            db,
            workspace_id=workspace_id,
            organisation_id=payload.organisation_id,
            user_id=user_id,
            actor_user_id=payload.user_id,
            request_id=request_id,
            client_ip=client_ip,
        )
    except WorkspaceServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
