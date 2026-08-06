"""Organisation and membership schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class OrganisationResponse(BaseModel):
    id: uuid.UUID
    slug: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class OrganisationUpdateRequest(BaseModel):
    display_name: Annotated[str | None, Field(min_length=1, max_length=255)] = None


class MembershipResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    org_role: str | None
    created_at: datetime


class InviteMemberRequest(BaseModel):
    user_id: uuid.UUID
    org_role: str | None = None

    @field_validator("org_role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from app.auth.permissions import is_valid_org_role

        if not is_valid_org_role(v):
            raise ValueError(f"Invalid org role: {v!r}")
        return v


class UpdateMemberRoleRequest(BaseModel):
    org_role: str | None

    @field_validator("org_role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from app.auth.permissions import is_valid_org_role

        if not is_valid_org_role(v):
            raise ValueError(f"Invalid org role: {v!r}")
        return v


class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: uuid.UUID
