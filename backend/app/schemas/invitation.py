"""Invitation request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator

from app.auth.permissions import OrgRole, WorkspaceRole


class InvitationCreateRequest(BaseModel):
    """Create a new invitation. Role is encoded server-side and cannot be escalated."""

    invited_email: EmailStr
    org_role: str | None = None
    workspace_id: uuid.UUID | None = None
    workspace_role: str | None = None
    # Expiry in hours; defaults to 72 hours (3 days).
    expires_in_hours: int = 72

    @field_validator("org_role")
    @classmethod
    def validate_org_role(cls, v: str | None) -> str | None:
        if v is not None and v not in {r.value for r in OrgRole}:
            raise ValueError(f"Invalid org_role: {v!r}")
        # Owner cannot be invited — ownership must be transferred explicitly.
        if v == OrgRole.OWNER:
            raise ValueError("Cannot invite a user as owner. Use ownership transfer.")
        return v

    @field_validator("workspace_role")
    @classmethod
    def validate_workspace_role(cls, v: str | None) -> str | None:
        if v is not None and v not in {r.value for r in WorkspaceRole}:
            raise ValueError(f"Invalid workspace_role: {v!r}")
        return v

    @field_validator("expires_in_hours")
    @classmethod
    def validate_expires(cls, v: int) -> int:
        if v < 1 or v > 168:  # 1 hour to 7 days
            raise ValueError("expires_in_hours must be between 1 and 168.")
        return v


class InvitationAcceptRequest(BaseModel):
    """Accept an invitation. The raw token is presented here; hash is verified server-side."""

    token: str


class InvitationRevokeRequest(BaseModel):
    """Revoke an invitation by ID."""

    reason: str | None = None


class InvitationResponse(BaseModel):
    """Public invitation details — never includes token_hash."""

    id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID | None
    invited_email: str
    org_role: str | None
    workspace_role: str | None
    created_by_user_id: uuid.UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class InvitationCreatedResponse(BaseModel):
    """
    Response returned when an invitation is created.

    Includes the raw_token exactly once — the caller must deliver this
    to the invitee.  It is not stored and cannot be retrieved again.
    This is clearly labelled as a development/demo mechanism.
    """

    invitation: InvitationResponse
    # Raw token — shown ONCE. In production, deliver via email; in development, shown here.
    raw_token: str
    delivery_note: str = (
        "DEVELOPMENT MODE: The raw invitation token is included in this response. "
        "In production, deliver this token via email to the invitee. "
        "It will not be shown again."
    )
