"""Team endpoints — /api/v1/teams."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import CurrentPayload, RawDB, RequirePermission
from app.auth.permissions import Permission
from app.schemas.team import (
    PaginatedTeamsResponse,
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdateRequest,
)
from app.services.audit import AuditService
from app.services.team_service import (
    TeamDuplicateError,
    TeamMemberAlreadyExistsError,
    TeamMemberNotFoundError,
    TeamMemberNotOrgMemberError,
    TeamNotFoundError,
    TeamService,
)

router = APIRouter(prefix="/teams", tags=["teams"])


def _get_team_service() -> TeamService:
    return TeamService()


TeamSvc = Annotated[TeamService, Depends(_get_team_service)]


def _team_to_response(team: object) -> TeamResponse:
    from app.db.models.team import Team

    assert isinstance(team, Team)
    return TeamResponse(
        id=team.id,
        organisation_id=team.organisation_id,
        workspace_id=team.workspace_id,
        name=team.name,
        description=team.description,
        created_by_user_id=team.created_by_user_id,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def _team_member_to_response(membership: object) -> TeamMemberResponse:
    from app.db.models.team import TeamMembership

    assert isinstance(membership, TeamMembership)
    return TeamMemberResponse(
        id=membership.id,
        team_id=membership.team_id,
        user_id=membership.user_id,
        organisation_id=membership.organisation_id,
        created_at=membership.created_at,
    )


@router.post(
    "",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    body: TeamCreateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_CREATE))],
    db: RawDB,
    svc: TeamSvc,
) -> TeamResponse:
    """Create a team within the current organisation."""
    try:
        team = await svc.create(
            db,
            organisation_id=payload.organisation_id,
            name=body.name,
            description=body.description,
            workspace_id=body.workspace_id,
            created_by_user_id=payload.user_id,
        )
    except TeamDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="team.created",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "team_id": str(team.id),
            "name": team.name,
            "workspace_id": str(team.workspace_id) if team.workspace_id else None,
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _team_to_response(team)


@router.get("", response_model=PaginatedTeamsResponse)
async def list_teams(
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_READ))],
    db: RawDB,
    svc: TeamSvc,
    workspace_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedTeamsResponse:
    """List teams for the current organisation."""
    teams, total = await svc.list_for_org(
        db,
        organisation_id=payload.organisation_id,
        workspace_id=workspace_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedTeamsResponse(
        items=[_team_to_response(t) for t in teams],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: uuid.UUID,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_READ))],
    db: RawDB,
    svc: TeamSvc,
) -> TeamResponse:
    """Get a single team."""
    try:
        team = await svc.get(db, team_id=team_id, organisation_id=payload.organisation_id)
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _team_to_response(team)


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_UPDATE))],
    db: RawDB,
    svc: TeamSvc,
) -> TeamResponse:
    """Update a team's name or description."""
    try:
        team = await svc.update(
            db,
            team_id=team_id,
            organisation_id=payload.organisation_id,
            name=body.name,
            description=body.description,
        )
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TeamDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="team.updated",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"team_id": str(team_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _team_to_response(team)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_DELETE))],
    db: RawDB,
    svc: TeamSvc,
) -> None:
    """Delete a team and all its memberships."""
    try:
        await svc.delete(db, team_id=team_id, organisation_id=payload.organisation_id)
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="team.deleted",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"team_id": str(team_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Team membership sub-resource
# ---------------------------------------------------------------------------


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberAddRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_MEMBER_MANAGE))],
    db: RawDB,
    svc: TeamSvc,
) -> TeamMemberResponse:
    """Add a user to a team."""
    try:
        team_membership = await svc.add_member(
            db,
            team_id=team_id,
            user_id=body.user_id,
            organisation_id=payload.organisation_id,
        )
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TeamMemberNotOrgMemberError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except TeamMemberAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="team.member_added",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"team_id": str(team_id), "user_id": str(body.user_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _team_member_to_response(team_membership)


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(
    team_id: uuid.UUID,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_READ))],
    db: RawDB,
    svc: TeamSvc,
) -> list[TeamMemberResponse]:
    """List members of a team."""
    try:
        members = await svc.list_members(
            db, team_id=team_id, organisation_id=payload.organisation_id
        )
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [_team_member_to_response(m) for m in members]


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.TEAM_MEMBER_MANAGE))],
    db: RawDB,
    svc: TeamSvc,
) -> None:
    """Remove a user from a team."""
    try:
        await svc.remove_member(
            db,
            team_id=team_id,
            user_id=user_id,
            organisation_id=payload.organisation_id,
        )
    except TeamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TeamMemberNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="team.member_removed",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"team_id": str(team_id), "user_id": str(user_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
