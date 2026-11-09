from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from app.core.crypto import SecretBox
from app.core.db import create_all, dispose_engine, init_engine
from app.core.security import new_uuid
from app.models.routing import Client
from app.models.servers import Server, ServerSecret
from app.services.reality import generate_x25519, new_short_id
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, make_settings

XRAY_BIN = Path(os.environ.get("XRAY_BIN", "/home/dev/vpn/.tools/xray/xray"))
XRAY_ASSETS = Path(os.environ.get("XRAY_LOCATION_ASSET", "/home/dev/vpn/.tools"))


def _seed(settings, base_dir: str) -> int:
    box = SecretBox(settings.encryption_key)
    private_key, public_key = generate_x25519()

    async def run() -> int:
        init_engine(settings)
        await create_all()
        from app.core.db import get_session_factory

        async with get_session_factory()() as db:
            server = Server(
                name="entry-e2e",
                host="203.0.113.10",
                roles="entry",
                status="online",
                base_dir=base_dir,
            )
            db.add(server)
            await db.flush()
            db.add(
                ServerSecret(
                    server_id=server.id,
                    reality_private_key_enc=box.encrypt(private_key),
                    reality_public_key=public_key,
                    short_ids_json=[new_short_id()],
                    interconnect_uuid_enc=box.encrypt(new_uuid()),
                    real_dest="www.microsoft.com",
                    real_sni="www.microsoft.com",
                )
            )
            db.add(
                Client(
                    name="phone",
                    uuid=str(new_uuid()),
                    email_tag="client-1@vpn",
                    sub_token="e2e-sub-token",
                )
            )
            await db.commit()
            return server.id

    return asyncio.run(run())


def _dispose() -> None:
    asyncio.run(dispose_engine())


@pytest.fixture
def panel(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    base_dir = tmp_path / "node-base"
    (base_dir / "log").mkdir(parents=True, exist_ok=True)
    settings = make_settings(tmp_path)
    _seed(settings, str(base_dir))
    _dispose()
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login.status_code == 200
        client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
        yield client


@pytest.mark.skipif(not XRAY_BIN.is_file(), reason="xray binary is not available")
def test_apply_compiles_config_accepted_by_real_xray(panel, tmp_path) -> None:
    exit_response = panel.post(
        "/api/v1/exits",
        json={
            "kind": "external",
            "label": "us-isp",
            "region": "US",
            "endpoint_host": "198.51.100.20",
            "endpoint_port": 3128,
            "protocol": "http",
            "username": "user",
            "password": "pass",
        },
    )
    assert exit_response.status_code == 201, exit_response.text
    exit_id = exit_response.json()["id"]
    site = panel.post("/api/v1/sites", json={"preset_key": "anthropic", "allowed_exit_ids": [exit_id]})
    assert site.status_code == 201, site.text
    profile = panel.put(
        "/api/v1/routing/profile", json={"default_exit_ids": [exit_id], "enabled": True}
    )
    assert profile.status_code == 200, profile.text

    apply_response = panel.post("/api/v1/routing/apply")
    assert apply_response.status_code == 200, apply_response.text
    revision_id = apply_response.json()["revision_id"]
    body = {}
    for _ in range(40):
        revision = panel.get(f"/api/v1/routing/revisions/{revision_id}")
        assert revision.status_code == 200, revision.text
        body = revision.json()
        if body.get("status") == "applied":
            break
        time.sleep(0.05)
    assert body.get("status") == "applied", body
    assert body["applied_at"]
    configs = body["configs"]
    assert configs, "revision has no compiled configs"
    server_config = next(iter(configs.values()))
    config_path = tmp_path / "entry.json"
    config_path.write_text(json.dumps(server_config, ensure_ascii=False, indent=2))

    env = dict(os.environ)
    env["XRAY_LOCATION_ASSET"] = str(XRAY_ASSETS)
    process = subprocess.run(
        [str(XRAY_BIN), "run", "-test", "-c", str(config_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr or process.stdout

    payload = json.loads(config_path.read_text())
    rule_tags = [rule.get("balancerTag") for rule in payload["routing"]["rules"] if rule.get("balancerTag")]
    assert ANY_RULE_TAG in rule_tags
    assert payload["outbounds"][0]["tag"] == "block"
    balancer_tags = {item["tag"] for item in payload["routing"]["balancers"]}
    assert "bal-default" in balancer_tags
    assert ANY_RULE_TAG in balancer_tags
    direct_rules = [
        rule
        for rule in payload["routing"]["rules"]
        if rule.get("outboundTag") == "direct"
    ]
    assert all("geosite:category-ru" in rule.get("domain", []) or "geoip:ru" in rule.get("ip", []) for rule in direct_rules)


ANY_RULE_TAG = "bal-rule-1"


def test_link_and_smart_subscription(panel, tmp_path) -> None:
    created = panel.post("/api/v1/clients", json={"name": "e2e-phone"})
    assert created.status_code == 201, created.text
    client_id = created.json()["id"]
    link = panel.get(f"/api/v1/clients/{client_id}/link")
    assert link.status_code == 200
    assert link.json()["url"].startswith("vless://")

    subscription = panel.get(
        "/sub/e2e-sub-token", headers={"User-Agent": "sing-box/1.11.0"}
    )
    assert subscription.status_code == 200, subscription.text
    profile = subscription.json()
    assert profile["outbounds"][0]["type"] == "vless"
    assert any("category-ru" in str(item) for item in profile["route"]["rule_set"])
    assert subscription.headers["subscription-userinfo"].startswith("upload=")
