"""
Authentication endpoints.

Step 1: POST /auth/login          — verify credentials, return org list + pre-auth cookie
Step 2: POST /auth/select-org     — consume pre-auth, select org, issue JWT + refresh cookie
        POST /auth/refresh         — rotate refresh token
        POST /auth/logout          — revoke family, clear cookies
        POST /auth/logout-all      — revoke all families, clear cookies
        POST /auth/change-password — change password (requires current_password)
        GET  /auth/me              — current user + org context
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import text

from app.api.deps import (
    AppSettings,
    CurrentMembership,
    CurrentPayload,
    RawDB,
    get_csrf_service,
)
from app.auth.csrf import CSRFService
from app.auth.password import PasswordService
from app.auth.pre_auth import PreAuthSessionService
from app.auth.refresh import RefreshTokenReuseError, RefreshTokenService
from app.auth.tokens import JWTService
from app.core.config import Settings
from app.db.models.organisation import Organisation
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginStep1Response,
    MeResponse,
    OrganisationSummary,
    RegisterRequest,
    SelectOrganisationRequest,
    TokenResponse,
)
from app.services.auth_service import (
    AuthenticationError,
    AuthService,
    OrgSelectionError,
    RegistrationError,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_PRE_AUTH_COOKIE = "pre_auth_session"
_REFRESH_COOKIE = "refresh_token"
_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _build_auth_service(settings: Settings) -> AuthService:
    return AuthService(
        password_service=PasswordService(
            pepper=settings.ARGON2_PEPPER,
            pepper_version=settings.ARGON2_PEPPER_VERSION,
        ),
        jwt_service=JWTService(settings),
        refresh_service=RefreshTokenService(settings),
        pre_auth_service=PreAuthSessionService(settings),
    )


def _set_refresh_cookie(
    response: Response,
    raw_token: str,
    settings: Settings,
    max_age: int,
) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
        max_age=max_age,
    )


def _set_pre_auth_cookie(
    response: Response,
    raw_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=_PRE_AUTH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite="strict",
        path="/api/v1/auth/select-organisation",
        max_age=settings.PRE_AUTH_SESSION_EXPIRE_MINUTES * 60,
    )


def _clear_auth_cookies(response: Response, csrf_service: CSRFService) -> None:
    response.delete_cookie(key=_REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)
    response.delete_cookie(key=_PRE_AUTH_COOKIE, path="/api/v1/auth/select-organisation")
    csrf_service.clear_csrf_cookie(response)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    body: RegisterRequest,
    response: Response,
    db: RawDB,
    settings: AppSettings,
) -> dict[str, str]:
    """Register a new user and create their first organisation."""
    svc = _build_auth_service(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        _user, _org = await svc.register(
            db,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            organisation_name=body.organisation_name,
            organisation_slug=body.organisation_slug,
            client_ip=client_ip,
            request_id=request_id,
        )
    except RegistrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return {"message": "Registration successful. Please log in."}


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: RawDB,
    settings: AppSettings,
) -> LoginStep1Response:
    """Login step 1: verify credentials, return available organisations."""
    svc = _build_auth_service(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    try:
        _user, memberships, pre_auth_token = await svc.login_step1(
            db,
            email=body.email,
            password=body.password,
            client_ip=client_ip,
            user_agent=user_agent,
            request_id=request_id,
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    # Set pre-auth cookie
    _set_pre_auth_cookie(response, pre_auth_token, settings)

    # Load org slugs and display names
    # (In production this would be a joined query; simplified here for clarity)
    return LoginStep1Response(
        organisations=[
            OrganisationSummary(
                id=m.organisation_id,
                slug=str(m.organisation_id),  # placeholder — real impl joins orgs
                display_name="",
                org_role=m.org_role,
            )
            for m in memberships
        ]
    )


@router.post("/select-organisation")
async def select_organisation(
    request: Request,
    body: SelectOrganisationRequest,
    response: Response,
    db: RawDB,
    settings: AppSettings,
    pre_auth_session: Annotated[str | None, Cookie(alias=_PRE_AUTH_COOKIE)] = None,
) -> TokenResponse:
    """
    Login step 2: consume pre-auth session, issue JWT and refresh token.

    user_id is sourced from the server-side pre-auth session — never from
    the request body.
    """
    if pre_auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Pre-authentication session not found. Please log in.",
        )

    svc = _build_auth_service(settings)
    jwt_service = JWTService(settings)
    csrf_service = CSRFService(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        user, org, membership, raw_refresh, rt = await svc.select_organisation(
            db,
            pre_auth_raw_token=pre_auth_session,
            organisation_id=body.organisation_id,
            client_ip=client_ip,
            request_id=request_id,
        )
    except OrgSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    # Issue access token — jti generated internally; fid carries the refresh family
    access_token = jwt_service.issue(
        user_id=user.id,
        organisation_id=org.id,
        org_role=membership.org_role,
        family_id=str(rt.family_id),
    )

    # Set refresh cookie
    max_age = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
    _set_refresh_cookie(response, raw_refresh, settings, max_age)

    # Set CSRF cookie (JS-readable) — bound to family_id, stable across context switches
    csrf_service.set_csrf_cookie(response, str(rt.family_id))

    # Clear pre-auth cookie
    response.delete_cookie(
        key=_PRE_AUTH_COOKIE,
        path="/api/v1/auth/select-organisation",
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    db: RawDB,
    settings: AppSettings,
    refresh_token: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token."""
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found.",
        )

    svc = _build_auth_service(settings)
    jwt_service = JWTService(settings)
    csrf_service = CSRFService(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    try:
        result = await svc.refresh_tokens(
            db,
            raw_refresh_token=refresh_token,
            client_ip=client_ip,
            request_id=request_id,
        )
    except RefreshTokenReuseError as exc:
        _clear_auth_cookies(response, csrf_service)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalidated due to token reuse. Please log in again.",
        ) from exc

    if result is None:
        _clear_auth_cookies(response, csrf_service)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid or expired.",
        )

    raw_new_refresh, new_rt = result

    # refresh_tokens() commits the rotation. Because RLS context is
    # transaction-local, re-establish the verified token tenant before the
    # membership lookup used to mint the next access token.
    await db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(new_rt.organisation_id)},
    )
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(new_rt.user_id)},
    )

    # Look up user + membership for new access token
    from sqlalchemy import select as sa_select

    from app.db.models.membership import OrganisationMembership as OrgMem

    mem_result = await db.execute(
        sa_select(OrgMem).where(
            OrgMem.user_id == new_rt.user_id,
            OrgMem.organisation_id == new_rt.organisation_id,
        )
    )
    membership = mem_result.scalar_one_or_none()
    if membership is None:
        _clear_auth_cookies(response, csrf_service)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Organisation membership not found.",
        )

    access_token = jwt_service.issue(
        user_id=new_rt.user_id,
        organisation_id=new_rt.organisation_id,
        org_role=membership.org_role,
        family_id=str(new_rt.family_id),
    )

    max_age = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
    _set_refresh_cookie(response, raw_new_refresh, settings, max_age)
    csrf_service.set_csrf_cookie(response, str(new_rt.family_id))

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: RawDB,
    settings: AppSettings,
    payload: CurrentPayload,
    membership: CurrentMembership,
    csrf_service: Annotated[CSRFService, Depends(get_csrf_service)],
    refresh_token: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
) -> dict[str, str]:
    """Revoke the current refresh token family and clear cookies."""
    svc = _build_auth_service(settings)
    refresh_svc = RefreshTokenService(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    if refresh_token:
        rt = await refresh_svc.find_active_by_raw_token(db, raw_token=refresh_token)
        if rt is not None:
            await svc.logout(
                db,
                family_id=rt.family_id,
                user_id=payload.user_id,
                organisation_id=payload.organisation_id,
                request_id=request_id,
                client_ip=client_ip,
            )

    _clear_auth_cookies(response, csrf_service)
    return {"message": "Logged out successfully."}


@router.post("/logout-all")
async def logout_all(
    request: Request,
    response: Response,
    db: RawDB,
    settings: AppSettings,
    payload: CurrentPayload,
    membership: CurrentMembership,
    csrf_service: Annotated[CSRFService, Depends(get_csrf_service)],
) -> dict[str, str]:
    """Revoke all refresh token families for the current user+org."""
    svc = _build_auth_service(settings)
    request_id = request.headers.get(settings.REQUEST_ID_HEADER)
    client_ip = request.client.host if request.client else None

    await svc.logout_all(
        db,
        user_id=payload.user_id,
        organisation_id=payload.organisation_id,
        request_id=request_id,
        client_ip=client_ip,
    )
    _clear_auth_cookies(response, csrf_service)
    return {"message": "All sessions terminated."}


@router.get("/me")
async def me(
    payload: CurrentPayload,
    membership: CurrentMembership,
    db: RawDB,
) -> MeResponse:
    """Return current user and org context."""
    from sqlalchemy import select as sa_select

    from app.db.models.user import User

    user_result = await db.execute(sa_select(User).where(User.id == payload.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    org_result = await db.execute(
        sa_select(Organisation).where(Organisation.id == payload.organisation_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Org not found.")

    return MeResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        organisation_id=org.id,
        organisation_slug=org.slug,
        org_role=membership.org_role,
        workspace_id=payload.workspace_id,
        is_platform_admin=user.is_platform_admin,
    )


@router.post("/change-password")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    db: RawDB,
    settings: AppSettings,
    payload: CurrentPayload,
    membership: CurrentMembership,
) -> dict[str, str]:
    """Change the current user's password."""
    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from app.db.models.user import User

    pwd_svc = PasswordService(
        pepper=settings.ARGON2_PEPPER,
        pepper_version=settings.ARGON2_PEPPER_VERSION,
    )

    user_result = await db.execute(sa_select(User).where(User.id == payload.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    if not pwd_svc.verify(body.current_password, user.password_hash, user.pepper_version):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    new_hash = pwd_svc.hash(body.new_password)
    await db.execute(
        sa_update(User)
        .where(User.id == payload.user_id)
        .values(password_hash=new_hash, pepper_version=settings.ARGON2_PEPPER_VERSION)
    )
    await db.commit()

    return {"message": "Password changed successfully."}
