"""Team and TeamMembership request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class TeamCreateRequest(BaseModel):
    name: str
    description: str | None = None
    workspace_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Team name cannot be blank.")
        if len(v) > 128:
            raise ValueError("Team name must be ≤128 characters.")
        return v


class TeamUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Team name cannot be blank.")
            if len(v) > 128:
                raise ValueError("Team name must be ≤128 characters.")
        return v


class TeamResponse(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID | None
    name: str
    description: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamMemberAddRequest(BaseModel):
    user_id: uuid.UUID


class TeamMemberResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    organisation_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedTeamsResponse(BaseModel):
    items: list[TeamResponse]
    total: int
    page: int
    page_size: int
