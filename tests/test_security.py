"""Tests for password hashing utilities."""

from app.core.security import hash_password, verify_password


def test_hash_is_not_plaintext():
    h = hash_password("hunter2hunter2")
    assert h != "hunter2hunter2"
    assert h.startswith("$2b$")


def test_same_password_produces_different_hashes():
    """Different per-password salts → different hashes for the same input."""
    a = hash_password("hunter2hunter2")
    b = hash_password("hunter2hunter2")
    assert a != b


def test_verify_correct_password():
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", h) is True


def test_verify_wrong_password():
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter3", h) is False


def test_verify_handles_garbage_hash():
    """Malformed stored hash returns False, not an exception."""
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False
