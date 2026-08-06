"""Phase 1B — Administrative identity and membership management.

Revision ID: 0002_phase_1b
Revises:     0001_phase_1a_foundation
Create Date: 2026-08-04

Tables added:
  - invitations          (org-scoped, workspace_id nullable)
  - teams                (org-scoped, workspace_id nullable)
  - team_memberships     (org-scoped, references teams)
  - service_accounts     (org-scoped, non-human identities)
  - api_keys             (org-scoped, references service_accounts)

Security:
  - All tables use FORCE ROW LEVEL SECURITY.
  - RLS policies use the same NULLIF fail-closed pattern as Phase 1A.
  - invitation token_hash — only the hash is stored; raw token never persisted.
  - api_key secret_hash — same; raw key returned once at creation only.
  - Partial unique index prevents duplicate active invitations per email+org.
  - Service accounts cannot have passwords (no password_hash column).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0002_phase_1b"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. invitations
    # -------------------------------------------------------------------------
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_email", sa.String(320), nullable=False),
        sa.Column("org_role", sa.String(32), nullable=True),
        sa.Column("workspace_role", sa.String(32), nullable=True),
        # BLAKE2b(INVITATION_TOKEN_PEPPER + raw_token) — raw token NEVER stored.
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_invitations_organisation_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_invitations_created_by_user_id",
        ),
    )
    op.create_index("ix_invitations_organisation_id", "invitations", ["organisation_id"])
    op.create_index("ix_invitations_invited_email", "invitations", ["invited_email"])
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"])

    # Partial unique index: at most one active (non-accepted, non-revoked, non-expired)
    # invitation per email+org. Prevents duplicate active invitations.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invitations_active_email_org
        ON invitations (organisation_id, invited_email)
        WHERE accepted_at IS NULL AND revoked_at IS NULL
        """
    )

    # -------------------------------------------------------------------------
    # 2. teams
    # -------------------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_teams_organisation_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_teams_created_by_user_id",
        ),
        sa.UniqueConstraint(
            "organisation_id", "workspace_id", "name",
            name="uq_teams_org_workspace_name",
        ),
    )
    op.create_index("ix_teams_organisation_id", "teams", ["organisation_id"])
    op.create_index("ix_teams_workspace_id", "teams", ["workspace_id"])

    # -------------------------------------------------------------------------
    # 3. team_memberships
    # -------------------------------------------------------------------------
    op.create_table(
        "team_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Denormalised for RLS enforcement.
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_team_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_organisation_id",
        ),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_user"),
    )
    op.create_index("ix_team_memberships_team_id", "team_memberships", ["team_id"])
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"])
    op.create_index("ix_team_memberships_organisation_id", "team_memberships", ["organisation_id"])

    # -------------------------------------------------------------------------
    # 4. service_accounts
    # -------------------------------------------------------------------------
    op.create_table(
        "service_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
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
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_service_accounts_organisation_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_service_accounts_created_by_user_id",
        ),
        sa.UniqueConstraint(
            "organisation_id", "name",
            name="uq_service_accounts_org_name",
        ),
    )
    op.create_index("ix_service_accounts_organisation_id", "service_accounts", ["organisation_id"])

    # -------------------------------------------------------------------------
    # 5. api_keys
    # -------------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        # Public prefix: first 8 chars of key (shown in UI, safe to expose).
        sa.Column("key_prefix", sa.String(16), nullable=False),
        # BLAKE2b-256(API_KEY_PEPPER + raw_key) — raw key NEVER stored.
        sa.Column("secret_hash", sa.String(128), nullable=False),
        # JSON array of permission strings ["org:read", ...].
        sa.Column(
            "scopes",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_accounts.id"],
            ondelete="CASCADE",
            name="fk_api_keys_service_account_id",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name="fk_api_keys_organisation_id",
        ),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_prefix"),
    )
    op.create_index("ix_api_keys_service_account_id", "api_keys", ["service_account_id"])
    op.create_index("ix_api_keys_organisation_id", "api_keys", ["organisation_id"])

    # -------------------------------------------------------------------------
    # 6. FORCE ROW LEVEL SECURITY on all new tables
    # -------------------------------------------------------------------------
    for table in ("invitations", "teams", "team_memberships", "service_accounts", "api_keys"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # -------------------------------------------------------------------------
    # 7. RLS policies — same NULLIF fail-closed pattern as Phase 1A
    # -------------------------------------------------------------------------

    # invitations
    op.execute(
        """
        CREATE POLICY invitations_tenant_isolation ON invitations
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

    # teams
    op.execute(
        """
        CREATE POLICY teams_tenant_isolation ON teams
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

    # team_memberships
    op.execute(
        """
        CREATE POLICY team_memberships_tenant_isolation ON team_memberships
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

    # service_accounts
    op.execute(
        """
        CREATE POLICY service_accounts_tenant_isolation ON service_accounts
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

    # api_keys
    op.execute(
        """
        CREATE POLICY api_keys_tenant_isolation ON api_keys
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
    # 8. Grants — atlascore role: full CRUD on new tables
    # -------------------------------------------------------------------------
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE
        ON invitations, teams, team_memberships, service_accounts, api_keys
        TO atlascore
        """
    )

    # -------------------------------------------------------------------------
    # 9. fn_audit_insert_global — Phase 1B: NO change to the allowlist.
    #    invitation.expired is NOT a global event: invitations are always
    #    tenant-owned and expiry is always detected within accept(), which has
    #    a valid JWT org context.  The Phase 1A 4-type allowlist is correct and
    #    does not need extension.  No-op here; keeping the section for clarity.
    # -------------------------------------------------------------------------
    # (no migration SQL required — fn_audit_insert_global remains as Phase 1A left it)


def downgrade() -> None:
    # Restore the Phase 1A version of fn_audit_insert_global
    op.execute("DROP FUNCTION IF EXISTS fn_audit_insert_global")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_audit_insert_global(
            p_event_type  text,
            p_actor_id    uuid,
            p_event_data  jsonb,
            p_request_id  text,
            p_client_ip   text,
            p_outcome     text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        AS $$
        DECLARE
            v_allowed_types text[] := ARRAY[
                'auth.login_failed',
                'auth.pre_auth_session_expired',
                'auth.pre_auth_session_reused',
                'auth.token_reuse_detected'
            ];
        BEGIN
            IF p_event_type != ALL(v_allowed_types) THEN
                RAISE EXCEPTION 'fn_audit_insert_global: disallowed event type %', p_event_type;
            END IF;

            INSERT INTO audit_events (
                id, organisation_id, actor_user_id,
                event_type, event_data, request_id, client_ip, outcome, created_at
            ) VALUES (
                gen_random_uuid(),
                NULL,
                p_actor_id,
                p_event_type,
                COALESCE(p_event_data, '{}'::jsonb),
                p_request_id,
                p_client_ip,
                COALESCE(p_outcome, 'failure'),
                now()
            );
        END;
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION fn_audit_insert_global FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION fn_audit_insert_global TO atlascore")

    # Drop Phase 1B tables in reverse dependency order.
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON api_keys FROM atlascore")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON service_accounts FROM atlascore")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON team_memberships FROM atlascore")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON teams FROM atlascore")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON invitations FROM atlascore")

    op.drop_table("api_keys")
    op.drop_table("service_accounts")
    op.drop_table("team_memberships")
    op.drop_table("teams")
    op.drop_table("invitations")
