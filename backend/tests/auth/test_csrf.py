"""
Tests for CSRFService.

Coverage:
- generate_token() is deterministic for a given family_id
- generate_token() is unique for different family_ids
- verify() returns True when header matches the expected HMAC
- verify() returns False when the header is wrong
- verify() returns False when the header is missing
- validate_origin() returns True for an allowed origin
- validate_origin() returns False for a disallowed origin
- validate_origin() returns True when Origin header is absent (non-browser)
- CSRF token is stable across access token rotations (same family_id)
"""

from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from app.auth.csrf import CSRFService
from app.core.config import Settings


@pytest.fixture()
def svc(settings: Settings) -> CSRFService:
    return CSRFService(settings)


def test_generate_token_deterministic(svc: CSRFService) -> None:
    family_id = str(uuid.uuid4())
    t1 = svc.generate_token(family_id)
    t2 = svc.generate_token(family_id)
    assert t1 == t2


def test_generate_token_unique_per_family_id(svc: CSRFService) -> None:
    t1 = svc.generate_token(str(uuid.uuid4()))
    t2 = svc.generate_token(str(uuid.uuid4()))
    assert t1 != t2


def test_csrf_stable_across_access_token_rotations(svc: CSRFService) -> None:
    """
    The CSRF token bound to a family_id must be identical for all access
    tokens in the same login session (same family_id, different jti).
    This verifies the Phase 1B jti correction: switching context does not
    invalidate CSRF protection.
    """
    family_id = str(uuid.uuid4())
    # Simulate three different access tokens issued for the same session
    csrf1 = svc.generate_token(family_id)
    csrf2 = svc.generate_token(family_id)
    csrf3 = svc.generate_token(family_id)
    assert csrf1 == csrf2 == csrf3


def _make_request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_verify_correct_header(svc: CSRFService) -> None:
    family_id = str(uuid.uuid4())
    token = svc.generate_token(family_id)
    request = _make_request({"x-csrf-token": token})
    assert svc.verify(request, family_id) is True


def test_verify_wrong_header(svc: CSRFService) -> None:
    family_id = str(uuid.uuid4())
    request = _make_request({"x-csrf-token": "totallywrong"})
    assert svc.verify(request, family_id) is False


def test_verify_missing_header(svc: CSRFService) -> None:
    family_id = str(uuid.uuid4())
    request = _make_request({})
    assert svc.verify(request, family_id) is False


def test_verify_wrong_family_id_rejected(svc: CSRFService) -> None:
    """CSRF token for family A must not validate for family B."""
    family_a = str(uuid.uuid4())
    family_b = str(uuid.uuid4())
    token_a = svc.generate_token(family_a)
    request = _make_request({"x-csrf-token": token_a})
    # Token was generated for family_a, verifying against family_b must fail
    assert svc.verify(request, family_b) is False


def test_validate_origin_allowed(svc: CSRFService) -> None:
    request = _make_request({"origin": "http://localhost:3100"})
    assert svc.validate_origin(request, ["http://localhost:3100"]) is True


def test_validate_origin_disallowed(svc: CSRFService) -> None:
    request = _make_request({"origin": "https://evil.example.com"})
    assert svc.validate_origin(request, ["http://localhost:3100"]) is False


def test_validate_origin_absent_is_allowed(svc: CSRFService) -> None:
    """Non-browser clients (curl, SDK) omit Origin — should pass."""
    request = _make_request({})
    assert svc.validate_origin(request, ["http://localhost:3100"]) is True
