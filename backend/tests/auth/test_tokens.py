"""
Tests for JWTService (access token).

Coverage:
- issue() returns a non-empty JWT string
- verify() decodes a freshly issued token correctly
- verify() raises ExpiredSignatureError for an expired token
- verify() raises PyJWTError for a tampered signature
- verify() raises PyJWTError for a token signed with a different key
- payload contains expected claims: sub, org, role, jti, fid, iss, iat, exp
- jti is unique per issue() call (RFC 7519 §4.1.7)
- family_id round-trips correctly (CSRF binding claim)
- workspace claims are optional and round-trip when supplied
"""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest

from app.auth.tokens import JWTService, TokenPayload
from app.core.config import Settings


@pytest.fixture()
def svc(settings: Settings) -> JWTService:
    return JWTService(settings)


def test_issue_returns_string(svc: JWTService) -> None:
    token = svc.issue(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        org_role="owner",
        family_id=str(uuid.uuid4()),
    )
    assert isinstance(token, str)
    assert len(token) > 0


def test_verify_round_trips(svc: JWTService) -> None:
    uid = uuid.uuid4()
    oid = uuid.uuid4()
    fid = str(uuid.uuid4())
    token = svc.issue(user_id=uid, organisation_id=oid, org_role="analyst", family_id=fid)
    payload: TokenPayload = svc.verify(token)

    assert payload.user_id == uid
    assert payload.organisation_id == oid
    assert payload.org_role == "analyst"
    assert payload.family_id == fid
    # jti is not caller-supplied; just verify it's a non-empty string
    assert isinstance(payload.jti, str)
    assert len(payload.jti) > 0


def test_jti_is_unique_per_issue_call(svc: JWTService) -> None:
    """Each call to issue() must produce a distinct jti (RFC 7519 §4.1.7)."""
    fid = str(uuid.uuid4())
    uid = uuid.uuid4()
    oid = uuid.uuid4()

    token1 = svc.issue(user_id=uid, organisation_id=oid, org_role="viewer", family_id=fid)
    token2 = svc.issue(user_id=uid, organisation_id=oid, org_role="viewer", family_id=fid)
    token3 = svc.issue(user_id=uid, organisation_id=oid, org_role="viewer", family_id=fid)

    p1 = svc.verify(token1)
    p2 = svc.verify(token2)
    p3 = svc.verify(token3)

    # All three must have distinct jtis
    assert p1.jti != p2.jti
    assert p2.jti != p3.jti
    assert p1.jti != p3.jti
    # All share the same family_id (CSRF binding stable within a session)
    assert p1.family_id == p2.family_id == p3.family_id == fid


def test_org_switch_produces_new_jti(svc: JWTService) -> None:
    """Simulates org context switch: same user/family, different org → new jti."""
    uid = uuid.uuid4()
    org1 = uuid.uuid4()
    org2 = uuid.uuid4()
    fid = str(uuid.uuid4())

    token_org1 = svc.issue(user_id=uid, organisation_id=org1, org_role="owner", family_id=fid)
    token_org2 = svc.issue(
        user_id=uid, organisation_id=org2, org_role="administrator", family_id=fid
    )

    p1 = svc.verify(token_org1)
    p2 = svc.verify(token_org2)

    # Different orgs
    assert p1.organisation_id == org1
    assert p2.organisation_id == org2
    # Different jtis
    assert p1.jti != p2.jti
    # Same family — CSRF remains valid
    assert p1.family_id == p2.family_id == fid


def test_workspace_switch_produces_new_jti(svc: JWTService) -> None:
    """Simulates workspace context switch: same org/family, workspace added → new jti."""
    uid = uuid.uuid4()
    oid = uuid.uuid4()
    wsid = uuid.uuid4()
    fid = str(uuid.uuid4())

    token_no_ws = svc.issue(user_id=uid, organisation_id=oid, org_role="owner", family_id=fid)
    token_with_ws = svc.issue(
        user_id=uid,
        organisation_id=oid,
        org_role="owner",
        family_id=fid,
        workspace_id=wsid,
        workspace_role="administrator",
    )

    p_no_ws = svc.verify(token_no_ws)
    p_with_ws = svc.verify(token_with_ws)

    assert p_no_ws.workspace_id is None
    assert p_with_ws.workspace_id == wsid
    assert p_with_ws.workspace_role == "administrator"
    # New jti for each minted token
    assert p_no_ws.jti != p_with_ws.jti
    # Family stable
    assert p_no_ws.family_id == p_with_ws.family_id == fid


def test_workspace_claims_optional(svc: JWTService) -> None:
    """Token without workspace context has None workspace fields."""
    token = svc.issue(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        org_role="viewer",
        family_id=str(uuid.uuid4()),
    )
    payload = svc.verify(token)
    assert payload.workspace_id is None
    assert payload.workspace_role is None


def test_verify_raises_on_expired(settings: Settings) -> None:
    # Craft a token whose exp is 1 second in the past.
    now = int(time.time())
    raw = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "viewer",
            "jti": str(uuid.uuid4()),
            "fid": str(uuid.uuid4()),
            "iss": "atlascore",
            "iat": now - 10,
            "exp": now - 1,
        },
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )
    svc = JWTService(settings)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        svc.verify(raw)


def test_verify_raises_on_tampered_signature(svc: JWTService) -> None:
    token = svc.issue(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        org_role="viewer",
        family_id=str(uuid.uuid4()),
    )
    # Flip the last character of the signature segment.
    parts = token.split(".")
    sig = parts[2]
    tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    bad_token = ".".join(parts[:2] + [tampered_sig])
    with pytest.raises(pyjwt.PyJWTError):
        svc.verify(bad_token)


def test_verify_raises_on_wrong_key(settings: Settings) -> None:
    # Issue a token with a different key.
    other_key = "z" * 64
    raw = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "viewer",
            "jti": str(uuid.uuid4()),
            "fid": str(uuid.uuid4()),
            "iss": "atlascore",
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
        },
        other_key,
        algorithm="HS256",
    )
    svc = JWTService(settings)
    with pytest.raises(pyjwt.PyJWTError):
        svc.verify(raw)


def test_payload_has_required_claims(svc: JWTService, settings: Settings) -> None:
    token = svc.issue(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        org_role="owner",
        family_id=str(uuid.uuid4()),
    )
    # Decode without verification to inspect claims.
    decoded = pyjwt.decode(token, options={"verify_signature": False})
    for claim in ("sub", "org", "role", "jti", "fid", "iss", "iat", "exp"):
        assert claim in decoded, f"Missing claim: {claim}"
    assert decoded["iss"] == "atlascore"


def test_verify_raises_on_missing_fid_claim(settings: Settings) -> None:
    """Token without 'fid' claim must be rejected (required claim)."""
    now = int(time.time())
    raw = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "viewer",
            "jti": str(uuid.uuid4()),
            # fid intentionally absent
            "iss": "atlascore",
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )
    svc = JWTService(settings)
    with pytest.raises(pyjwt.PyJWTError):
        svc.verify(raw)
