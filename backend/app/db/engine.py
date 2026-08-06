"""
Async SQLAlchemy engine and session factory.

OrganisationScopedSession sets transaction-scoped PostgreSQL configuration
parameters that are consumed by Row-Level Security policies:

  app.current_organisation_id  — mandatory for all tenant-scoped work
  app.current_user_id          — set for all authenticated requests
  app.current_workspace_id     — set for workspace-scoped requests only

Context is transaction-scoped (the third argument `true` to set_config):
  - Cleared automatically on COMMIT or ROLLBACK
  - Never leaks across pooled connections
  - Cannot be seen by concurrent transactions on the same connection

Callers MUST NOT pass a non-None organisation_id to tenant-scoped queries
without going through this session factory.

For workspace-scoped knowledge tables, workspace_id MUST be supplied.
Omitting it causes workspace_id to be set to '' which maps to NULL via
NULLIF — the fail-closed predicate then hides all workspace rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async SQLAlchemy engine from application settings."""
    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT,
        echo=settings.DATABASE_ECHO,
        pool_pre_ping=True,
        # Prevent connections from being reused across requests without
        # confirming the connection is still alive.
        pool_recycle=3600,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autobegin=True,
        autoflush=False,
    )


class OrganisationScopedSession:
    """
    Async context manager that yields a session with RLS context set.

    Usage:
        async with OrganisationScopedSession(
            session_factory,
            organisation_id=org_id,
            user_id=user_id,
            workspace_id=ws_id,   # required for workspace-scoped knowledge tables
        ) as session:
            result = await session.execute(...)

    The PostgreSQL session-local variables are set at transaction scope
    (set_config third argument = true), meaning they are automatically
    cleared when the transaction commits or rolls back.  They are NOT
    visible to other transactions.

    Both organisation_id and user_id are required for tenant-scoped work.
    Pass user_id=None only for bootstrap operations (registration) where
    no authenticated user exists yet.

    For workspace-scoped knowledge tables, pass workspace_id.  When absent
    (None), app.current_workspace_id is set to '' which NULLIF maps to NULL,
    causing the workspace RLS predicate to hide all rows (fail-closed).

    SECURITY: Callers must never source organisation_id or workspace_id from
    request data (query strings, JSON bodies, headers).  Both must come from
    the verified JWT claim checked against a live database membership row.
    workspace_id must additionally be validated via a live WorkspaceMembership
    query before being passed here.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        organisation_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> None:
        self._factory = session_factory
        self._org_id = organisation_id
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        session = self._factory()
        self._session = session

        # Set transaction-scoped RLS context parameters.
        # All calls use is_local=true (third argument), which is the
        # PostgreSQL semantics for "transaction-local SET".
        org_id_str = str(self._org_id)
        user_id_str = str(self._user_id) if self._user_id else ""
        workspace_id_str = str(self._workspace_id) if self._workspace_id else ""

        await session.execute(
            text("SELECT set_config('app.current_organisation_id', :org_id, true)"),
            {"org_id": org_id_str},
        )
        await session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": user_id_str},
        )
        await session.execute(
            text("SELECT set_config('app.current_workspace_id', :ws_id, true)"),
            {"ws_id": workspace_id_str},
        )
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        session = self._session
        if session is None:
            return
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()


async def get_raw_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a raw (unscoped) session for operations that do not require
    tenant context, such as:
    - Pre-authentication session operations (step 1 of login)
    - Global audit writes via SECURITY DEFINER function

    NEVER use this for tenant-scoped queries.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Connection-level event hook — wipe RLS context on return to pool
# ---------------------------------------------------------------------------
def _clear_rls_context_on_checkin(
    dbapi_con: object,
    con_record: object,
) -> None:
    """
    Safety net: clear the RLS context variables when a connection is
    returned to the pool.  Transaction-local SET already clears them on
    COMMIT/ROLLBACK, but this provides an additional defence against any
    code path that might forget to commit or rollback.
    """
    # This runs synchronously via the sync-level event hook on the
    # underlying DBAPI connection.
    try:
        cursor = dbapi_con.cursor()  # type: ignore[attr-defined]
        cursor.execute(
            "SELECT set_config('app.current_organisation_id', '', false), "
            "       set_config('app.current_user_id', '', false), "
            "       set_config('app.current_workspace_id', '', false)"
        )
        cursor.close()
    except Exception:
        # Never raise from a pool event — it would kill the connection.
        pass


def register_pool_events(engine: AsyncEngine) -> None:
    """Register the connection-level safety-net event on the engine."""
    event.listen(engine.sync_engine, "checkin", _clear_rls_context_on_checkin)
