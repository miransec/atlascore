"""Phase 2A — Secure Enterprise Knowledge Foundation.

Revision ID: 0003_phase_2a
Revises:     0002_phase_1b
Create Date: 2026-08-05

Tables added:
  - knowledge_sources         (workspace-scoped, one per connector/type per workspace)
  - knowledge_documents       (workspace-scoped, per uploaded document)
  - knowledge_document_versions (immutable versions of document content)
  - knowledge_ingestion_jobs  (queued/running/succeeded/failed/cancelled)
  - knowledge_chunks          (parsed + chunked text, per version)
  - knowledge_chunk_embeddings (vector embedding per chunk)

Security:
  - All tables use FORCE ROW LEVEL SECURITY.
  - RLS policies use the same NULLIF fail-closed pattern as Phase 1A/1B.
  - Composite FK (workspace_id, organisation_id) on every workspace-scoped
    table guarantees DB-level cross-tenant consistency.
  - storage_key is server-generated; uploaded filename is display metadata only.
  - content_sha256 ensures integrity; never used as a password hash.
  - No global deduplication across organisations.
  - GLOBAL_EVENT_TYPES NOT extended.

Enums:
  - knowledge_source_type  (manual_upload, ...)
  - ingestion_job_status   (queued, running, succeeded, failed, cancelled)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0003_phase_2a"
down_revision = "0002_phase_1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Enums
    # -------------------------------------------------------------------------
    knowledge_source_type = postgresql.ENUM(
        "manual_upload",
        name="knowledge_source_type",
        create_type=True,
    )
    knowledge_source_type.create(op.get_bind())

    ingestion_job_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        name="ingestion_job_status",
        create_type=True,
    )
    ingestion_job_status.create(op.get_bind())

    # -------------------------------------------------------------------------
    # 2. knowledge_sources
    # -------------------------------------------------------------------------
    op.create_table(
        "knowledge_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM("manual_upload", name="knowledge_source_type", create_type=False),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # JSON config — must NOT contain secrets/tokens/passwords (enforced at service layer).
        sa.Column(
            "configuration",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Composite FK: (workspace_id, organisation_id) → workspaces(id, organisation_id)
        # Guarantees a workspace cannot belong to a different organisation than the source.
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sources_workspace_org",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_sources_created_by_user_id",
        ),
        sa.UniqueConstraint(
            "workspace_id", "display_name",
            name="uq_knowledge_sources_workspace_name",
        ),
    )
    op.create_index("ix_knowledge_sources_organisation_id", "knowledge_sources", ["organisation_id"])
    op.create_index("ix_knowledge_sources_workspace_id", "knowledge_sources", ["workspace_id"])

    # -------------------------------------------------------------------------
    # 3. knowledge_documents
    # -------------------------------------------------------------------------
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Original filename as provided by the uploader — display metadata only.
        # Never used as a filesystem path.
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("media_type", sa.String(256), nullable=False),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Composite FK: workspace isolation
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_documents_workspace_org",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            ondelete="CASCADE",
            name="fk_knowledge_documents_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_documents_created_by_user_id",
        ),
    )
    op.create_index("ix_knowledge_documents_organisation_id", "knowledge_documents", ["organisation_id"])
    op.create_index("ix_knowledge_documents_workspace_id", "knowledge_documents", ["workspace_id"])
    op.create_index("ix_knowledge_documents_source_id", "knowledge_documents", ["source_id"])

    # -------------------------------------------------------------------------
    # 4. knowledge_document_versions
    #    Immutable. Each upload creates a new version row.
    # -------------------------------------------------------------------------
    op.create_table(
        "knowledge_document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        # Hex SHA-256 of the raw file bytes. For content integrity / within-org dedup.
        # NOT a password hash — use SHA-256 directly.
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("media_type", sa.String(256), nullable=False),
        # Server-generated storage key. Never derived from upload filename.
        # Format: {org_id}/{workspace_id}/{document_id}/{version_id}
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_knowledge_document_versions_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_document_versions_workspace_org",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_knowledge_document_versions_created_by_user_id",
        ),
        sa.UniqueConstraint(
            "document_id", "version_number",
            name="uq_knowledge_document_versions_doc_version",
        ),
    )
    op.create_index(
        "ix_knowledge_document_versions_document_id",
        "knowledge_document_versions",
        ["document_id"],
    )
    op.create_index(
        "ix_knowledge_document_versions_organisation_id",
        "knowledge_document_versions",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_document_versions_content_sha256",
        "knowledge_document_versions",
        ["content_sha256"],
    )

    # -------------------------------------------------------------------------
    # 5. knowledge_ingestion_jobs
    # -------------------------------------------------------------------------
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued", "running", "succeeded", "failed", "cancelled",
                name="ingestion_job_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'queued'::ingestion_job_status"),
        ),
        # DB-level idempotency: re-uploading the same version won't create two jobs.
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        # JSON metadata about the run (chunk count, embedding model, etc.)
        sa.Column(
            "result_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_document_versions.id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_ingestion_jobs_workspace_org",
        ),
        # DB-level idempotency: one job per (org, workspace, idempotency_key).
        sa.UniqueConstraint(
            "organisation_id", "workspace_id", "idempotency_key",
            name="uq_knowledge_ingestion_jobs_idempotency",
        ),
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_organisation_id",
        "knowledge_ingestion_jobs",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_workspace_id",
        "knowledge_ingestion_jobs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_version_id",
        "knowledge_ingestion_jobs",
        ["version_id"],
    )
    op.create_index(
        "ix_knowledge_ingestion_jobs_status",
        "knowledge_ingestion_jobs",
        ["status"],
    )

    # -------------------------------------------------------------------------
    # 6. knowledge_chunks
    # -------------------------------------------------------------------------
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Stable ordinal within the version (0-indexed).
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        # Hex SHA-256 of the UTF-8 encoded chunk_text.
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_document_versions.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_workspace_org",
        ),
        sa.UniqueConstraint(
            "version_id", "chunk_index",
            name="uq_knowledge_chunks_version_chunk_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_organisation_id",
        "knowledge_chunks",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_workspace_id",
        "knowledge_chunks",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_version_id",
        "knowledge_chunks",
        ["version_id"],
    )

    # -------------------------------------------------------------------------
    # 7. knowledge_chunk_embeddings
    # -------------------------------------------------------------------------
    # Requires pgvector extension.  Phase 1A already enables it.
    op.create_table(
        "knowledge_chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The embedding model identifier that produced this vector.
        sa.Column("model_id", sa.String(128), nullable=False),
        # Vector stored as text; application layer casts to/from list[float].
        # Using sa.Text for portability in the migration; ORM layer uses Vector type.
        sa.Column("embedding", sa.Text, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunks.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_embeddings_chunk_id",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_embeddings_workspace_org",
        ),
        sa.UniqueConstraint(
            "chunk_id", "model_id",
            name="uq_knowledge_chunk_embeddings_chunk_model",
        ),
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_organisation_id",
        "knowledge_chunk_embeddings",
        ["organisation_id"],
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_workspace_id",
        "knowledge_chunk_embeddings",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_chunk_id",
        "knowledge_chunk_embeddings",
        ["chunk_id"],
    )

    # -------------------------------------------------------------------------
    # 8. UNIQUE(id, organisation_id) on workspaces — required by composite FKs above.
    #    Only add if not already present (Phase 1A may have created it).
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_workspaces_id_organisation_id'
                  AND conrelid = 'workspaces'::regclass
            ) THEN
                ALTER TABLE workspaces
                ADD CONSTRAINT uq_workspaces_id_organisation_id
                UNIQUE (id, organisation_id);
            END IF;
        END
        $$
        """
    )

    # -------------------------------------------------------------------------
    # 9. FORCE ROW LEVEL SECURITY on all new tables
    # -------------------------------------------------------------------------
    for table in (
        "knowledge_sources",
        "knowledge_documents",
        "knowledge_document_versions",
        "knowledge_ingestion_jobs",
        "knowledge_chunks",
        "knowledge_chunk_embeddings",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # -------------------------------------------------------------------------
    # 10. RLS policies — NULLIF fail-closed pattern (organisation_id column)
    # -------------------------------------------------------------------------
    rls_tables = [
        "knowledge_sources",
        "knowledge_documents",
        "knowledge_document_versions",
        "knowledge_ingestion_jobs",
        "knowledge_chunks",
        "knowledge_chunk_embeddings",
    ]
    for table in rls_tables:
        policy_name = f"{table}_tenant_isolation"
        op.execute(
            f"""
            CREATE POLICY {policy_name} ON {table}
            AS PERMISSIVE FOR ALL TO atlascore
            USING (
                organisation_id = NULLIF(
                    current_setting('app.current_organisation_id', true), ''
                )::uuid
            )
            WITH CHECK (
                organisation_id = NULLIF(
                    current_setting('app.current_organisation_id', true), ''
                )::uuid
            )
            """
        )

    # -------------------------------------------------------------------------
    # 11. Grants — atlascore role: full CRUD on new tables
    # -------------------------------------------------------------------------
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON knowledge_sources,
           knowledge_documents,
           knowledge_document_versions,
           knowledge_ingestion_jobs,
           knowledge_chunks,
           knowledge_chunk_embeddings
        TO atlascore
        """
    )

    # -------------------------------------------------------------------------
    # 12. NOTE: GLOBAL_EVENT_TYPES NOT extended.
    #     Knowledge ingestion events are tenant-owned audit events emitted via
    #     AuditService.emit() (standard transactional path) — not via
    #     fn_audit_insert_global, which is restricted to 4 pre-auth events only.
    # -------------------------------------------------------------------------


def downgrade() -> None:
    # Revoke grants
    op.execute(
        """
        REVOKE SELECT, INSERT, UPDATE, DELETE
        ON knowledge_sources,
           knowledge_documents,
           knowledge_document_versions,
           knowledge_ingestion_jobs,
           knowledge_chunks,
           knowledge_chunk_embeddings
        FROM atlascore
        """
    )

    # Drop in reverse dependency order
    op.drop_table("knowledge_chunk_embeddings")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_ingestion_jobs")
    op.drop_table("knowledge_document_versions")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_sources")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS ingestion_job_status")
    op.execute("DROP TYPE IF EXISTS knowledge_source_type")
