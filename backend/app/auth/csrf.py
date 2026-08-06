"""
CSRF protection — double-submit cookie pattern.

DESIGN:
  csrf_token = HMAC-SHA256(CSRF_SECRET, family_id)

The backend sets a JS-readable (NOT HttpOnly) cookie named 'csrf_token'.
The frontend reads the cookie and sends the value as the X-CSRF-Token header.
The backend compares header to cookie in constant time (hmac.compare_digest).

Additional protection: Origin header is validated on all cookie-authenticated
state-changing requests (POST/PUT/PATCH/DELETE).

BINDING IDENTIFIER — family_id:
  family_id is the RefreshToken.family_id (UUID) assigned at first login and
  preserved across all token rotations of the same login session.  It is
  carried in the JWT 'fid' claim.  Using family_id as the CSRF binding key
  rather than the access-token jti means:
    - CSRF token is stable across org/workspace context switches (which mint
      new access tokens with new jti values, but the same family_id).
    - jti is free to be a unique per-access-token identifier (RFC 7519 §4.1.7).
    - CSRF token still rotates on token refresh (new refresh token family) and
      is cleared on logout.

The CSRF token is rotated on:
  - Org selection (new refresh token → new family_id unless same session)
  - Token refresh (new family_id when family changes, otherwise stable)
  - Logout (cookie cleared)

COOKIE ATTRIBUTES for csrf_token:
  HttpOnly: false (must be readable by JavaScript)
  Secure: true (required in production)
  SameSite: Lax
  Path: /  (must be readable on all paths)
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import Response

from app.core.config import Settings


class CSRFService:
    """Issue and verify CSRF tokens using the double-submit cookie pattern."""

    COOKIE_NAME = "csrf_token"
    HEADER_NAME = "X-CSRF-Token"

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.CSRF_SECRET.encode()
        self._secure_cookies = settings.SECURE_COOKIES

    def generate_token(self, family_id: str) -> str:
        """
        Generate CSRF token: HMAC-SHA256(CSRF_SECRET, family_id).

        The token is bound to the refresh session via the refresh token
        family_id.  It remains stable across org/workspace context switches
        (new access tokens, same family) and rotates only when a new refresh
        token family is created or on logout.
        """
        mac = hmac.new(self._secret, family_id.encode(), hashlib.sha256)
        return mac.hexdigest()

    def set_csrf_cookie(
        self,
        response: Response,
        family_id: str,
    ) -> str:
        """
        Set the CSRF cookie on the response and return the token.

        The cookie is NOT HttpOnly so that JavaScript can read it.
        family_id must be the RefreshToken.family_id for the current session.
        """
        token = self.generate_token(family_id)
        response.set_cookie(
            key=self.COOKIE_NAME,
            value=token,
            httponly=False,  # Must be JS-readable
            secure=self._secure_cookies,
            samesite="lax",
            path="/",
        )
        return token

    def clear_csrf_cookie(self, response: Response) -> None:
        """Clear the CSRF cookie (called on logout)."""
        response.delete_cookie(
            key=self.COOKIE_NAME,
            path="/",
        )

    def verify(self, request: Request, family_id: str) -> bool:
        """
        Verify the CSRF token from the request header.

        Returns True if:
        - X-CSRF-Token header is present, AND
        - The header value matches HMAC-SHA256(CSRF_SECRET, family_id),
          verified in constant time.

        family_id must be the value from the JWT 'fid' claim (TokenPayload.family_id).
        """
        header_token = request.headers.get(self.HEADER_NAME, "")
        if not header_token:
            return False

        expected = self.generate_token(family_id)
        return hmac.compare_digest(header_token, expected)

    def validate_origin(self, request: Request, allowed_origins: list[str]) -> bool:
        """
        Validate the Origin header against the allowed origins list.

        Called on all state-changing requests (POST/PUT/PATCH/DELETE).
        Returns True if Origin is in the allowed list, or if Origin header
        is absent (non-browser clients).
        """
        origin = request.headers.get("Origin")
        if origin is None:
            # Non-browser client — allow (CSRF is a browser attack)
            return True
        return origin in allowed_origins
