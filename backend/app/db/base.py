"""
Re-export shim for the SQLAlchemy declarative Base.

Importing from app.db.base imports all ORM models as a side-effect,
which registers them with Base.metadata so create_all / drop_all work.
"""

# Import all models to register them with Base.metadata
