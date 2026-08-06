"""
Shared pytest fixtures for AtlasCore.

The environment defaults are installed before importing application modules so
module-level Settings() construction cannot see placeholder development secrets.
Explicit environment variables supplied by Docker/CI still win via setdefault().
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Test settings MUST be established before importing app.* modules.
# ---------------------------------------------------------------------------

_TEST_SECRETS = {
    "DATABASE_URL": os.getenv(
        "DATABASE_URL_TEST",
        "postgresql+asyncpg://atlascore:atlascore@localhost:5433/atlascore_test",
    ),
    "REDIS_URL": "redis://localhost:6380/1",
    "JWT_SECRET_KEY": "t" * 64,
    "REFRESH_TOKEN_PEPPER": "r" * 32,
    "ARGON2_PEPPER": "a" * 32,
    "ARGON2_PEPPER_VERSION": "1",
    "CSRF_SECRET": "c" * 32,
    "PRE_AUTH_SESSION_PEPPER": "p" * 32,
    "ALLOWED_ORIGINS": "http://localhost:3100",
    "ENVIRONMENT": "test",
    "SECURE_COOKIES": "false",
    "ACCESS_TOKEN_EXPIRE_SECONDS": "900",
    "REFRESH_TOKEN_EXPIRE_SECONDS": "604800",
    "PRE_AUTH_SESSION_EXPIRE_SECONDS": "300",
    "API_KEY_PEPPER": "k" * 32,
    "INVITATION_TOKEN_PEPPER": "i" * 32,
}

for k, v in _TEST_SECRETS.items():
    os.environ.setdefault(k, v)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.csrf import CSRFService
from app.auth.password import PasswordService
from app.auth.pre_auth import PreAuthSessionService
from app.auth.refresh import RefreshTokenService
from app.auth.tokens import JWTService
from app.core.config import Settings
from app.services.audit import AuditService
from app.services.auth_service import AuthService
from app.services.invitation_service import InvitationService
from app.services.service_account_service import ServiceAccountService
from app.services.team_service import TeamService

_settings = Settings()

# ---------------------------------------------------------------------------
# Engine — session-scoped
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def engine():
    url = _settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+asyncpg://")
    eng = create_async_engine(url, echo=False, future=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def admin_engine():
    """Migration/admin engine for security assertions that the app role may not perform."""
    admin_url = os.getenv(
        "DATABASE_URL_ADMIN",
        "postgresql+asyncpg://postgres:postgres@localhost:5433/atlascore_test",
    )
    eng = create_async_engine(admin_url, echo=False, future=True)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# Tables — create once, drop at the end of the session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def tables(engine):
    """
    Verify that the test database has already been provisioned by Alembic.

    Schema objects such as RLS policies, security-definer functions, triggers,
    indexes, and tables are migration-owned and must not be recreated or
    dropped by the restricted application role.

    Individual tests remain isolated by the outer transaction used by the
    db/raw_db fixtures below.
    """
    async with engine.connect() as conn:
        organisations = await conn.scalar(text("SELECT to_regclass('public.organisations')"))
        memberships = await conn.scalar(
            text("SELECT to_regclass('public.organisation_memberships')")
        )

        if organisations is None or memberships is None:
            pytest.fail(
                "atlascore_test is not migrated; run Alembic migrations "
                "with the database migration/admin role before pytest"
            )

    yield


# ---------------------------------------------------------------------------
# db — per-test async session inside a nested transaction (SAVEPOINT)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db(engine, tables) -> AsyncGenerator[AsyncSession, None]:
    """
    Each test gets a fresh SAVEPOINT.  On teardown we ROLLBACK to the savepoint
    so the DB state is pristine for the next test without recreating tables.
    """
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()  # SAVEPOINT

        session_factory = async_sessionmaker(bind=conn, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
            await session.rollback()

        await conn.rollback()


# ---------------------------------------------------------------------------
# Raw (unscoped) session for global / pre-auth operations
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def raw_db(engine, tables) -> AsyncGenerator[AsyncSession, None]:
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()
        session_factory = async_sessionmaker(bind=conn, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
            await session.rollback()
        await conn.rollback()


# ---------------------------------------------------------------------------
# Service instances
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return _settings


@pytest.fixture()
def password_service(settings: Settings) -> PasswordService:
    return PasswordService(
        pepper=settings.ARGON2_PEPPER,
        pepper_version=settings.ARGON2_PEPPER_VERSION,
    )


@pytest.fixture()
def jwt_service(settings: Settings) -> JWTService:
    return JWTService(settings)


@pytest.fixture()
def refresh_service(settings: Settings) -> RefreshTokenService:
    return RefreshTokenService(settings)


@pytest.fixture()
def pre_auth_service(settings: Settings) -> PreAuthSessionService:
    return PreAuthSessionService(settings)


@pytest.fixture()
def csrf_service(settings: Settings) -> CSRFService:
    return CSRFService(settings)


@pytest.fixture()
def audit_service() -> AuditService:
    return AuditService()


@pytest.fixture()
def invitation_service(settings: Settings) -> InvitationService:
    return InvitationService(settings)


@pytest.fixture()
def service_account_service(settings: Settings) -> ServiceAccountService:
    return ServiceAccountService(settings)


@pytest.fixture()
def team_service() -> TeamService:
    return TeamService()


@pytest.fixture()
def auth_service(
    password_service: PasswordService,
    jwt_service: JWTService,
    refresh_service: RefreshTokenService,
    pre_auth_service: PreAuthSessionService,
) -> AuthService:
    return AuthService(
        password_service=password_service,
        jwt_service=jwt_service,
        refresh_service=refresh_service,
        pre_auth_service=pre_auth_service,
    )


# ---------------------------------------------------------------------------
# HTTPX AsyncClient
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client(engine, tables) -> AsyncGenerator[AsyncClient, None]:
    from app.api.deps import get_raw_db, get_settings_dep
    from app.main import app

    # Override the session dependency to use the test engine.
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_raw_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_raw_db] = override_get_raw_db
    app.dependency_overrides[get_settings_dep] = lambda: _settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Independent session factory — for durability tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def independent_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """
    A session factory that creates INDEPENDENT sessions on the test engine.

    Unlike the `db` fixture (which wraps everything in a SAVEPOINT-rollback),
    sessions from this factory each get their own real connection and can commit
    independently.

    Use this ONLY for tests that need to prove durability across separate
    transactions — e.g. the invitation.expired audit durability test (scenario
    28) which must query from a new session after the original accept() fails.

    Tests that use this factory are responsible for cleaning up the rows they
    commit, or for scoping them narrowly enough that they don't interfere with
    the rollback-isolated `db` fixture.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.example"


def make_slug(prefix: str = "org") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
