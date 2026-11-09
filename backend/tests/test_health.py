from __future__ import annotations


def test_healthz_ok(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "ok"
    assert payload["version"]


def test_security_headers_present(client) -> None:
    response = client.get("/healthz")
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
    assert "x-request-id" in response.headers
