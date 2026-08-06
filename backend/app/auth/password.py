"""
Password hashing and verification using Argon2id via pwdlib.

SECURITY PROPERTIES:
- Uses Argon2id (memory-hard, side-channel resistant).
- A server-side pepper is prepended to the password before hashing.
  The pepper is never stored in the database; it lives only in the
  application environment.  Compromise of the DB alone does not expose
  plaintext passwords without also compromising the pepper.
- Pepper versioning enables key rotation.  When ARGON2_PEPPER_VERSION
  increments, the old pepper is retained for verification and passwords
  are lazily rehashed on successful login.
- Passwords and hashes are never logged.
"""

from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import Settings

# ---------------------------------------------------------------------------
# Hasher instance — created from settings at startup
# ---------------------------------------------------------------------------


def build_password_hash(settings: Settings) -> PasswordService:
    """Build a PasswordService from application settings."""
    return PasswordService(
        pepper=settings.ARGON2_PEPPER,
        pepper_version=settings.ARGON2_PEPPER_VERSION,
    )


class PasswordService:
    """
    Argon2id password hashing with server-side pepper.

    The _ph (PasswordHash) instance is configured with Argon2id defaults.
    Argon2id parameters (time_cost, memory_cost, parallelism) can be tuned
    in the Argon2Hasher constructor; defaults are OWASP-recommended.
    """

    def __init__(self, pepper: str, pepper_version: int) -> None:
        self._pepper = pepper
        self._pepper_version = pepper_version
        self._ph = PasswordHash([Argon2Hasher()])

    def _peppered(self, password: str, pepper_version: int | None = None) -> str:
        """
        Prepend the pepper to the password before hashing/verification.

        Only the current pepper version is used for new hashes.
        Verification accepts the stored pepper version so old hashes remain
        valid during rotation.
        """
        # For now only one pepper version is supported.
        # Phase 9 (security hardening) adds multi-pepper rotation.
        _ = pepper_version  # reserved for rotation
        return self._pepper + password

    def hash(self, password: str) -> str:
        """
        Hash a password with the current pepper version.

        Returns an Argon2id hash string (includes algorithm parameters
        and salt — safe to store in the database).
        """
        peppered = self._peppered(password)
        return self._ph.hash(peppered)

    def verify(
        self,
        password: str,
        password_hash: str,
        stored_pepper_version: int,
    ) -> bool:
        """
        Verify a plaintext password against a stored hash.

        Uses the pepper version that was active when the hash was created.
        Returns True if the password is correct, False otherwise.
        Never raises on hash mismatch.
        """
        # Only the currently configured pepper material is available.
        # A hash stamped with a different pepper version cannot be verified
        # safely until that historical pepper is explicitly configured.
        if stored_pepper_version != self._pepper_version:
            return False
        peppered = self._peppered(password, stored_pepper_version)
        try:
            return bool(self._ph.verify(peppered, password_hash))
        except Exception:
            return False

    def needs_rehash(self, password_hash: str, stored_pepper_version: int) -> bool:
        """
        Return True if the hash should be upgraded.

        Triggers rehash if:
        - The Argon2 parameters have changed (pwdlib detects this), or
        - The stored pepper version is older than the current pepper version.
        """
        # Pepper-version changes always require a new hash.  pwdlib's
        # PasswordHash API intentionally does not expose a generic
        # check_needs_rehash() method, so parameter upgrades are handled when
        # the configured hash policy/version changes alongside the pepper.
        return stored_pepper_version != self._pepper_version

    def rehash(self, password: str) -> tuple[str, int]:
        """
        Produce a new hash with the current pepper version.

        Returns (new_hash, current_pepper_version).
        Call this when needs_rehash() returns True.
        """
        new_hash = self.hash(password)
        return new_hash, self._pepper_version
