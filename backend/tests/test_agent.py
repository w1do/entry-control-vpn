from __future__ import annotations

import asyncio

import pytest

from app.core.db import create_all, dispose_engine, init_engine
from app.core.security import sha256_hex
from app.models.ops import Job
from app.models.servers import Server
from tests.conftest import make_settings

TOKEN = "test-agent-token-0123456789"


def _seed(settings, token: str = TOKEN) -> int:
    async def run() -> int:
        init_engine(settings)
        await create_all()
        from app.core.db import get_session_factory

        async with get_session_factory()() as db:
            server = Server(
                name="node-test",
                host="203.0.113.10",
                roles="entry",
                status="provisioning",
                agent_token_hash=sha256_hex(token),
            )
            db.add(server)
            await db.flush()
            db.add(Job(type="diagnose", server_id=server.id, status="queued", payload_json={}))
            await db.commit()
            return server.id

    return asyncio.run(run())


def _dispose(settings) -> None:
    asyncio.run(dispose_engine())


@pytest.fixture
def agent_client(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = make_settings(tmp_path)
    server_id = _seed(settings)
    _dispose(settings)
    with TestClient(create_app(settings)) as client:
        yield client, server_id


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_agent_requires_token(agent_client) -> None:
    client, _ = agent_client
    response = client.post("/agent/v1/heartbeat", json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_agent_unknown_token(agent_client) -> None:
    client, _ = agent_client
    response = client.post("/agent/v1/heartbeat", json={}, headers=_headers("wrong-token"))
    assert response.status_code == 401


def test_agent_register_and_heartbeat(agent_client) -> None:
    client, server_id = agent_client
    register = client.post(
        "/agent/v1/register",
        json={"agent_version": "0.1.0", "xray_version": "26.11.9", "hostname": "node-test"},
        headers=_headers(),
    )
    assert register.status_code == 200, register.text
    assert register.json()["server_id"] == server_id

    heartbeat = client.post(
        "/agent/v1/heartbeat",
        json={"revision": None, "xray": {"active": True, "version": "26.11.9"}},
        headers=_headers(),
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["revision"] is None


def test_agent_config_204_then_metrics(agent_client) -> None:
    client, _ = agent_client
    config = client.get("/agent/v1/config", headers=_headers())
    assert config.status_code == 204

    metrics = client.post(
        "/agent/v1/metrics",
        json={
            "revision": None,
            "traffic_delta": {"up": 100, "down": 200},
            "connections": {"accepted_15s": 3, "blocked_15s": 1, "online": 1},
            "errors": {"rejected_15s": 0, "xray_error_15s": 0},
            "clients": [{"email": "client-1@vpn", "up": 10, "down": 20, "online": True}],
        },
        headers=_headers(),
    )
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["ok"] is True


def test_agent_receives_queued_command_and_reports_result(agent_client) -> None:
    client, server_id = agent_client
    heartbeat = client.post("/agent/v1/heartbeat", json={}, headers=_headers())
    assert heartbeat.status_code == 200
    commands = heartbeat.json()["commands"]
    assert len(commands) == 1
    assert commands[0]["type"] == "diagnose"
    command_id = commands[0]["id"]

    # Command is no longer delivered twice.
    second = client.post("/agent/v1/heartbeat", json={}, headers=_headers())
    assert second.json()["commands"] == []

    result = client.post(
        "/agent/v1/command-result",
        json={"command_id": command_id, "status": "succeeded", "output": '{"xray_version":"26.11.9"}'},
        headers=_headers(),
    )
    assert result.status_code == 200

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    job = client.get(f"/api/v1/jobs/{command_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"


def test_agent_apply_result_failure_creates_incident(agent_client) -> None:
    client, _ = agent_client
    result = client.post(
        "/agent/v1/apply-result",
        json={"revision": 5, "status": "failed", "error": "xray -test failed"},
        headers=_headers(),
    )
    assert result.status_code == 200
    assert result.json()["ok"] is True

    artifact = client.get("/agent/v1/artifacts/missing", headers=_headers())
    assert artifact.status_code == 404
