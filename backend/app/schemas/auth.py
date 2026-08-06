"""Auth request/response schemas."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: Annotated[str, EmailStr]
    password: Annotated[str, Field(min_length=12, max_length=128)]
    full_name: Annotated[str, Field(min_length=1, max_length=255)]
    organisation_name: Annotated[str, Field(min_length=1, max_length=255)]
    organisation_slug: Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")]

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        return v


class LoginRequest(BaseModel):
    """Login step 1: credential verification."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()


class OrganisationSummary(BaseModel):
    """Minimal org info returned in login step 1 response."""

    id: uuid.UUID
    slug: str
    display_name: str
    org_role: str | None


class LoginStep1Response(BaseModel):
    """
    Response to login step 1.

    Does NOT include JWT or refresh token — those come after org selection.
    The pre-auth session cookie is set separately in the HTTP response.
    """

    organisations: list[OrganisationSummary]
    message: str = "Select an organisation to continue."


class SelectOrganisationRequest(BaseModel):
    """
    Login step 2: organisation selection.

    Contains ONLY organisation_id.  user_id is NEVER accepted here —
    it is derived from the pre-auth session server-side.
    """

    organisation_id: uuid.UUID


class TokenResponse(BaseModel):
    """Successful auth response after org selection or token refresh."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(BaseModel):
    """Current user and org context."""

    user_id: uuid.UUID
    email: str
    full_name: str
    organisation_id: uuid.UUID
    organisation_slug: str
    org_role: str | None
    workspace_id: uuid.UUID | None = None
    is_platform_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: Annotated[str, Field(min_length=12, max_length=128)]

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        return v
