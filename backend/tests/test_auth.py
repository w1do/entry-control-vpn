from __future__ import annotations

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, make_settings


def test_login_and_me(client) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["csrf_token"]
    cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie_header

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL
    assert me.json()["weak_password"] is False


def test_login_wrong_password(client) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_user_generic_error(client) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_me_requires_auth(client) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_login_rate_limit(client) -> None:
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
    )
    assert blocked.status_code == 429
    assert "retry-after" in {key.lower() for key in blocked.headers}


def test_session_rotation(client) -> None:
    first = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert first.status_code == 200
    old_cookie = first.cookies.get("vpn_session") or first.cookies.get("__Host-vpn_session")
    second = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert second.status_code == 200
    # New cookie is now used; replay of the old token must fail.
    client.cookies.set("vpn_session", old_cookie)
    replay = client.get("/api/v1/auth/me")
    assert replay.status_code == 401


def test_csrf_required_for_mutations(client, auth) -> None:
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-12345"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_change_password_flow(client, auth) -> None:
    new_password = "new-password-12345"
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": new_password},
        headers=auth["headers"],
    )
    assert response.status_code == 200, response.text
    new_csrf = response.json()["csrf_token"]

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": new_csrf})
    login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": new_password})
    assert login.status_code == 200


def test_logout_revokes_session(client, auth) -> None:
    response = client.post("/api/v1/auth/logout", headers=auth["headers"])
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_weak_bootstrap_password_flag(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = make_settings(tmp_path, password="short")
    with TestClient(create_app(settings)) as weak_client:
        weak_client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "short"})
        me = weak_client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["weak_password"] is True


def test_settings_reject_bad_encryption_key(tmp_path) -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_settings(tmp_path, encryption_key="not-a-fernet-key")


def test_settings_reject_placeholder_secret(tmp_path) -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_settings(tmp_path, secret_key="change-me-to-a-random-64-hex")


def test_bootstrap_does_not_overwrite_existing_admin(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as first_client:
        assert (
            first_client.post(
                "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            ).status_code
            == 200
        )
    other = make_settings(tmp_path, password="other-password-1234")
    with TestClient(create_app(other)) as second_client:
        assert (
            second_client.post(
                "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            ).status_code
            == 200
        )
        assert (
            second_client.post(
                "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "other-password-1234"}
            ).status_code
            == 401
        )