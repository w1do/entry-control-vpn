from __future__ import annotations

import os

from cryptography.fernet import Fernet

# Environment defaults must exist before importing app.core.config / app.main.
os.environ.setdefault("ADMIN", "admin@example.com")
os.environ.setdefault("PASSWORD", "correct-horse-battery-staple")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test-app.db")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("APP_ENV", "test")

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct-horse-battery-staple"


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "admin": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "secret_key": "test-secret-key-0123456789abcdef",
        "encryption_key": Fernet.generate_key().decode(),
        "public_base_url": "http://testserver",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "session_cookie_secure": False,
        "scheduler_enabled": False,
        "auto_create_schema": True,
        "app_env": "test",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return {"csrf": data["csrf_token"], "headers": {"X-CSRF-Token": data["csrf_token"]}}
