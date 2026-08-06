"""
Tests for PasswordService.

Coverage:
- hash() produces a non-empty string that is not the raw password
- verify() returns True for a correct password
- verify() returns False for a wrong password (no exception)
- verify() returns False when pepper version mismatches
- needs_rehash() returns True when the stored pepper version is old
- needs_rehash() returns False for a freshly hashed password
- rehash() produces a new hash and returns the current pepper version
"""

import pytest

from app.auth.password import PasswordService


@pytest.fixture()
def svc() -> PasswordService:
    return PasswordService(pepper="x" * 32, pepper_version=1)


def test_hash_is_not_plaintext(svc: PasswordService) -> None:
    h = svc.hash("hunter2")
    assert h != "hunter2"
    assert len(h) > 20


def test_verify_correct_password(svc: PasswordService) -> None:
    h = svc.hash("correct-horse-battery-staple")
    assert svc.verify("correct-horse-battery-staple", h, stored_pepper_version=1) is True


def test_verify_wrong_password(svc: PasswordService) -> None:
    h = svc.hash("my-secret")
    assert svc.verify("wrong-secret", h, stored_pepper_version=1) is False


def test_verify_wrong_pepper_version(svc: PasswordService) -> None:
    h = svc.hash("my-secret")
    # The hash was made with pepper_version=1; presenting version=2 should fail.
    assert svc.verify("my-secret", h, stored_pepper_version=2) is False


def test_verify_never_raises(svc: PasswordService) -> None:
    # Garbage hash — must not raise.
    result = svc.verify("anything", "notahash$garbage", stored_pepper_version=1)
    assert result is False


def test_needs_rehash_current_version(svc: PasswordService) -> None:
    h = svc.hash("my-secret")
    assert svc.needs_rehash(h, stored_pepper_version=1) is False


def test_needs_rehash_old_pepper_version(svc: PasswordService) -> None:
    # Simulate an older hash (from pepper_version=0).
    old_svc = PasswordService(pepper="x" * 32, pepper_version=0)
    h = old_svc.hash("my-secret")
    # Now the current svc uses pepper_version=1 — so rehash is needed.
    assert svc.needs_rehash(h, stored_pepper_version=0) is True


def test_rehash_returns_new_hash_and_version(svc: PasswordService) -> None:
    new_hash, version = svc.rehash("my-secret")
    assert version == 1
    assert svc.verify("my-secret", new_hash, stored_pepper_version=version) is True


def test_hash_is_deterministically_different(svc: PasswordService) -> None:
    """Argon2 uses a random salt — two hashes of the same password differ."""
    h1 = svc.hash("same-password")
    h2 = svc.hash("same-password")
    assert h1 != h2
    assert svc.verify("same-password", h1, stored_pepper_version=1) is True
    assert svc.verify("same-password", h2, stored_pepper_version=1) is True
