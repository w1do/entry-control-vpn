from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP-recommended argon2id parameters (64 MiB, t=3, p=4).
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Used for constant-time-ish dummy verification of unknown users (anti-enumeration).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password_hash or _DUMMY_HASH
    try:
        return _hasher.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_is_weak(password: str) -> bool:
    """Policy used for bootstrap warning and password change validation."""
    return len(password) < 12


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_hex(32)


def tokens_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def new_uuid() -> str:
    import uuid

    return str(uuid.uuid4())
