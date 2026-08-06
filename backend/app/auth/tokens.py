"""
JWT access token service using PyJWT.

SECURITY PROPERTIES:
- Uses HS256 with a long random key (≥64 bytes enforced at startup).
- Access tokens are short-lived (default 15 minutes).
- Tokens contain:
    sub        — user_id
    org        — organisation_id
    role       — org_role (org-level role string, nullable)
    workspace  — workspace_id (optional, present when a workspace context is active)
    ws_role    — workspace_role (optional, matches workspace membership role)
    jti        — unique random UUID generated fresh for EACH minted access token
    fid        — family_id (refresh token family UUID; stable across rotations of a
                 single login session; used as the CSRF binding identifier)
    exp, iat, iss
- Tokens are NOT stored server-side; verification is stateless.
  Revocation is via org membership re-check on every request.
- Tokens are kept in JavaScript memory on the frontend — never localStorage.

JTI SEMANTICS (Phase 1B correction):
  Prior to this correction, jti was set to the refresh token's own jti so that
  HMAC-SHA256(CSRF_SECRET, jti) could serve as the CSRF token.  This violated
  RFC 7519 §4.1.7 which requires jti to uniquely identify the *access* token.

  Resolution: jti is now a fresh uuid4() generated inside issue().  CSRF binding
  is preserved via the separate 'fid' (family_id) claim:
      CSRF_TOKEN = HMAC-SHA256(CSRF_SECRET, fid)
  Since family_id is stable across the entire login session (all rotations of a
  single refresh token share one family_id), CSRF protection is preserved without
  requiring jti to be shared between token generations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import Settings

_ALGORITHM = "HS256"
_ISSUER = "atlascore"


class TokenPayload:
    """Validated access token payload."""

    def __init__(
        self,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
        org_role: str | None,
        jti: str,
        family_id: str,
        exp: datetime,
        iat: datetime,
        workspace_id: uuid.UUID | None = None,
        workspace_role: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.organisation_id = organisation_id
        self.org_role = org_role
        self.jti = jti
        self.family_id = family_id
        self.exp = exp
        self.iat = iat
        self.workspace_id = workspace_id
        self.workspace_role = workspace_role


class JWTService:
    """Issue and verify JWT access tokens."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.JWT_SECRET_KEY
        self._algorithm = _ALGORITHM
        self._expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        organisation_id: uuid.UUID,
        org_role: str | None,
        family_id: str,
        workspace_id: uuid.UUID | None = None,
        workspace_role: str | None = None,
    ) -> str:
        """
        Issue a signed JWT access token.

        A unique jti is generated internally for each call — the access token's
        own identifier.  CSRF binding uses the 'fid' (family_id) claim which is
        the refresh token family ID — stable across the entire login session.

        Callers must supply family_id (the RefreshToken.family_id for the
        current session).  This is used by CSRFService to compute and verify
        the CSRF double-submit token: HMAC-SHA256(CSRF_SECRET, family_id).

        workspace_id / workspace_role are optional.  They are present only when
        the user has an active workspace context (i.e. after POST /me/switch-workspace).
        """
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "org": str(organisation_id),
            "role": org_role,
            "jti": str(uuid.uuid4()),  # unique per access token
            "fid": family_id,  # refresh-session binding for CSRF
            "iss": _ISSUER,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=self._expire_minutes)).timestamp()),
        }
        if workspace_id is not None:
            payload["workspace"] = str(workspace_id)
        if workspace_role is not None:
            payload["ws_role"] = workspace_role
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def verify(self, token: str) -> TokenPayload:
        """
        Decode and verify a JWT access token.

        Raises jwt.exceptions.* on any failure:
        - ExpiredSignatureError: token has expired
        - InvalidSignatureError: signature does not match
        - DecodeError: malformed token
        - InvalidIssuerError: wrong issuer
        """
        decoded = jwt.decode(
            token,
            self._secret,
            algorithms=[self._algorithm],
            options={"require": ["sub", "org", "jti", "fid", "exp", "iat", "iss"]},
            issuer=_ISSUER,
        )
        ws_raw = decoded.get("workspace")
        return TokenPayload(
            user_id=uuid.UUID(decoded["sub"]),
            organisation_id=uuid.UUID(decoded["org"]),
            org_role=decoded.get("role"),
            jti=decoded["jti"],
            family_id=decoded["fid"],
            exp=datetime.fromtimestamp(decoded["exp"], tz=UTC),
            iat=datetime.fromtimestamp(decoded["iat"], tz=UTC),
            workspace_id=uuid.UUID(ws_raw) if ws_raw else None,
            workspace_role=decoded.get("ws_role"),
        )
