from __future__ import annotations

from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    """Encrypts secrets at rest (SSH credentials, REALITY keys, tokens)."""

    def __init__(self, key: str, previous_keys: Sequence[str] = ()) -> None:
        ferns = [Fernet(key.encode())]
        for old in previous_keys:
            ferns.append(Fernet(old.encode()))
        self._fernet = MultiFernet(ferns)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("cannot decrypt secret: ENCRYPTION_KEY mismatch") from exc

    def rotate(self, token: str) -> str:
        return self._fernet.rotate(token.encode()).decode()


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()
