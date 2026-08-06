"""Team service — create, update, delete teams and manage memberships."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership
from app.db.models.team import Team, TeamMembership


class TeamError(Exception):
    """Base class for team service errors."""


class TeamNotFoundError(TeamError):
    pass


class TeamDuplicateError(TeamError):
    pass


class TeamMemberNotFoundError(TeamError):
    pass


class TeamMemberAlreadyExistsError(TeamError):
    pass


class TeamMemberNotOrgMemberError(TeamError):
    pass


class TeamService:
    # -------------------------------------------------------------------------
    # Teams
    # -------------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        name: str,
        description: str | None = None,
        workspace_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID,
    ) -> Team:
        duplicate_query = select(Team.id).where(
            Team.organisation_id == organisation_id,
            Team.name == name,
        )
        if workspace_id is None:
            duplicate_query = duplicate_query.where(Team.workspace_id.is_(None))
        else:
            duplicate_query = duplicate_query.where(Team.workspace_id == workspace_id)
        if (await session.execute(duplicate_query)).scalar_one_or_none() is not None:
            raise TeamDuplicateError(f"A team named {name!r} already exists in this scope.")

        team = Team(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            name=name,
            description=description,
            created_by_user_id=created_by_user_id,
        )
        session.add(team)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if "uq_teams_org_workspace_name" in str(exc):
                raise TeamDuplicateError(
                    f"A team named {name!r} already exists in this scope."
                ) from exc
            raise
        return team

    async def get(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> Team:
        result = await session.execute(
            select(Team).where(
                Team.id == team_id,
                Team.organisation_id == organisation_id,
            )
        )
        team = result.scalar_one_or_none()
        if team is None:
            raise TeamNotFoundError(f"Team {team_id} not found.")
        return team

    async def update(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        organisation_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> Team:
        team = await self.get(session, team_id=team_id, organisation_id=organisation_id)
        if name is not None:
            team.name = name
        if description is not None:
            team.description = description
        team.updated_at = datetime.now(tz=UTC)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if "uq_teams_org_workspace_name" in str(exc):
                raise TeamDuplicateError(
                    f"A team named {name!r} already exists in this scope."
                ) from exc
            raise
        return team

    async def delete(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> None:
        """Delete a team and all its memberships (CASCADE)."""
        team = await self.get(session, team_id=team_id, organisation_id=organisation_id)
        await session.delete(team)
        await session.flush()

    async def list_for_org(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Team], int]:
        base_query = select(Team).where(Team.organisation_id == organisation_id)
        if workspace_id is not None:
            base_query = base_query.where(Team.workspace_id == workspace_id)

        count_result = await session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        items_result = await session.execute(
            base_query.order_by(Team.name).offset((page - 1) * page_size).limit(page_size)
        )
        return list(items_result.scalars()), total

    # -------------------------------------------------------------------------
    # Team memberships
    # -------------------------------------------------------------------------

    async def add_member(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> TeamMembership:
        """
        Add a user to a team.

        Cross-tenant protection:
        - team must belong to organisation_id (verified by get()).
        - user must be an org member (service layer check).
        """
        # Verify team belongs to this org (raises if not found or wrong org)
        await self.get(session, team_id=team_id, organisation_id=organisation_id)

        # Verify user is an org member
        org_member = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        if org_member.scalar_one_or_none() is None:
            raise TeamMemberNotOrgMemberError(
                "User is not a member of this organisation and cannot be added to a team."
            )

        # Check for duplicate
        existing = await session.execute(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise TeamMemberAlreadyExistsError("User is already a member of this team.")

        membership = TeamMembership(
            id=uuid.uuid4(),
            team_id=team_id,
            user_id=user_id,
            organisation_id=organisation_id,
        )
        session.add(membership)
        await session.flush()
        return membership

    async def remove_member(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> None:
        # Verify team belongs to this org
        await self.get(session, team_id=team_id, organisation_id=organisation_id)

        result = await session.execute(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise TeamMemberNotFoundError("User is not a member of this team.")
        await session.delete(membership)
        await session.flush()

    async def list_members(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        organisation_id: uuid.UUID,
    ) -> list[TeamMembership]:
        # Verify team belongs to this org
        await self.get(session, team_id=team_id, organisation_id=organisation_id)

        result = await session.execute(
            select(TeamMembership)
            .where(TeamMembership.team_id == team_id)
            .order_by(TeamMembership.created_at)
        )
        return list(result.scalars())
