"""Phase 2A — Workspace RLS Hardening.

Revision ID: 0004_phase_2a_ws_rls
Revises:     0003_phase_2a
Create Date: 2026-08-05

Security fix: the Phase 2A knowledge table RLS policies (0003) checked
organisation_id only.  A composite FK (workspace_id, organisation_id) →
workspaces(id, organisation_id) prevents REFERENTIAL inconsistency but does
NOT prevent same-organisation cross-workspace access via RLS.

Example:
  org A, workspace W1, workspace W2
  A session with app.current_organisation_id = A
  can see rows belonging to W2 while the active context is for W1.

This migration:
  1. Drops the six Phase 2A knowledge table policies that check only
     organisation_id.
  2. Recreates them to check BOTH organisation_id AND workspace_id using the
     same NULLIF fail-closed pattern (missing workspace context → NULL →
     zero rows).
  3. The pool checkin safety net in engine.py is updated to also clear
     app.current_workspace_id (Python-side change, not DB migration).

USING / WITH CHECK predicate for all six knowledge tables:

    organisation_id = NULLIF(
        current_setting('app.current_organisation_id', true), ''
    )::uuid
    AND
    workspace_id = NULLIF(
        current_setting('app.current_workspace_id', true), ''
    )::uuid

Fail-closed behaviour:
  app.current_workspace_id absent or ''  →  NULL  →  zero rows
  app.current_workspace_id = wrong UUID  →  zero rows
  correct org + correct workspace        →  own rows only
  correct org but wrong workspace        →  zero rows

DOES NOT TOUCH:
  Phase 1A tables (organisations, users, workspaces, audit_events, ...)
  Phase 1B tables (invitations, teams, service_accounts, api_keys, ...)
  Any GRANT that is already in place.
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision = "0004_phase_2a_ws_rls"
down_revision = "0003_phase_2a"
branch_labels = None
depends_on = None

# The six Phase 2A knowledge tables whose RLS policies are being corrected.
_KNOWLEDGE_TABLES = [
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_ingestion_jobs",
    "knowledge_chunks",
    "knowledge_chunk_embeddings",
]

# The old policy name pattern (from migration 0003).
_OLD_POLICY_SUFFIX = "_tenant_isolation"

# The new policy name pattern.
_NEW_POLICY_SUFFIX = "_workspace_isolation"


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Drop old organisation-only policies and replace with org+workspace policies
    # -------------------------------------------------------------------------
    for table in _KNOWLEDGE_TABLES:
        old_policy = f"{table}{_OLD_POLICY_SUFFIX}"
        new_policy = f"{table}{_NEW_POLICY_SUFFIX}"

        # Drop the old policy (organisation_id only).
        op.execute(f"DROP POLICY IF EXISTS {old_policy} ON {table}")

        # Create the hardened policy (organisation_id AND workspace_id).
        # Both predicates use NULLIF fail-closed semantics:
        #   - Empty string ('') maps to NULL via NULLIF.
        #   - A NULL UUID in either predicate causes the row to be invisible.
        #   - A session that has not set app.current_workspace_id gets no rows.
        op.execute(
            f"""
            CREATE POLICY {new_policy} ON {table}
            AS PERMISSIVE FOR ALL TO atlascore
            USING (
                organisation_id = NULLIF(
                    current_setting('app.current_organisation_id', true), ''
                )::uuid
                AND
                workspace_id = NULLIF(
                    current_setting('app.current_workspace_id', true), ''
                )::uuid
            )
            WITH CHECK (
                organisation_id = NULLIF(
                    current_setting('app.current_organisation_id', true), ''
                )::uuid
                AND
                workspace_id = NULLIF(
                    current_setting('app.current_workspace_id', true), ''
                )::uuid
            )
            """
        )


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Restore the old organisation-only policies (reverts this fix).
    # WARNING: This re-introduces the same-org cross-workspace RLS gap.
    # -------------------------------------------------------------------------
    for table in _KNOWLEDGE_TABLES:
        old_policy = f"{table}{_OLD_POLICY_SUFFIX}"
        new_policy = f"{table}{_NEW_POLICY_SUFFIX}"

        # Drop the hardened policy.
        op.execute(f"DROP POLICY IF EXISTS {new_policy} ON {table}")

        # Restore the original organisation-only policy.
        op.execute(
            f"""
            CREATE POLICY {old_policy} ON {table}
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
