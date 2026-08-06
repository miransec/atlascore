"""ServiceAccount and ApiKey request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.auth.permissions import Permission


class ServiceAccountCreateRequest(BaseModel):
    name: str
    description: str | None = None
    workspace_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Service account name cannot be blank.")
        if len(v) > 128:
            raise ValueError("Name must be ≤128 characters.")
        return v


class ServiceAccountUpdateRequest(BaseModel):
    description: str | None = None


class ServiceAccountResponse(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID | None
    name: str
    description: str | None
    is_active: bool
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

_VALID_SCOPES = {p.value for p in Permission}


class ApiKeyCreateRequest(BaseModel):
    name: str
    scopes: list[str]
    expires_in_days: int | None = None  # None = no expiration

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("API key name cannot be blank.")
        return v

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one scope is required.")
        invalid = set(v) - _VALID_SCOPES
        if invalid:
            raise ValueError(f"Invalid scopes: {sorted(invalid)}")
        return sorted(set(v))  # deduplicate, sort for stable storage

    @field_validator("expires_in_days")
    @classmethod
    def validate_expiry(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 3650):
            raise ValueError("expires_in_days must be between 1 and 3650.")
        return v


class ApiKeyResponse(BaseModel):
    """Safe representation — never includes secret_hash or raw key."""

    id: uuid.UUID
    service_account_id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID | None
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(BaseModel):
    """
    Response returned EXACTLY ONCE when an API key is created.

    raw_key is shown once and cannot be retrieved again.
    The caller must copy it immediately.
    """

    api_key: ApiKeyResponse
    raw_key: str
    warning: str = (
        "This API key will NOT be shown again. "
        "Copy it now and store it securely. "
        "It cannot be retrieved after this response."
    )


class ApiKeyRevokeRequest(BaseModel):
    reason: str | None = None


class PaginatedServiceAccountsResponse(BaseModel):
    items: list[ServiceAccountResponse]
    total: int
    page: int
    page_size: int


class PaginatedApiKeysResponse(BaseModel):
    items: list[ApiKeyResponse]
    total: int
    page: int
    page_size: int
