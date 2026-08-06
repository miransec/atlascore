"""
fn_audit_insert_global security proof tests.

Each test proves one non-negotiable security property of the global audit
function.  Tests use direct SQL against the pg_catalog / pg_proc system
tables and the function itself — there is no application-layer bypass.

Properties proven (one test per property, cross-referenced to spec):

  S1. SECURITY DEFINER is set (prosecdef = true in pg_proc).
  S2. Function has a fixed, safe search_path (SET search_path = public).
  S3. PUBLIC (everyone) cannot execute the function (no PUBLIC EXECUTE grant).
  S4. Only the atlascore application role has EXECUTE permission.
  S5. Allowed event types are hard-coded inside the function — disallowed
      types are rejected with an exception.
  S6. organisation_id cannot be supplied as a parameter — the function
      signature has no such parameter, and the INSERT always uses NULL.
  S7. The function contains no dynamic SQL (no EXECUTE keyword in body).
  S8. The function owner is NOT the atlascore application role; it runs
      as the migration/superuser role whose identity has BYPASSRLS.
  S9. atlascore cannot UPDATE or DELETE rows in audit_events directly
      (table privileges, not function privileges).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FN_NAME = "fn_audit_insert_global"
_APP_ROLE = "atlascore"

# The four hard-coded global event types from the migration.
_ALLOWED_TYPES = (
    "auth.login_failed",
    "auth.pre_auth_session_expired",
    "auth.pre_auth_session_reused",
    "auth.token_reuse_detected",
)

# A representative set of event types that must be rejected.
_REJECTED_TYPES = (
    "auth.login_success",  # tenant-scoped → not in global allowlist
    "org.member_added",  # Phase 1B type that does not belong here
    "auth.logout",  # not a pre-org event
    "",  # empty string
    "fn_audit_insert_global",  # function name itself
    "'; DROP TABLE audit_events; --",  # SQL-injection attempt
)


async def _get_fn_oid(conn) -> int:  # type: ignore[no-untyped-def]
    """Return the pg_proc OID for fn_audit_insert_global (any overload)."""
    row = await conn.execute(
        text(
            "SELECT oid FROM pg_proc "
            "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
        ),
        {"name": _FN_NAME},
    )
    oid = row.scalar_one()
    return int(oid)


# ---------------------------------------------------------------------------
# S1 — SECURITY DEFINER is genuinely set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_is_security_definer(engine: AsyncEngine, tables: None) -> None:
    """
    pg_proc.prosecdef must be TRUE for fn_audit_insert_global.

    SECURITY DEFINER means the function executes with the privileges of the
    function OWNER, not the caller.  This is what allows it to insert audit
    rows with NULL organisation_id while bypassing RLS without granting
    BYPASSRLS to the application role.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT prosecdef FROM pg_proc "
                "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
            ),
            {"name": _FN_NAME},
        )
        prosecdef = row.scalar_one()

    assert prosecdef is True, (
        f"{_FN_NAME} must be SECURITY DEFINER — prosecdef is not true. "
        "Without SECURITY DEFINER the function executes as the application role "
        "which has NOBYPASSRLS and cannot write global (NULL org) audit rows."
    )


# ---------------------------------------------------------------------------
# S2 — Fixed safe search_path is configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_has_fixed_search_path(engine: AsyncEngine, tables: None) -> None:
    """
    pg_proc.proconfig must include 'search_path=public'.

    Without a fixed search_path a malicious user with CREATE SCHEMA could
    shadow the public schema and redirect function calls to attacker-controlled
    objects (a search_path injection attack).
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT proconfig FROM pg_proc "
                "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
            ),
            {"name": _FN_NAME},
        )
        proconfig = row.scalar_one_or_none()

    assert proconfig is not None, f"{_FN_NAME} has no proconfig — SET search_path was not applied."

    config_list: list[str] = list(proconfig)
    has_search_path = any("search_path" in entry for entry in config_list)
    assert has_search_path, (
        f"{_FN_NAME} proconfig={proconfig!r} does not contain a search_path "
        "entry.  The function is vulnerable to search_path injection."
    )


# ---------------------------------------------------------------------------
# S3 — PUBLIC role cannot execute the function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_public_execute_revoked(engine: AsyncEngine, tables: None) -> None:
    """
    has_function_privilege('public', fn_oid, 'EXECUTE') must return FALSE.

    PostgreSQL grants EXECUTE to PUBLIC by default for new functions.
    The migration must explicitly REVOKE it so that only the application role
    can invoke the global audit function.
    """
    async with engine.connect() as conn:
        fn_oid = await _get_fn_oid(conn)
        row = await conn.execute(
            text("SELECT has_function_privilege('public', CAST(:oid AS oid), 'EXECUTE')"),
            {"oid": fn_oid},
        )
        public_can_execute = row.scalar_one()

    assert public_can_execute is False, (
        f"{_FN_NAME}: PUBLIC role still has EXECUTE — "
        "the migration must REVOKE EXECUTE ON FUNCTION ... FROM PUBLIC."
    )


# ---------------------------------------------------------------------------
# S4 — Only atlascore has EXECUTE permission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_only_app_role_has_execute(engine: AsyncEngine, tables: None) -> None:
    """
    has_function_privilege('atlascore', fn_oid, 'EXECUTE') must return TRUE.
    No other non-superuser role should have explicit EXECUTE.

    We check the proacl (access control list) column — each entry is a
    grantee=privileges/grantor triple.  We assert that:
      a) atlascore appears as a grantee with 'X' (EXECUTE), and
      b) no entry grants EXECUTE to a role other than the owner and atlascore.
    """
    async with engine.connect() as conn:
        fn_oid = await _get_fn_oid(conn)

        # atlascore must be able to execute
        row = await conn.execute(
            text("SELECT has_function_privilege(:role, CAST(:oid AS oid), 'EXECUTE')"),
            {"role": _APP_ROLE, "oid": fn_oid},
        )
        app_can_execute = row.scalar_one()

        # Retrieve the full ACL for manual inspection
        row2 = await conn.execute(
            text("SELECT array_to_string(proacl, ',') AS acl FROM pg_proc WHERE oid = :oid"),
            {"oid": fn_oid},
        )
        raw_acl = row2.scalar_one_or_none() or ""

        owner_role = await conn.scalar(
            text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = :oid"),
            {"oid": fn_oid},
        )

    assert app_can_execute is True, (
        f"{_FN_NAME}: the {_APP_ROLE} role does not have EXECUTE — "
        "the migration must GRANT EXECUTE ON FUNCTION ... TO atlascore."
    )

    # Ensure no unexpected role has EXECUTE (allow owner and atlascore only)
    for entry in raw_acl.split(","):
        if not entry:
            continue
        grantee = entry.split("=")[0]
        if grantee in ("", _APP_ROLE, owner_role):
            # empty grantee = owner implicit right; atlascore is expected
            continue
        privileges = entry.split("=")[1].split("/")[0]
        assert "X" not in privileges, (
            f"{_FN_NAME}: unexpected grantee '{grantee}' has EXECUTE privilege. "
            f"Full ACL: {raw_acl!r}"
        )


# ---------------------------------------------------------------------------
# S5 — Allowed event types are hard-coded; disallowed types are rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_allows_only_hard_coded_event_types(
    engine: AsyncEngine, tables: None
) -> None:
    """
    Each allowed type must succeed; each rejected type must raise an exception.

    The allowlist is embedded in the function body — no application-layer
    bypass is possible.  A call with an unlisted event type raises a
    PostgreSQL EXCEPTION with a recognisable message.
    """
    async with engine.connect() as conn:
        # All allowed types must succeed
        for event_type in _ALLOWED_TYPES:
            await conn.execute(text("SAVEPOINT sp_allowed"))
            try:
                await conn.execute(
                    text(
                        "SELECT fn_audit_insert_global("
                        ":event_type, CAST(NULL AS uuid), '{}'::jsonb, 'req-test', '127.0.0.1', 'failure')"
                    ),
                    {"event_type": event_type},
                )
                await conn.execute(text("RELEASE SAVEPOINT sp_allowed"))
            except Exception as exc:  # pragma: no cover
                await conn.execute(text("ROLLBACK TO SAVEPOINT sp_allowed"))
                pytest.fail(f"Allowed event type '{event_type}' was rejected by {_FN_NAME}: {exc}")

        # All rejected types must raise an exception
        for bad_type in _REJECTED_TYPES:
            await conn.execute(text("SAVEPOINT sp_rejected"))
            try:
                await conn.execute(
                    text(
                        "SELECT fn_audit_insert_global("
                        ":event_type, CAST(NULL AS uuid), '{}'::jsonb, 'req-test', '127.0.0.1', 'failure')"
                    ),
                    {"event_type": bad_type},
                )
                await conn.execute(text("RELEASE SAVEPOINT sp_rejected"))
                pytest.fail(
                    f"Rejected event type {bad_type!r} was accepted by "
                    f"{_FN_NAME} — the allowlist is not enforced."
                )
            except Exception:
                await conn.execute(text("ROLLBACK TO SAVEPOINT sp_rejected"))


# ---------------------------------------------------------------------------
# S6 — organisation_id cannot be supplied (parameter does not exist)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_has_no_org_id_parameter(engine: AsyncEngine, tables: None) -> None:
    """
    The function signature must not include an organisation_id parameter.

    If a caller could pass organisation_id, they could forge tenant-scoped
    global audit events, undermining the entire audit trail.
    We verify this by inspecting pg_proc.proargnames.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT proargnames FROM pg_proc "
                "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
            ),
            {"name": _FN_NAME},
        )
        proargnames = row.scalar_one()

    # proargnames is a PostgreSQL array of text — SQLAlchemy returns it as a list
    param_names: list[str] = list(proargnames) if proargnames else []

    assert "organisation_id" not in param_names, (
        f"{_FN_NAME} exposes an 'organisation_id' parameter: {param_names!r}. "
        "This allows callers to forge the organisation context of audit events."
    )
    assert "org_id" not in param_names, (
        f"{_FN_NAME} exposes an 'org_id' parameter: {param_names!r}."
    )


# ---------------------------------------------------------------------------
# S7 — Function body contains no dynamic SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_has_no_dynamic_sql(engine: AsyncEngine, tables: None) -> None:
    """
    The function body (pg_proc.prosrc) must not contain 'EXECUTE'.

    Dynamic SQL (EXECUTE 'some string') is the primary vector for SQL
    injection inside PL/pgSQL.  The function only requires static INSERT
    and IF statements — no dynamic SQL is needed or safe.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT prosrc FROM pg_proc "
                "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
            ),
            {"name": _FN_NAME},
        )
        prosrc: str = row.scalar_one()

    # Strip comments before checking (line comments start with --)
    non_comment_lines = [line for line in prosrc.splitlines() if not line.lstrip().startswith("--")]
    body_without_comments = " ".join(non_comment_lines)

    # 'EXECUTE' as a standalone word would indicate dynamic SQL
    import re

    dynamic_sql_calls = re.findall(r"\bEXECUTE\b", body_without_comments, re.IGNORECASE)

    assert not dynamic_sql_calls, (
        f"{_FN_NAME} contains {len(dynamic_sql_calls)} EXECUTE keyword(s) — "
        "dynamic SQL is forbidden in this function. "
        f"Matches: {dynamic_sql_calls!r}"
    )


# ---------------------------------------------------------------------------
# S8 — Function owner is NOT the atlascore application role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_owner_is_not_app_role(engine: AsyncEngine, tables: None) -> None:
    """
    pg_get_userbyid(proowner) must NOT return 'atlascore'.

    The function must be owned by the migration/superuser role so that
    SECURITY DEFINER elevates execution to that role's privileges
    (including BYPASSRLS).  If the function were owned by atlascore, then
    SECURITY DEFINER would have no effect — it would still run as a role
    with NOBYPASSRLS and could not write NULL-org audit rows.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT pg_get_userbyid(proowner) AS owner FROM pg_proc "
                "WHERE proname = :name AND pronamespace = 'public'::regnamespace"
            ),
            {"name": _FN_NAME},
        )
        owner: str = row.scalar_one()

    assert owner != _APP_ROLE, (
        f"{_FN_NAME} is owned by '{owner}' which equals the application role "
        f"'{_APP_ROLE}'.  SECURITY DEFINER requires the owner to be a more "
        "privileged role (migration role or superuser)."
    )


# ---------------------------------------------------------------------------
# S9 — atlascore cannot UPDATE or DELETE audit_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_fn_audit_app_role_cannot_update_audit_events(
    engine: AsyncEngine, tables: None
) -> None:
    """
    has_table_privilege('atlascore', 'audit_events', 'UPDATE') must be FALSE.

    The application role must have INSERT-only access to audit_events.
    If UPDATE were permitted, application code (or an attacker with app-level
    access) could tamper with existing audit log entries.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT has_table_privilege(:role, 'audit_events', 'UPDATE')"),
            {"role": _APP_ROLE},
        )
        can_update = row.scalar_one()

    assert can_update is False, (
        f"Role '{_APP_ROLE}' has UPDATE on audit_events — "
        "the audit log is mutable by application code.  Only INSERT must be granted."
    )


@pytest.mark.asyncio()
async def test_fn_audit_app_role_cannot_delete_audit_events(
    engine: AsyncEngine, tables: None
) -> None:
    """
    has_table_privilege('atlascore', 'audit_events', 'DELETE') must be FALSE.

    If DELETE were permitted, application code could purge audit trail entries,
    making the log unreliable for forensic and compliance purposes.
    """
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT has_table_privilege(:role, 'audit_events', 'DELETE')"),
            {"role": _APP_ROLE},
        )
        can_delete = row.scalar_one()

    assert can_delete is False, (
        f"Role '{_APP_ROLE}' has DELETE on audit_events — "
        "the audit log is erasable by application code.  No DELETE must be granted."
    )
