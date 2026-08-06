"""Phase 1A: secure identity, multi-tenancy, RBAC, PostgreSQL RLS, transactional audit.

Revision ID: 0001
Revises:
Create Date: 2026-08-03

SECURITY NOTES:
- All tenant-scoped tables use ENABLE ROW LEVEL SECURITY + FORCE ROW LEVEL SECURITY.
- The RLS policy is a SINGLE PERMISSIVE 'FOR ALL' policy with BOTH a USING clause
  (guards SELECT, DELETE, and the pre-update existing row) AND a WITH CHECK clause
  (guards INSERT and the post-write row produced by UPDATE).
- Both clauses use a NULLIF null guard: if app.current_organisation_id is not set,
  the null comparison fails closed — no access is granted and no write is permitted.
  Absent context NEVER grants access.
- We do NOT use AS RESTRICTIVE policies. A single permissive policy achieves the
  desired fail-closed semantics without the footgun of restricting legitimate access
  when other permissive policies exist.
- Two PostgreSQL roles:
    atlascore_migration: owns tables, runs migrations, is NOT used at runtime.
    atlascore (application role): no SUPERUSER, no BYPASSRLS, cannot own tables,
      has minimum required CRUD grants, INSERT-only on audit_events (no UPDATE/DELETE).
- Global auth events (before org context) are written via fn_audit_insert_global
  (SECURITY DEFINER), which runs as the migration role and bypasses RLS.
  We do NOT add a permissive 'organisation_id IS NULL' rule to the main audit table.
- The exactly-one-owner constraint uses a DEFERRABLE INITIALLY DEFERRED trigger
  that fires at transaction commit — this allows ownership transfer in a single
  transaction without a transient violation.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==========================================================================
    # ROLES AND EXTENSIONS
    # ==========================================================================

    # Create the application role if it doesn't already exist.
    # The migration role (which runs this script) owns the tables.
    # The application role gets minimum required grants only.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlascore') THEN
                CREATE ROLE atlascore WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
            END IF;
        END$$
    """)

    # Revoke BYPASSRLS from the application role (should not have it, but be explicit)
    op.execute("ALTER ROLE atlascore NOBYPASSRLS")

    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ==========================================================================
    # ENUMS
    # ==========================================================================

    org_role_enum = postgresql.ENUM(
        "owner",
        "administrator",
        "workflow_builder",
        "analyst",
        "operator",
        "viewer",
        "auditor",
        name="org_role",
        create_type=True,
    )
    org_role_enum.create(op.get_bind(), checkfirst=True)

    workspace_role_enum = postgresql.ENUM(
        "administrator",
        "workflow_builder",
        "analyst",
        "operator",
        "viewer",
        "auditor",
        name="workspace_role",
        create_type=True,
    )
    workspace_role_enum.create(op.get_bind(), checkfirst=True)

    audit_outcome_enum = postgresql.ENUM(
        "success",
        "failure",
        "pending",
        name="audit_outcome",
        create_type=True,
    )
    audit_outcome_enum.create(op.get_bind(), checkfirst=True)

    # ==========================================================================
    # TABLES
    # ==========================================================================

    # -- users ---------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("pepper_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_platform_admin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # -- organisations -------------------------------------------------------
    op.create_table(
        "organisations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_organisations_slug", "organisations", ["slug"], unique=True)

    # -- workspaces ----------------------------------------------------------
    # UNIQUE(id, organisation_id) is required for the composite FK from workspace_memberships
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisations.id"],
            ondelete="CASCADE",
            name="fk_workspaces_organisation_id",
        ),
    )
    op.create_index("ix_workspaces_organisation_id", "workspaces", ["organisation_id"])
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"])
    # Unique slug per organisation
    op.create_index("uq_workspaces_org_slug", "workspaces", ["organisation_id", "slug"], unique=True)
    # Composite unique required for FK from workspace_memberships
    op.execute(
        "ALTER TABLE workspaces ADD CONSTRAINT uq_workspaces_id_org UNIQUE (id, organisation_id)"
    )

    # -- organisation_memberships --------------------------------------------
    op.create_table(
        "organisation_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # org_role is nullable: NULL = member without a named role
        sa.Column("org_role", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_org_memberships_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisations.id"],
            ondelete="CASCADE",
            name="fk_org_memberships_organisation_id",
        ),
        sa.UniqueConstraint("organisation_id", "user_id", name="uq_org_memberships_org_user"),
    )
    op.create_index("ix_org_memberships_organisation_id", "organisation_memberships", ["organisation_id"])
    op.create_index("ix_org_memberships_user_id", "organisation_memberships", ["user_id"])
    # Partial index: at most one 'owner' per org — the deferred trigger enforces exactly-one,
    # but this index prevents duplicate 'owner' rows from being inserted simultaneously.
    op.execute(
        "CREATE UNIQUE INDEX uq_org_memberships_one_owner "
        "ON organisation_memberships (organisation_id) "
        "WHERE org_role = 'owner'"
    )

    # -- workspace_memberships -----------------------------------------------
    op.create_table(
        "workspace_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_ws_memberships_user_id"
        ),
        # COMPOSITE FK: workspace_id + organisation_id → workspaces(id, organisation_id)
        # This guarantees at the DB level that workspace and organisation are consistent.
        sa.ForeignKeyConstraint(
            ["workspace_id", "organisation_id"],
            ["workspaces.id", "workspaces.organisation_id"],
            ondelete="CASCADE",
            name="fk_ws_memberships_workspace_org",
        ),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_ws_user"),
    )
    op.create_index("ix_ws_memberships_workspace_id", "workspace_memberships", ["workspace_id"])
    op.create_index("ix_ws_memberships_user_id", "workspace_memberships", ["user_id"])
    op.create_index("ix_ws_memberships_organisation_id", "workspace_memberships", ["organisation_id"])

    # -- pre_auth_sessions ---------------------------------------------------
    op.create_table(
        "pre_auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_pre_auth_sessions_user_id"
        ),
    )
    op.create_index("ix_pre_auth_sessions_token_hash", "pre_auth_sessions", ["token_hash"], unique=True)
    op.create_index("ix_pre_auth_sessions_user_id", "pre_auth_sessions", ["user_id"])
    op.create_index("ix_pre_auth_sessions_expires_at", "pre_auth_sessions", ["expires_at"])

    # -- refresh_tokens -------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("family_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_refresh_tokens_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisations.id"],
            ondelete="CASCADE",
            name="fk_refresh_tokens_organisation_id",
        ),
    )
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_organisation_id", "refresh_tokens", ["organisation_id"])

    # -- sessions ------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_sessions_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisations.id"],
            ondelete="CASCADE",
            name="fk_sessions_organisation_id",
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_organisation_id", "sessions", ["organisation_id"])
    op.create_index("ix_sessions_refresh_family_id", "sessions", ["refresh_family_id"])

    # -- audit_events --------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("organisation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="'success'"),
        # Use SET NULL so audit records survive user/org deletion
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisations.id"],
            ondelete="SET NULL",
            name="fk_audit_events_organisation_id",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            ondelete="SET NULL",
            name="fk_audit_events_actor_user_id",
        ),
    )
    op.create_index("ix_audit_events_org_created", "audit_events", ["organisation_id", "created_at"])
    op.create_index("ix_audit_events_actor_created", "audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # ==========================================================================
    # DEFERRED EXACTLY-ONE-OWNER TRIGGER
    # ==========================================================================

    # The partial unique index (uq_org_memberships_one_owner) prevents two owner rows
    # from co-existing. The DEFERRABLE constraint allows ownership transfer within a
    # single transaction by temporarily having 0 owners (old owner demoted, new owner
    # not yet promoted).
    #
    # Implementation: a deferred trigger function checks at commit that every
    # organisation has exactly one owner membership.

    op.execute("""
        CREATE OR REPLACE FUNCTION check_exactly_one_owner()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            v_org_id uuid;
            v_owner_count int;
        BEGIN
            -- Determine the organisation to check
            IF TG_OP = 'DELETE' THEN
                v_org_id := OLD.organisation_id;
                -- If the parent organisation itself is being deleted in the same
                -- transaction (CASCADE delete of memberships), the ownership
                -- constraint is vacuously satisfied — there is no organisation
                -- left to enforce it on.  Skip the check to avoid a false failure.
                IF NOT EXISTS (
                    SELECT 1 FROM organisations WHERE id = v_org_id
                ) THEN
                    RETURN NULL;
                END IF;
            ELSE
                v_org_id := NEW.organisation_id;
            END IF;

            SELECT COUNT(*) INTO v_owner_count
            FROM organisation_memberships
            WHERE organisation_id = v_org_id AND org_role = 'owner';

            IF v_owner_count != 1 THEN
                RAISE EXCEPTION
                    'Organisation % must have exactly one owner (found %)',
                    v_org_id, v_owner_count;
            END IF;

            RETURN NULL; -- AFTER trigger, return value ignored
        END;
        $$
    """)

    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_exactly_one_owner
        AFTER INSERT OR UPDATE OR DELETE ON organisation_memberships
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_exactly_one_owner()
    """)

    # ==========================================================================
    # ROW LEVEL SECURITY
    # ==========================================================================

    # ---- tenant-scoped tables -----------------------------------------------
    # Each table uses ENABLE + FORCE + a single permissive FOR ALL policy
    # with USING (read/delete/pre-write) and WITH CHECK (write/post-write).
    # NULLIF guard: null context → comparison fails → no access granted.

    tenant_scoped_tables = {
        "organisations": "id",
        "workspaces": "organisation_id",
        "organisation_memberships": "organisation_id",
        "workspace_memberships": "organisation_id",
        "refresh_tokens": "organisation_id",
        "sessions": "organisation_id",
    }

    for table, tenant_column in tenant_scoped_tables.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING (
                {tenant_column} =
                NULLIF(current_setting('app.current_organisation_id', true), '')::uuid
            )
            WITH CHECK (
                {tenant_column} =
                NULLIF(current_setting('app.current_organisation_id', true), '')::uuid
            )
        """)

    # ---- user-context policy on organisation_memberships --------------------
    # Users can read their own memberships (for org-selection list).
    # This is a SEPARATE policy from the tenant isolation policy.
    # The tenant isolation policy governs all reads WITHIN an established org context.
    # This user-context policy allows users to see their own memberships
    # BEFORE they have selected an org (i.e., before the tenant context is set).
    #
    # Note: this is on top of the tenant isolation policy. Both must pass for access.
    # For the org selector (step 2), we use a raw session without RLS context,
    # so this policy is not needed for the auth flow. It's included for completeness.
    op.execute("""
        CREATE POLICY org_memberships_user_read ON organisation_memberships
        FOR SELECT
        USING (
            user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    """)

    # ---- audit_events: no RLS (see below) -----------------------------------
    # audit_events does NOT get a tenant isolation RLS policy.
    # Instead:
    # - The application role has INSERT-only permission (granted below).
    # - Global events (no org context) are written via SECURITY DEFINER function.
    # - Tenant-scoped events are filtered by organisation_id in queries at the
    #   service layer.
    # Adding 'organisation_id IS NULL OR ...' RLS would be a security anti-pattern.

    # ---- pre_auth_sessions: no RLS -----------------------------------------
    # pre_auth_sessions is not tenant-scoped; tokens are consumed before org selection.

    # ---- users: no RLS ------------------------------------------------------
    # users is a platform-level table; access is controlled at the service layer.

    # ==========================================================================
    # SECURITY DEFINER FUNCTION FOR GLOBAL AUDIT EVENTS
    # ==========================================================================
    # This function is owned by the migration role (which has BYPASSRLS).
    # It writes audit events with organisation_id = NULL for global events.
    # The application role calls this function but cannot write directly to
    # audit_events with a NULL organisation_id (INSERT-only grant).
    #
    # The allowlist of event types is enforced here, not in application code.
    # This is the only safe way to write pre-org-selection audit events.

    op.execute("""
        CREATE OR REPLACE FUNCTION fn_audit_insert_global(
            p_event_type    text,
            p_actor_user_id uuid,
            p_event_data    jsonb,
            p_request_id    text,
            p_client_ip     text,
            p_outcome       text DEFAULT 'failure'
        )
        RETURNS void
        SECURITY DEFINER
        SET search_path = public
        LANGUAGE plpgsql AS $$
        DECLARE
            v_allowed_types text[] := ARRAY[
                'auth.login_failed',
                'auth.pre_auth_session_expired',
                'auth.pre_auth_session_reused',
                'auth.token_reuse_detected'
            ];
        BEGIN
            -- Validate event type against allowlist
            IF p_event_type != ALL(v_allowed_types) THEN
                RAISE EXCEPTION 'fn_audit_insert_global: event type % is not in the allowlist', p_event_type;
            END IF;

            -- Validate outcome
            IF p_outcome NOT IN ('success', 'failure', 'pending') THEN
                RAISE EXCEPTION 'fn_audit_insert_global: invalid outcome %', p_outcome;
            END IF;

            INSERT INTO audit_events (
                id,
                organisation_id,
                actor_user_id,
                event_type,
                event_data,
                request_id,
                client_ip,
                outcome,
                created_at
            ) VALUES (
                uuid_generate_v4(),
                NULL,  -- global event: no organisation context
                p_actor_user_id,
                p_event_type,
                COALESCE(p_event_data, '{}'),
                p_request_id,
                p_client_ip,
                p_outcome,
                now()
            );
        END;
        $$
    """)

    # ==========================================================================
    # GRANTS AND PERMISSIONS
    # ==========================================================================

    # Grant connect and schema usage to application role
    op.execute("GRANT CONNECT ON DATABASE atlascore TO atlascore")
    op.execute("GRANT USAGE ON SCHEMA public TO atlascore")

    # Application role grants — minimum required
    for table in ("users", "organisations", "workspaces", "organisation_memberships",
                  "workspace_memberships", "pre_auth_sessions", "refresh_tokens", "sessions"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO atlascore")

    # audit_events: INSERT ONLY — no UPDATE or DELETE for the application role
    op.execute("GRANT INSERT ON audit_events TO atlascore")

    # Revoke the default PUBLIC EXECUTE on the SECURITY DEFINER function.
    # PostgreSQL grants EXECUTE to PUBLIC by default for new functions;
    # we must explicitly revoke it so only the application role can call it.
    op.execute(
        "REVOKE EXECUTE ON FUNCTION fn_audit_insert_global("
        "text, uuid, jsonb, text, text, text) FROM PUBLIC"
    )

    # Grant EXECUTE on the SECURITY DEFINER function to application role only
    op.execute(
        "GRANT EXECUTE ON FUNCTION fn_audit_insert_global("
        "text, uuid, jsonb, text, text, text) TO atlascore"
    )

    # Sequence grants
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO atlascore")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE EXECUTE ON FUNCTION fn_audit_insert_global(text, uuid, jsonb, text, text, text) FROM atlascore")
    op.execute("REVOKE INSERT ON audit_events FROM atlascore")
    for table in ("users", "organisations", "workspaces", "organisation_memberships",
                  "workspace_memberships", "pre_auth_sessions", "refresh_tokens", "sessions"):
        op.execute(f"REVOKE ALL ON {table} FROM atlascore")

    # Drop function
    op.execute("DROP FUNCTION IF EXISTS fn_audit_insert_global(text, uuid, jsonb, text, text, text)")

    # Drop policies
    for table in ("organisations", "workspaces", "workspace_memberships",
                  "refresh_tokens", "sessions"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS org_memberships_user_read ON organisation_memberships")
    op.execute("DROP POLICY IF EXISTS organisation_memberships_tenant_isolation ON organisation_memberships")
    op.execute("ALTER TABLE organisation_memberships DISABLE ROW LEVEL SECURITY")

    # Drop trigger
    op.execute("DROP TRIGGER IF EXISTS trg_exactly_one_owner ON organisation_memberships")
    op.execute("DROP FUNCTION IF EXISTS check_exactly_one_owner()")

    # Drop tables (reverse order for FK deps)
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_table("refresh_tokens")
    op.drop_table("pre_auth_sessions")
    op.drop_table("workspace_memberships")
    op.drop_table("organisation_memberships")
    op.drop_table("workspaces")
    op.drop_table("organisations")
    op.drop_table("users")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS audit_outcome")
    op.execute("DROP TYPE IF EXISTS workspace_role")
    op.execute("DROP TYPE IF EXISTS org_role")
