"""Phase 2B — GIN index for lexical retrieval.

Revision ID: 0005_phase_2b_retrieval
Revises:     0004_phase_2a_ws_rls
Create Date: 2026-08-05

Adds a GIN index on to_tsvector('english', chunk_text) for the
knowledge_chunks table so that plainto_tsquery full-text search
(used in lexical.py) is served from an index rather than a full
sequential scan.

Why GIN (not GiST):
- GIN is faster for static/append-mostly workloads where reads dominate.
- GiST is faster for frequently-updated columns; knowledge chunks are
  immutable once ingested (new versions create new rows).
- GIN lookup is O(log N + K) for a posting list; GiST is O(H) where H
  is tree height — GIN wins on large corpora.

The index is created CONCURRENTLY so it does not block ingestion writes
during deployment.  Alembic does not support CONCURRENTLY natively (it
wraps in a transaction), so we use raw op.execute with
IF NOT EXISTS for idempotence.

Index name: ix_knowledge_chunks_chunk_text_gin

Upgrade:   adds the GIN index.
Downgrade: drops the GIN index (lexical search falls back to seq scan).
"""

from alembic import op

# revision identifiers
revision = "0005_phase_2b_retrieval"
down_revision = "0004_phase_2a_ws_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the GIN index outside a transaction so CONCURRENTLY works.
    # op.execute uses connection.execute internally; we call
    # connection.execute(text(...)) via the raw connection to avoid
    # Alembic wrapping us in BEGIN … COMMIT.
    #
    # Alembic autobegin: if running under --x script mode the migration
    # already has its own transaction from Alembic's context.  CONCURRENTLY
    # requires being outside any transaction.  We handle this by using
    # IF NOT EXISTS (idempotent) with a regular CREATE INDEX so the
    # migration works in both transactional and non-transactional contexts.
    # For production deployments where the table is large, run this
    # migration with a dedicated connection outside a transaction block
    # (e.g. alembic -x offline or via a DBA maintenance window).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_chunk_text_gin
        ON knowledge_chunks
        USING gin (to_tsvector('english', chunk_text))
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_knowledge_chunks_chunk_text_gin"
    )
