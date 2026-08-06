# =============================================================================
# AtlasCore Backend — Multi-stage Dockerfile
# =============================================================================
# Stage 'dev':  development + test (hot-reload, dev deps, pytest)
# Stage 'prod': production (minimal image, no dev deps)
# =============================================================================

ARG PYTHON_VERSION=3.12

# =============================================================================
# Stage: base
# =============================================================================
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# =============================================================================
# Stage: deps (build dependency layers)
# =============================================================================
FROM base AS deps

# Copy dependency specs first (cache layer)
COPY pyproject.toml ./

# Install all dependencies (including dev) for dev/test stages
RUN uv sync --all-extras

# =============================================================================
# Stage: dev (development and testing)
# =============================================================================
FROM deps AS dev

# Copy application code
COPY . .

# Create non-root user for running the application
RUN addgroup --system atlascore && \
    adduser --system --group atlascore

# Note: in dev mode we run as root for volume mount convenience.
# Production runs as non-root.

EXPOSE 8100

CMD ["uv", "run", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8100", \
     "--reload", "--reload-dir", "/app/app"]

# =============================================================================
# Stage: prod (production)
# =============================================================================
FROM base AS prod

# Install only production dependencies (no dev extras)
COPY pyproject.toml ./
RUN uv sync --no-dev

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Create non-root user
RUN addgroup --system atlascore && \
    adduser --system --group atlascore

USER atlascore

EXPOSE 8100

CMD ["uv", "run", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8100", \
     "--workers", "2"]
