"""Service account and API key endpoints — /api/v1/service-accounts."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import AppSettings, CurrentPayload, RawDB, RequirePermission
from app.auth.permissions import Permission
from app.schemas.service_account import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiKeyRevokeRequest,
    PaginatedApiKeysResponse,
    PaginatedServiceAccountsResponse,
    ServiceAccountCreateRequest,
    ServiceAccountResponse,
    ServiceAccountUpdateRequest,
)
from app.services.audit import AuditService
from app.services.service_account_service import (
    ApiKeyNotFoundError,
    ApiKeyRevokedError,
    ServiceAccountDisabledError,
    ServiceAccountDuplicateError,
    ServiceAccountNotFoundError,
    ServiceAccountService,
)

router = APIRouter(prefix="/service-accounts", tags=["service-accounts"])


def _get_sa_service(settings: AppSettings) -> ServiceAccountService:
    return ServiceAccountService(settings)


SASvc = Annotated[ServiceAccountService, Depends(_get_sa_service)]


def _sa_to_response(sa: object) -> ServiceAccountResponse:
    from app.db.models.service_account import ServiceAccount

    assert isinstance(sa, ServiceAccount)
    return ServiceAccountResponse(
        id=sa.id,
        organisation_id=sa.organisation_id,
        workspace_id=sa.workspace_id,
        name=sa.name,
        description=sa.description,
        is_active=sa.is_active,
        created_by_user_id=sa.created_by_user_id,
        created_at=sa.created_at,
        updated_at=sa.updated_at,
        disabled_at=sa.disabled_at,
        last_used_at=sa.last_used_at,
    )


def _api_key_to_response(key: object) -> ApiKeyResponse:
    from app.db.models.service_account import ApiKey

    assert isinstance(key, ApiKey)
    return ApiKeyResponse(
        id=key.id,
        service_account_id=key.service_account_id,
        organisation_id=key.organisation_id,
        workspace_id=key.workspace_id,
        name=key.name,
        key_prefix=key.key_prefix,
        scopes=key.scopes,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        is_active=key.is_active,
    )


# ---------------------------------------------------------------------------
# Service account CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ServiceAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_service_account(
    body: ServiceAccountCreateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_CREATE))],
    db: RawDB,
    svc: SASvc,
) -> ServiceAccountResponse:
    """Create a service account for non-human API access."""
    try:
        sa = await svc.create_service_account(
            db,
            organisation_id=payload.organisation_id,
            name=body.name,
            description=body.description,
            workspace_id=body.workspace_id,
            created_by_user_id=payload.user_id,
        )
    except ServiceAccountDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="service_account.created",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "service_account_id": str(sa.id),
            "name": sa.name,
            "workspace_id": str(sa.workspace_id) if sa.workspace_id else None,
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _sa_to_response(sa)


@router.get("", response_model=PaginatedServiceAccountsResponse)
async def list_service_accounts(
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_READ))],
    db: RawDB,
    svc: SASvc,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedServiceAccountsResponse:
    """List service accounts for the current organisation."""
    accounts, total = await svc.list_service_accounts(
        db,
        organisation_id=payload.organisation_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedServiceAccountsResponse(
        items=[_sa_to_response(a) for a in accounts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{sa_id}", response_model=ServiceAccountResponse)
async def get_service_account(
    sa_id: uuid.UUID,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_READ))],
    db: RawDB,
    svc: SASvc,
) -> ServiceAccountResponse:
    """Get a single service account."""
    try:
        sa = await svc.get_service_account(db, sa_id=sa_id, organisation_id=payload.organisation_id)
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _sa_to_response(sa)


@router.patch("/{sa_id}", response_model=ServiceAccountResponse)
async def update_service_account(
    sa_id: uuid.UUID,
    body: ServiceAccountUpdateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_MANAGE))],
    db: RawDB,
    svc: SASvc,
) -> ServiceAccountResponse:
    """Update service account description."""
    try:
        sa = await svc.get_service_account(db, sa_id=sa_id, organisation_id=payload.organisation_id)
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if body.description is not None:
        sa.description = body.description
        from datetime import datetime

        sa.updated_at = datetime.now(tz=UTC)
        await db.flush()

    AuditService.emit_transactional(
        db,
        event_type="service_account.updated",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"service_account_id": str(sa_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _sa_to_response(sa)


@router.post("/{sa_id}/disable", response_model=ServiceAccountResponse)
async def disable_service_account(
    sa_id: uuid.UUID,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_MANAGE))],
    db: RawDB,
    svc: SASvc,
) -> ServiceAccountResponse:
    """Disable a service account (all its keys stop working immediately)."""
    try:
        sa = await svc.disable_service_account(
            db, sa_id=sa_id, organisation_id=payload.organisation_id
        )
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="service_account.disabled",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"service_account_id": str(sa_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _sa_to_response(sa)


@router.post("/{sa_id}/enable", response_model=ServiceAccountResponse)
async def enable_service_account(
    sa_id: uuid.UUID,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.SERVICE_ACCOUNT_MANAGE))],
    db: RawDB,
    svc: SASvc,
) -> ServiceAccountResponse:
    """Re-enable a disabled service account."""
    try:
        sa = await svc.enable_service_account(
            db, sa_id=sa_id, organisation_id=payload.organisation_id
        )
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="service_account.enabled",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={"service_account_id": str(sa_id)},
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _sa_to_response(sa)


# ---------------------------------------------------------------------------
# API key sub-resource
# ---------------------------------------------------------------------------


@router.post(
    "/{sa_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    sa_id: uuid.UUID,
    body: ApiKeyCreateRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.API_KEY_CREATE))],
    db: RawDB,
    svc: SASvc,
) -> ApiKeyCreatedResponse:
    """
    Create an API key for a service account.

    The raw key is returned EXACTLY ONCE. Store it securely — it cannot be
    retrieved after this response.
    """
    try:
        api_key, raw_key = await svc.create_api_key(
            db,
            service_account_id=sa_id,
            organisation_id=payload.organisation_id,
            name=body.name,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
        )
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ServiceAccountDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="api_key.created",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "api_key_id": str(api_key.id),
            "service_account_id": str(sa_id),
            "key_prefix": api_key.key_prefix,
            "scopes": api_key.scopes,
            # raw_key is NEVER logged — only the prefix
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()

    return ApiKeyCreatedResponse(
        api_key=_api_key_to_response(api_key),
        raw_key=raw_key,
    )


@router.get("/{sa_id}/api-keys", response_model=PaginatedApiKeysResponse)
async def list_api_keys(
    sa_id: uuid.UUID,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.API_KEY_LIST))],
    db: RawDB,
    svc: SASvc,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedApiKeysResponse:
    """List API keys for a service account."""
    try:
        # Verify SA exists and belongs to this org
        await svc.get_service_account(db, sa_id=sa_id, organisation_id=payload.organisation_id)
    except ServiceAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    keys, total = await svc.list_api_keys(
        db,
        service_account_id=sa_id,
        organisation_id=payload.organisation_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedApiKeysResponse(
        items=[_api_key_to_response(k) for k in keys],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{sa_id}/api-keys/{key_id}/revoke", response_model=ApiKeyResponse)
async def revoke_api_key(
    sa_id: uuid.UUID,
    key_id: uuid.UUID,
    body: ApiKeyRevokeRequest,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.API_KEY_REVOKE))],
    db: RawDB,
    svc: SASvc,
) -> ApiKeyResponse:
    """Revoke an API key immediately."""
    try:
        key = await svc.revoke_api_key(db, key_id=key_id, organisation_id=payload.organisation_id)
    except ApiKeyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ApiKeyRevokedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="api_key.revoked",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "api_key_id": str(key_id),
            "service_account_id": str(sa_id),
            "reason": body.reason,
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()
    return _api_key_to_response(key)


@router.post("/{sa_id}/api-keys/{key_id}/rotate", response_model=ApiKeyCreatedResponse)
async def rotate_api_key(
    sa_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    payload: CurrentPayload,
    membership: Annotated[None, Depends(RequirePermission(Permission.API_KEY_CREATE))],
    db: RawDB,
    svc: SASvc,
) -> ApiKeyCreatedResponse:
    """
    Rotate an API key.

    Revokes the old key and issues a new one with the same scopes/name/expiry.
    The new raw key is returned EXACTLY ONCE.
    """
    try:
        new_key, raw_key = await svc.rotate_api_key(
            db, key_id=key_id, organisation_id=payload.organisation_id
        )
    except ApiKeyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ApiKeyRevokedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    AuditService.emit_transactional(
        db,
        event_type="api_key.rotated",
        organisation_id=payload.organisation_id,
        actor_user_id=payload.user_id,
        event_data={
            "old_key_id": str(key_id),
            "new_key_id": str(new_key.id),
            "service_account_id": str(sa_id),
            "new_key_prefix": new_key.key_prefix,
            # raw_key is NEVER logged
        },
        request_id=request.headers.get("X-Request-ID"),
    )
    await db.commit()

    return ApiKeyCreatedResponse(
        api_key=_api_key_to_response(new_key),
        raw_key=raw_key,
    )
