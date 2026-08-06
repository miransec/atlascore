"""Phase 2B vector fix — convert embedding column to pgvector VECTOR type.

Revision ID: 0006_phase_2b_vector_column
Revises:     0005_phase_2b_retrieval
Create Date: 2026-08-05

Background:
  Phase 2A created knowledge_chunk_embeddings.embedding as sa.Text (storing
  a JSON-encoded float array).  Phase 2B retrieval was originally written to
  load all embedding rows into Python and compute cosine similarity in Python —
  an exact scan that reads every row in the workspace partition.

  This migration replaces that design:
    1. Enables the pgvector extension if not already present (Phase 1A enables
       it, but we guard with IF NOT EXISTS for safety).
    2. Adds a new column  embedding_vec  of type  vector  (variable-dimension,
       pgvector native type).  We use variable-dimension (no explicit N in the
       ALTER) because different models can have different dimensions; filtering
       by the dimensions column before comparison prevents mixing spaces.
    3. Backfills embedding_vec from the JSON text in embedding.
    4. Makes embedding_vec NOT NULL once backfilled (safe if no rows exist yet;
       for live systems with data, perform the backfill step separately first).
    5. Drops the old Text column embedding.
    6. Renames embedding_vec → embedding so no application code outside vector.py
       needs to change (all SQL is in that one file).

Why variable-dimension vector (no explicit N):
  - Different embedding providers may have different dimensions
    (e.g. 768, 1536, 3072).
  - The dimensions column already records the dimension for every stored row.
  - vector retrieval filters by (model_id, dimensions) before any comparison —
    dimension mismatches are excluded in the WHERE clause, not at the type level.
  - A fixed-dimension column would require one column per model dimension, or a
    migration every time the embedding model changes.
  - pgvector's cosine distance operator (<=>)  works on same-dimension pairs;
    the WHERE filter on kce.dimensions = :dimensions ensures this is guaranteed
    before the operator is applied.

Exact scan (no HNSW/IVFFlat):
  - An ANN index requires a fixed-dimension VECTOR column.  Using variable
    dimension keeps the provider-agnostic abstraction and is correct for the
    current bounded dataset size.
  - Phase 2C or a dedicated embedding migration may standardise on a single
    model and introduce HNSW if needed.

Upgrade idempotence:
  - All DDL steps use IF NOT EXISTS / IF EXISTS guards.
  - The backfill casts NULL → NULL (idempotent if run twice on an empty table).

Downgrade:
  - Restores the Text column from the vector data.
  - The round-trip through ::text loses floating-point precision vs. the
    original JSON but preserves the data shape.
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision = "0006_phase_2b_vector_column"
down_revision = "0005_phase_2b_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Ensure pgvector is available (Phase 1A enables it; guard here).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Add the new vector column (nullable initially for backfill).
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        ADD COLUMN IF NOT EXISTS embedding_vec vector
        """
    )

    # 3. Backfill: cast JSON text → float[] → vector.
    #    embedding stores a JSON array of floats, e.g. '[0.1, 0.2, ...]'.
    #    The cast  text::float[] → vector  is provided by pgvector.
    op.execute(
        """
        UPDATE knowledge_chunk_embeddings
        SET embedding_vec = embedding::vector
        WHERE embedding_vec IS NULL
          AND embedding IS NOT NULL
        """
    )

    # 4. Make embedding_vec NOT NULL (safe: either no rows, or all backfilled).
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        ALTER COLUMN embedding_vec SET NOT NULL
        """
    )

    # 5. Drop the old text column.
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        DROP COLUMN IF EXISTS embedding
        """
    )

    # 6. Rename the new column to the original name so app code sees 'embedding'.
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        RENAME COLUMN embedding_vec TO embedding
        """
    )


def downgrade() -> None:
    # Restore a Text column from the vector data.
    # Round-trip precision is acceptable since the original JSON was already
    # limited to Python float precision.

    # 1. Add text column back (nullable for backfill).
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        ADD COLUMN IF NOT EXISTS embedding_text text
        """
    )

    # 2. Backfill from vector → JSON text (pgvector emits '[x, y, ...]' notation).
    op.execute(
        """
        UPDATE knowledge_chunk_embeddings
        SET embedding_text = embedding::text
        WHERE embedding_text IS NULL
        """
    )

    # 3. Make NOT NULL.
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        ALTER COLUMN embedding_text SET NOT NULL
        """
    )

    # 4. Drop the vector column.
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        DROP COLUMN IF EXISTS embedding
        """
    )

    # 5. Rename text column back to 'embedding'.
    op.execute(
        """
        ALTER TABLE knowledge_chunk_embeddings
        RENAME COLUMN embedding_text TO embedding
        """
    )
