from __future__ import annotations

from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- mandatory ---
    admin: str = Field(min_length=3, description="Single admin email")
    password: str = Field(min_length=1, description="Bootstrap password (first start only)")
    secret_key: str = Field(min_length=16, description="Server-side secret for signing/CSRF")
    encryption_key: str = Field(description="Fernet key for secrets at rest")

    # --- panel ---
    app_name: str = "VPN Control Panel"
    app_env: str = "production"
    version: str = "0.1.0"
    public_base_url: str = "http://localhost:8000"
    metrics_token: str = ""
    data_dir: str = "/data"
    artifacts_dir: str = ""
    static_dir: str = ""
    auto_create_schema: bool = False
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    # --- sessions ---
    session_ttl_seconds: int = 7 * 24 * 3600
    session_cookie_secure: bool = True
    cookie_name: str = ""
    csrf_header: str = "X-CSRF-Token"

    # --- limits ---
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    login_block_seconds: int = 900

    # --- nodes / versions ---
    xray_version: str = "26.11.9"
    agent_version: str = "0.1.0"
    heartbeat_offline_seconds: int = 90
    exit_health_interval_seconds: int = 30
    scheduler_enabled: bool = True

    @field_validator("encryption_key")
    @classmethod
    def _validate_encryption_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except Exception as exc:  # noqa: BLE001 - explicit message for operator
            raise ValueError("ENCRYPTION_KEY must be a valid Fernet key (see .env.example)") from exc
        return value

    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key(cls, value: str) -> str:
        if value.strip().lower() in {"change-me", "changeme", "secret", "change-me-to-a-random-64-hex"}:
            raise ValueError("SECRET_KEY must be a strong random value, not a placeholder")
        return value

    @field_validator("public_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")
        return value

    @model_validator(mode="after")
    def _validate_model(self) -> Settings:
        if not self.database_url.startswith(("sqlite+aiosqlite://", "postgresql+asyncpg://")):
            raise ValueError(
                "DATABASE_URL must use sqlite+aiosqlite:// or postgresql+asyncpg:// scheme"
            )
        if not self.cookie_name:
            object.__setattr__(
                self,
                "cookie_name",
                "__Host-vpn_session" if self.session_cookie_secure else "vpn_session",
            )
        if not self.artifacts_dir:
            object.__setattr__(self, "artifacts_dir", f"{self.data_dir.rstrip('/')}/artifacts")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite+aiosqlite://")

    @property
    def weak_admin_password(self) -> bool:
        return len(self.password) < 12

    def public_dict(self) -> dict[str, Any]:
        """Safe representation for logs/diagnostics (secrets masked)."""
        return {
            "app_env": self.app_env,
            "version": self.version,
            "public_base_url": self.public_base_url,
            "database_url": self.database_url.split("://", 1)[0] + "://***",
            "admin": self.admin,
            "xray_version": self.xray_version,
            "agent_version": self.agent_version,
            "session_cookie_secure": self.session_cookie_secure,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
