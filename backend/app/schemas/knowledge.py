"""Pydantic schemas for the Phase 2A knowledge API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Knowledge Sources
# ---------------------------------------------------------------------------


class KnowledgeSourceCreateRequest(BaseModel):
    source_type: Annotated[str, Field(min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(max_length=2000)] = None
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        allowed = {"manual_upload"}
        if v not in allowed:
            raise ValueError(f"source_type must be one of: {sorted(allowed)}")
        return v


class KnowledgeSourceUpdateRequest(BaseModel):
    display_name: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    configuration: dict[str, Any] | None = None
    is_active: bool | None = None


class KnowledgeSourceResponse(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID
    source_type: str
    display_name: str
    description: str | None
    is_active: bool
    configuration: dict[str, Any]
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Knowledge Documents
# ---------------------------------------------------------------------------


class KnowledgeDocumentResponse(BaseModel):
    id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID
    source_id: uuid.UUID
    original_filename: str
    media_type: str
    is_archived: bool
    archived_at: datetime | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Document Versions
# ---------------------------------------------------------------------------


class KnowledgeDocumentVersionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID
    version_number: int
    content_sha256: str
    size_bytes: int
    media_type: str
    # storage_key is NEVER returned to clients.
    created_by_user_id: uuid.UUID | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Ingestion Jobs
# ---------------------------------------------------------------------------


class KnowledgeIngestionJobResponse(BaseModel):
    id: uuid.UUID
    version_id: uuid.UUID
    document_id: uuid.UUID
    organisation_id: uuid.UUID
    workspace_id: uuid.UUID
    status: str
    idempotency_key: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    result_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Upload response — wraps document + version + job together
# ---------------------------------------------------------------------------


class KnowledgeUploadResponse(BaseModel):
    document: KnowledgeDocumentResponse
    version: KnowledgeDocumentVersionResponse
    ingestion_job: KnowledgeIngestionJobResponse
