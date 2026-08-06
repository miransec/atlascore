"""Workspace and workspace membership schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class WorkspaceCreateRequest(BaseModel):
    slug: Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")]
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(max_length=1000)] = None


class WorkspaceUpdateRequest(BaseModel):
    display_name: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    description: Annotated[str | None, Field(max_length=1000)] = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    slug: str
    display_name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceMembershipResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    workspace_role: str
    created_at: datetime


class AddWorkspaceMemberRequest(BaseModel):
    user_id: uuid.UUID
    workspace_role: str

    @field_validator("workspace_role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        from app.auth.permissions import is_valid_workspace_role

        if not is_valid_workspace_role(v):
            raise ValueError(f"Invalid workspace role: {v!r}")
        return v


class UpdateWorkspaceMemberRoleRequest(BaseModel):
    workspace_role: str

    @field_validator("workspace_role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        from app.auth.permissions import is_valid_workspace_role

        if not is_valid_workspace_role(v):
            raise ValueError(f"Invalid workspace role: {v!r}")
        return v
