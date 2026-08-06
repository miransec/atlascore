"""
AtlasCore backend application entry point.

Lifecycle:
1. Settings validation runs at import (startup fails on bad config).
2. Logging is configured.
3. FastAPI application is created with CORS, middleware, and routers.
4. Lifespan context manager connects/disconnects the database.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(level=settings.LOG_LEVEL, fmt=settings.LOG_FORMAT)
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Starting AtlasCore",
        extra={
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
        },
    )
    # Database engine is initialised lazily on first request via deps.py
    # to allow test overrides.  Nothing to set up here.
    yield
    logger.info("Shutting down AtlasCore")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="AtlasCore API",
        version=settings.APP_VERSION,
        description="Secure enterprise AI operations platform",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # CORS
    # -----------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", settings.REQUEST_ID_HEADER, "X-CSRF-Token"],
        expose_headers=[settings.REQUEST_ID_HEADER],
    )

    # -----------------------------------------------------------------------
    # Request ID middleware
    # -----------------------------------------------------------------------
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        request_id = request.headers.get(settings.REQUEST_ID_HEADER) or str(uuid.uuid4())
        # Make available to request handlers
        request.state.request_id = request_id
        response: Response = await call_next(request)  # type: ignore[misc]
        response.headers[settings.REQUEST_ID_HEADER] = request_id
        return response

    # -----------------------------------------------------------------------
    # Exception handlers
    # -----------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "Unhandled exception",
            extra={"request_id": request_id, "error": str(exc)},
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred."},
        )

    # -----------------------------------------------------------------------
    # Health and readiness endpoints
    # -----------------------------------------------------------------------
    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — always returns 200 if the process is alive.

        Returns:
          status:           "ok"
          version:          Application version string.
          answer_provider:  Configured answer provider (never "real" in demo mode).
          demo_mode:        "true" if ANSWER_DEMO_MODE is set (deterministic provider forced).
        """
        effective_provider = (
            "deterministic-test" if settings.ANSWER_DEMO_MODE else settings.ANSWER_PROVIDER
        )
        return {
            "status": "ok",
            "version": settings.APP_VERSION,
            "answer_provider": effective_provider,
            "demo_mode": "true" if settings.ANSWER_DEMO_MODE else "false",
        }

    @app.get("/readiness", tags=["meta"])
    @app.get("/ready", tags=["meta"])
    async def readiness() -> dict[str, str]:
        """Readiness probe — checks database connectivity.

        Returns 200 {"status": "ready"} when the database is reachable.
        Returns 503 {"status": "not ready", "error": "database unavailable"} otherwise.
        Also exposed at /ready for k8s-style readinessProbe compatibility.
        """
        from app.api.deps import get_session_factory

        try:
            factory = get_session_factory(settings)
            async with factory() as session:
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Readiness check failed", extra={"error": str(exc)})
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not ready", "error": "database unavailable"},
            )
        return {"status": "ready"}

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------
    app.include_router(api_router)

    return app


app = create_app()
