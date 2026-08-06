"""Custom SQLAlchemy database types for AtlasCore."""

from __future__ import annotations

from pgvector.sqlalchemy import Vector


class VectorType(Vector):
    """Native pgvector SQLAlchemy type used for variable-dimension embeddings.

    The database column is created by Alembic as PostgreSQL ``vector``.  Using
    pgvector's native SQLAlchemy type ensures asyncpg binds parameters as vector
    values instead of VARCHAR/TEXT values.
    """

    cache_ok = True
