from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def generate_x25519() -> tuple[str, str]:
    """Return (private_key, public_key) in Xray base64url format."""
    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes_raw()
    public_raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64url(private_raw), _b64url(public_raw)


def public_from_private(private_key: str) -> str:
    private = X25519PrivateKey.from_private_bytes(_b64url_decode(private_key))
    public_raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64url(public_raw)


def new_short_id() -> str:
    return secrets.token_hex(8)
