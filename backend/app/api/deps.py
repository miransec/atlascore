"""
FastAPI dependency functions for authentication and authorisation.

SECURITY:
- Org membership is re-verified against the live database on every
  authenticated request (not just the JWT claim).
- Tokens that are syntactically valid but whose org membership has been
  revoked are rejected with 401 on the NEXT request (not at token expiry).
- user_id and organisation_id are sourced from the verified JWT — never
  from request data.
- Workspace context for knowledge endpoints is validated through
  get_validated_workspace_context: the URL path workspace_id must match
  the JWT workspace_id claim AND have an active WorkspaceMembership row.
  The PostgreSQL workspace GUC is never set from an unvalidated path param.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.csrf import CSRFService
from app.auth.permissions import OrgRole, Permission, has_org_permission
from app.auth.tokens import JWTService, TokenPayload
from app.core.config import Settings, get_settings
from app.db.engine import get_raw_session
from app.db.models.membership import OrganisationMembership, WorkspaceMembership

# ---------------------------------------------------------------------------
# Application-level singletons (created once, injected via Depends)
# ---------------------------------------------------------------------------


def get_settings_dep() -> Settings:
    return get_settings()


def get_jwt_service(settings: Annotated[Settings, Depends(get_settings_dep)]) -> JWTService:
    return JWTService(settings)


def get_csrf_service(settings: Annotated[Settings, Depends(get_settings_dep)]) -> CSRFService:
    return CSRFService(settings)


# ---------------------------------------------------------------------------
# Session factory dependency
# ---------------------------------------------------------------------------

_engine_cache: dict[str, object] = {}


def get_session_factory(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> async_sessionmaker[AsyncSession]:
    """Return the session factory (or create it on first call)."""
    key = settings.DATABASE_URL
    if key not in _engine_cache:
        from app.db.engine import build_engine, build_session_factory, register_pool_events

        engine = build_engine(settings)
        register_pool_events(engine)
        _engine_cache[key] = build_session_factory(engine)
    return _engine_cache[key]  # type: ignore[return-value]


async def get_raw_db(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw (unscoped) database session."""
    async for session in get_raw_session(factory):
        yield session


# ---------------------------------------------------------------------------
# Token extraction and verification
# ---------------------------------------------------------------------------


def _extract_bearer_token(authorization: str | None) -> str:
    """Extract the raw JWT from Authorization: Bearer <token>."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format.",
        )
    return parts[1]


async def get_token_payload(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> TokenPayload:
    """
    Verify the access token and return its payload.

    Does NOT re-verify org membership — use get_current_membership for that.
    """
    raw_token = _extract_bearer_token(authorization)
    try:
        return jwt_service.verify(raw_token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired.",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
        ) from exc


async def get_current_membership(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_raw_db)],
) -> OrganisationMembership:
    """
    Re-verify live org membership on every authenticated request.

    The JWT claim is the starting point; the database is the source of truth.
    A revoked membership takes effect immediately, not at token expiry.

    Workspace context (additional live check):
    If the JWT carries a workspace_id claim, WorkspaceMembership is also
    re-verified against the live database.  Removing a WorkspaceMembership
    row takes effect on the NEXT request — the workspace-scoped JWT does not
    remain valid until expiry.  This prevents stale workspace access after
    a membership is revoked.
    """
    # Bootstrap the transaction-local FORCE-RLS context from the
    # already-verified JWT before reading tenant-scoped membership rows.
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
        {"org_id": str(payload.organisation_id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": str(payload.user_id)},
    )

    result = await db.execute(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == payload.user_id,
            OrganisationMembership.organisation_id == payload.organisation_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organisation membership not found or revoked.",
        )

    # If the JWT is workspace-scoped, verify WorkspaceMembership is still active.
    # This is not redundant with the org check — a user can be removed from a
    # workspace while retaining org membership.  The workspace claims in the JWT
    # must not be trusted past the point where WorkspaceMembership is revoked.
    if payload.workspace_id is not None:
        ws_result = await db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == payload.workspace_id,
                WorkspaceMembership.user_id == payload.user_id,
                WorkspaceMembership.organisation_id == payload.organisation_id,
            )
        )
        ws_membership = ws_result.scalar_one_or_none()
        if ws_membership is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace membership not found or revoked.",
            )

    return membership


async def get_validated_workspace_context(
    workspace_id: uuid.UUID,
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_raw_db)],
) -> uuid.UUID:
    """
    Validate that a URL path workspace_id is safe to use as a PostgreSQL GUC.

    Three checks — ALL must pass:

    1. The JWT must carry a workspace_id claim.  A token without one has no
       workspace context and may not access workspace-scoped knowledge routes.

    2. The URL path workspace_id must equal the JWT workspace_id claim.
       This closes the IDOR gap: a W1-scoped token cannot be presented on a
       W2 URL to set app.current_workspace_id=W2 in the database session.

    3. A live WorkspaceMembership row must exist.  This re-verifies membership
       has not been revoked since the JWT was issued — symmetric with the org
       membership re-check in get_current_membership.

    The returned uuid.UUID is the validated, trusted workspace_id.  Callers
    MUST use this return value (not the raw path parameter) when constructing
    OrganisationScopedSession or passing workspace_id to service methods.

    TRUST CHAIN:
        URL path workspace_id (CLIENT-SUPPLIED)
            → path == JWT workspace_id  (step 2)
            → live WorkspaceMembership  (step 3)
            → trusted workspace_id      (return value)
            → OrganisationScopedSession (sets app.current_workspace_id GUC)
            → PostgreSQL RLS            (filters rows by workspace_id)
    """
    # Step 1: JWT must carry a workspace claim.
    if payload.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token carries no workspace context.  "
            "Switch to a workspace before accessing knowledge endpoints.",
        )

    # Step 2: Path workspace must match JWT workspace — no cross-workspace access.
    if workspace_id != payload.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requested workspace does not match token workspace.",
        )

    # Step 3: Live WorkspaceMembership check — revocation takes effect immediately.
    ws_result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == payload.user_id,
            WorkspaceMembership.organisation_id == payload.organisation_id,
        )
    )
    if ws_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace membership not found or revoked.",
        )

    return workspace_id


class RequirePermission:
    """
    Dependency that enforces a specific permission.

    Usage:
        @router.post("/workspaces")
        async def create_workspace(
            _: Annotated[None, Depends(RequirePermission(Permission.WORKSPACE_CREATE))],
            payload: Annotated[TokenPayload, Depends(get_token_payload)],
            membership: Annotated[OrganisationMembership, Depends(get_current_membership)],
        ) -> ...:
    """

    def __init__(self, permission: Permission) -> None:
        self._permission = permission

    async def __call__(
        self,
        membership: Annotated[OrganisationMembership, Depends(get_current_membership)],
    ) -> None:
        role: OrgRole | None = OrgRole(membership.org_role) if membership.org_role else None
        if not has_org_permission(role, self._permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {self._permission.value}",
            )


# ---------------------------------------------------------------------------
# CSRF verification dependency
# ---------------------------------------------------------------------------


class RequireCSRF:
    """
    Dependency that verifies the CSRF double-submit token on state-changing
    requests.

    Must be used on all POST/PUT/PATCH/DELETE endpoints that use refresh
    cookie authentication.
    """

    async def __call__(
        self,
        request: Request,
        payload: Annotated[TokenPayload, Depends(get_token_payload)],
        csrf_service: Annotated[CSRFService, Depends(get_csrf_service)],
        settings: Annotated[Settings, Depends(get_settings_dep)],
    ) -> None:
        # Validate Origin header
        if not csrf_service.validate_origin(request, settings.allowed_origins_list):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Origin not allowed.",
            )

        # Validate CSRF token — bound to the refresh token family (fid), not jti.
        # jti is now unique per access token; fid is stable across the login session.
        if not csrf_service.verify(request, payload.family_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token missing or invalid.",
            )


# ---------------------------------------------------------------------------
# Convenience type aliases for injection
# ---------------------------------------------------------------------------
CurrentMembership = Annotated[OrganisationMembership, Depends(get_current_membership)]
CurrentPayload = Annotated[TokenPayload, Depends(get_token_payload)]
RawDB = Annotated[AsyncSession, Depends(get_raw_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]

# ValidatedWorkspaceId: URL path workspace_id validated against JWT claim +
# live WorkspaceMembership.  Use on every knowledge endpoint that takes a
# workspace_id path parameter.  Never pass the raw path parameter to
# OrganisationScopedSession or service methods — use this alias instead.
ValidatedWorkspaceId = Annotated[uuid.UUID, Depends(get_validated_workspace_context)]
