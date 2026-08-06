"""Organisation and membership service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.membership import OrganisationMembership
from app.db.models.organisation import Organisation
from app.db.models.user import User
from app.services.audit import AuditService


class OrgServiceError(Exception):
    """Domain error from organisation operations."""


class OrgService:
    """Business logic for organisation management."""

    @staticmethod
    async def get_organisation(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
    ) -> Organisation | None:
        """Fetch an organisation by ID (RLS applied by session)."""
        result = await session.execute(
            select(Organisation).where(Organisation.id == organisation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_organisation(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        display_name: str | None = None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> Organisation:
        """Update organisation display name."""
        result = await session.execute(
            select(Organisation).where(Organisation.id == organisation_id)
        )
        org = result.scalar_one_or_none()
        if org is None:
            raise OrgServiceError("Organisation not found.")

        if display_name is not None:
            org.display_name = display_name

        AuditService.emit_transactional(
            session,
            event_type="org.updated",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"display_name": display_name},
            request_id=request_id,
            client_ip=client_ip,
        )
        await session.flush()
        return org

    @staticmethod
    async def list_members(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
    ) -> list[tuple[OrganisationMembership, User]]:
        """List all members of an organisation with their user records."""
        result = await session.execute(
            select(OrganisationMembership, User)
            .join(User, OrganisationMembership.user_id == User.id)
            .where(OrganisationMembership.organisation_id == organisation_id)
            .order_by(OrganisationMembership.created_at)
        )
        return list(result.tuples().all())

    @staticmethod
    async def add_member(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        org_role: str | None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> OrganisationMembership:
        """Add a user to an organisation."""
        # Check user exists
        user_check = await session.execute(select(User.id).where(User.id == user_id))
        if user_check.scalar_one_or_none() is None:
            raise OrgServiceError("User not found.")

        # Check not already a member
        existing = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise OrgServiceError("User is already a member of this organisation.")

        membership = OrganisationMembership(
            organisation_id=organisation_id,
            user_id=user_id,
            org_role=org_role,
        )
        session.add(membership)
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="org.membership_added",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"target_user_id": str(user_id), "org_role": org_role},
            request_id=request_id,
            client_ip=client_ip,
        )
        return membership

    @staticmethod
    async def remove_member(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Remove a user from an organisation."""
        result = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise OrgServiceError("User is not a member of this organisation.")

        if membership.org_role == "owner":
            raise OrgServiceError("Cannot remove the owner. Transfer ownership first.")

        await session.delete(membership)
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="org.membership_removed",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"target_user_id": str(user_id)},
            request_id=request_id,
            client_ip=client_ip,
        )

    @staticmethod
    async def change_member_role(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID,
        new_role: str | None,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> OrganisationMembership:
        """Change an org member's role."""
        result = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise OrgServiceError("User is not a member of this organisation.")

        if membership.org_role == "owner":
            raise OrgServiceError(
                "Cannot change the owner's role directly. Use transfer-ownership."
            )

        old_role = membership.org_role
        membership.org_role = new_role
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="org.role_changed",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={
                "target_user_id": str(user_id),
                "old_role": old_role,
                "new_role": new_role,
            },
            request_id=request_id,
            client_ip=client_ip,
        )
        return membership

    @staticmethod
    async def transfer_ownership(
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        current_owner_id: uuid.UUID,
        new_owner_id: uuid.UUID,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> None:
        """
        Transfer organisation ownership.

        Within a single transaction:
        1. Downgrade current owner to 'administrator'.
        2. Upgrade new owner to 'owner'.

        The DEFERRABLE INITIALLY DEFERRED trigger enforces exactly-one-owner
        at transaction commit.  During the transaction, there may temporarily
        be zero or two owners — this is safe because the trigger defers.
        """
        if current_owner_id == new_owner_id:
            raise OrgServiceError("New owner must be a different user.")

        # Verify current owner
        owner_result = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == current_owner_id,
                OrganisationMembership.org_role == "owner",
            )
        )
        owner_mem = owner_result.scalar_one_or_none()
        if owner_mem is None:
            raise OrgServiceError("Current user is not the owner.")

        # Verify new owner is a member
        new_owner_result = await session.execute(
            select(OrganisationMembership).where(
                OrganisationMembership.organisation_id == organisation_id,
                OrganisationMembership.user_id == new_owner_id,
            )
        )
        new_owner_mem = new_owner_result.scalar_one_or_none()
        if new_owner_mem is None:
            raise OrgServiceError("New owner must be a member of this organisation.")

        # Swap roles in deterministic order. The deferred trigger permits the
        # temporary zero-owner state, while the partial unique index requires
        # the old owner to be demoted before the new owner is promoted.
        owner_mem.org_role = "administrator"
        await session.flush()
        new_owner_mem.org_role = "owner"
        await session.flush()

        AuditService.emit_transactional(
            session,
            event_type="org.ownership_transferred",
            organisation_id=organisation_id,
            actor_user_id=current_owner_id,
            event_data={
                "from_user_id": str(current_owner_id),
                "to_user_id": str(new_owner_id),
            },
            request_id=request_id,
            client_ip=client_ip,
        )
