"""
Tests for AuditService.

Coverage (Phase 1A — 7 scenarios):
  1.  emit_transactional() adds an AuditEvent to the session
  2.  emit_transactional() sanitises sensitive keys from event_data
  3.  emit_transactional() rejects event types not in ALL_EVENT_TYPES
  4.  emit_independent() calls fn_audit_insert_global for allowed event types
  5.  emit_independent() rejects event types not in GLOBAL_EVENT_TYPES
  6.  _sanitise_event_data() redacts all sensitive keys
  7.  _sanitise_event_data() preserves non-sensitive keys

§11 Phase 1B audit event tests (additional scenarios):
  8.  invitation.expired is NOT in GLOBAL_EVENT_TYPES (§10 fix)
  9.  emit_transactional() accepts all Phase 1B invitation event types
  10. emit_transactional() accepts all Phase 1B team event types
  11. emit_transactional() accepts all Phase 1B service account event types
  12. emit_transactional() accepts all Phase 1B api_key event types
  13. _sanitise_event_data redacts raw_key, secret_hash, pepper
  14. emit_independent() rejects invitation.expired (must be transactional)
  15. GLOBAL_EVENT_TYPES has exactly 4 members (Phase 1A only)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditEvent
from app.services.audit import (
    GLOBAL_EVENT_TYPES,
    AuditService,
    _sanitise_event_data,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test 1 & 2: emit_transactional()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_emit_transactional_adds_event(
    audit_service: AuditService,
    db: AsyncSession,
) -> None:
    org_id = uuid.uuid4()
    audit_service.emit_transactional(
        session=db,
        event_type="auth.logout",
        organisation_id=org_id,
        actor_user_id=uuid.uuid4(),
        event_data={"reason": "user_request"},
        request_id="req-audit-1",
        client_ip="127.0.0.1",
        outcome="success",
    )
    # The event is added to the session but NOT yet committed.
    # We can still introspect via session.new.
    new_objs = [o for o in db.new if isinstance(o, AuditEvent)]
    assert any(e.event_type == "auth.logout" for e in new_objs)


@pytest.mark.asyncio()
async def test_emit_transactional_sanitises_event_data(
    audit_service: AuditService,
    db: AsyncSession,
) -> None:
    audit_service.emit_transactional(
        session=db,
        event_type="auth.logout",
        organisation_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        event_data={"password": "should-be-redacted", "email": "kept@example.com"},
        request_id="req-sanitise",
        client_ip="127.0.0.1",
        outcome="success",
    )
    new_objs = [o for o in db.new if isinstance(o, AuditEvent)]
    event = next(e for e in new_objs if e.event_type == "auth.logout")
    data = event.event_data
    assert data.get("password") == "[REDACTED]"
    assert data.get("email") == "kept@example.com"


@pytest.mark.asyncio()
async def test_emit_transactional_rejects_unknown_event_type(
    audit_service: AuditService,
    db: AsyncSession,
) -> None:
    with pytest.raises(ValueError, match="Unknown audit event type|unknown event type"):
        audit_service.emit_transactional(
            session=db,
            event_type="evil.injection",
            organisation_id=uuid.uuid4(),
            actor_user_id=None,
            event_data={},
            request_id="req-bad",
            client_ip="127.0.0.1",
            outcome="failure",
        )


# ---------------------------------------------------------------------------
# Test 4 & 5: emit_independent() (requires DB for fn_audit_insert_global)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_emit_independent_allowed_event(
    audit_service: AuditService,
    raw_db: AsyncSession,
) -> None:
    # Just ensure no exception is raised.
    await audit_service.emit_independent(
        session=raw_db,
        event_type="auth.login_failed",
        actor_user_id=None,
        event_data={"email": "hacker@example.com"},
        request_id="req-global-1",
        client_ip="127.0.0.1",
        outcome="failure",
    )


@pytest.mark.asyncio()
async def test_emit_independent_rejects_non_global_event(
    audit_service: AuditService,
    raw_db: AsyncSession,
) -> None:
    with pytest.raises(
        ValueError, match="not allowed as a global audit event|not a global event type"
    ):
        await audit_service.emit_independent(
            session=raw_db,
            event_type="auth.logout",  # transactional, not global
            actor_user_id=None,
            event_data={},
            request_id="req-bad-global",
            client_ip="127.0.0.1",
            outcome="success",
        )


# ---------------------------------------------------------------------------
# Test 6 & 7: _sanitise_event_data()
# ---------------------------------------------------------------------------


def test_sanitise_redacts_sensitive_keys() -> None:
    data = {
        "password": "secret",
        "token": "abc123",
        "raw_token": "raw",
        "access_token": "jwt",
        "refresh_token": "rt",
        "csrf_token": "csrf",
        "secret": "shhh",
        "api_key": "key123",
    }
    result = _sanitise_event_data(data)
    for key in data:
        assert result[key] == "[REDACTED]", f"Expected {key} to be redacted"


def test_sanitise_preserves_non_sensitive_keys() -> None:
    data = {
        "email": "user@example.com",
        "organisation_id": "some-uuid",
        "outcome": "success",
        "request_id": "req-123",
    }
    result = _sanitise_event_data(data)
    assert result == data


def test_sanitise_handles_nested_dict() -> None:
    """Only top-level keys are sanitised — nested structures are preserved as-is."""
    data = {"meta": {"password": "nested"}, "password": "top-level"}
    result = _sanitise_event_data(data)
    assert result["password"] == "[REDACTED]"
    # Nested is NOT redacted (by design — shallow sanitise).
    assert result["meta"] == {"password": "nested"}


# ---------------------------------------------------------------------------
# §11 Phase 1B audit event tests (scenarios 8-15)
# ---------------------------------------------------------------------------


def test_invitation_expired_not_in_global_types() -> None:
    """§10 fix: invitation.expired must NOT be in GLOBAL_EVENT_TYPES (scenario 8)."""
    assert "invitation.expired" not in GLOBAL_EVENT_TYPES, (
        "invitation.expired is always emitted transactionally with org context. "
        "It must not be a global event type."
    )


def test_global_event_types_has_exactly_four_members() -> None:
    """GLOBAL_EVENT_TYPES should have exactly the 4 Phase 1A auth events (scenario 15)."""
    expected = {
        "auth.login_failed",
        "auth.pre_auth_session_expired",
        "auth.pre_auth_session_reused",
        "auth.token_reuse_detected",
    }
    assert expected == GLOBAL_EVENT_TYPES, (
        f"GLOBAL_EVENT_TYPES should be exactly {expected!r}, got {GLOBAL_EVENT_TYPES!r}"
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "invitation.created",
        "invitation.revoked",
        "invitation.accepted",
        "invitation.expired",
    ],
)
@pytest.mark.asyncio()
async def test_phase1b_invitation_event_types_accepted(
    audit_service: AuditService,
    db: AsyncSession,
    event_type: str,
) -> None:
    """emit_transactional() accepts all Phase 1B invitation event types (scenario 9)."""
    org_id = uuid.uuid4()
    # Should not raise ValueError
    audit_service.emit_transactional(
        session=db,
        event_type=event_type,
        organisation_id=org_id,
        actor_user_id=uuid.uuid4(),
        event_data={"invitation_id": str(uuid.uuid4())},
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "team.created",
        "team.updated",
        "team.deleted",
        "team.member_added",
        "team.member_removed",
    ],
)
@pytest.mark.asyncio()
async def test_phase1b_team_event_types_accepted(
    audit_service: AuditService,
    db: AsyncSession,
    event_type: str,
) -> None:
    """emit_transactional() accepts all Phase 1B team event types (scenario 10)."""
    audit_service.emit_transactional(
        session=db,
        event_type=event_type,
        organisation_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        event_data={},
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "service_account.created",
        "service_account.disabled",
        "service_account.enabled",
        "api_key.created",
        "api_key.revoked",
        "api_key.rotated",
        "api_key.used",
    ],
)
@pytest.mark.asyncio()
async def test_phase1b_sa_apikey_event_types_accepted(
    audit_service: AuditService,
    db: AsyncSession,
    event_type: str,
) -> None:
    """emit_transactional() accepts all Phase 1B service account and API key types (scenarios 11-12)."""
    audit_service.emit_transactional(
        session=db,
        event_type=event_type,
        organisation_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        event_data={},
    )


def test_sanitise_redacts_api_key_secrets() -> None:
    """raw_key, secret_hash, pepper are all redacted (scenario 13)."""
    data = {
        "raw_key": "atk_prefix_secret",
        "secret_hash": "abc123",
        "pepper": "verysecret",
        "api_key": "full-key",
        "key_hash": "hash",
        "service_account_id": "some-uuid",  # not sensitive
    }
    result = _sanitise_event_data(data)
    assert result["raw_key"] == "[REDACTED]"
    assert result["secret_hash"] == "[REDACTED]"
    assert result["pepper"] == "[REDACTED]"
    assert result["api_key"] == "[REDACTED]"
    assert result["key_hash"] == "[REDACTED]"
    assert result["service_account_id"] == "some-uuid"  # preserved


@pytest.mark.asyncio()
async def test_emit_independent_rejects_invitation_expired(
    audit_service: AuditService,
    raw_db: AsyncSession,
) -> None:
    """emit_independent() must reject invitation.expired (it must be transactional) (scenario 14)."""
    with pytest.raises(ValueError, match="not a global event type|not allowed"):
        await audit_service.emit_independent(
            session=raw_db,
            event_type="invitation.expired",
            actor_user_id=uuid.uuid4(),
            event_data={"invitation_id": str(uuid.uuid4())},
            outcome="failure",
        )
