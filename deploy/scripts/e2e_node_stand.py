#!/usr/bin/env python3
"""E2E: real vpn-agent against the real panel API on a local stand.

The stand starts the FastAPI panel (uvicorn) on a free localhost port, creates
an exit node in the panel DB with a compiled Xray config, and runs the real
vpn-agent binary against it. Xray and systemd are replaced by small fake
scripts (the same technique the agent's own test-suite uses), so the whole
protocol is exercised end-to-end without root, REALITY or a real VPS:

  register -> heartbeat -> config poll -> apply (validate/swap/restart/health
  -> apply-result) -> metrics (delta accounting) -> diagnose command ->
  command-result.

Usage:
    .venv/bin/python deploy/scripts/e2e_node_stand.py [--keep]

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import http.cookiejar
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
VENV_PY = ROOT / ".venv" / "bin" / "python"
AGENT_BIN = ROOT / "artifacts" / "vpn-agent-linux-amd64"
ADMIN = "admin@example.com"
ADMIN_PASSWORD = secrets.token_urlsafe(18)
TOKEN = "e2e-agent-token-0123456789abcdef"
CLIENT_EMAIL = "client-1@vpn"

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS  {name:<44} {detail}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL  {name:<44} {detail}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def go_binary() -> str | None:
    found = shutil.which("go")
    if found:
        return found
    candidate = Path.home() / ".local" / "go" / "bin" / "go"
    return str(candidate) if candidate.is_file() else None


def build_agent() -> Path:
    if AGENT_BIN.is_file():
        return AGENT_BIN
    go = go_binary()
    if go is None:
        raise SystemExit("go not found: run 'make agent' first or install Go")
    AGENT_BIN.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [go, "build", "-trimpath", "-ldflags", "-s -w", "-o", str(AGENT_BIN), "./cmd/vpn-agent"],
        cwd=ROOT / "agent",
        check=True,
    )
    return AGENT_BIN


def write_fake_binaries(base: Path, node_port: int) -> None:
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    xray = bin_dir / "xray"
    xray.write_text(
        "#!/usr/bin/env python3\n"
        "import json, socket, sys, time\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['version']:\n"
        "    print('Xray 26.11.9 (stand)')\n"
        "    sys.exit(0)\n"
        "if args[:1] == ['run']:\n"
        "    if '-test' in args:\n"
        "        path = args[args.index('-c') + 1]\n"
        "        json.load(open(path))\n"
        "        print('Configuration OK.')\n"
        "        sys.exit(0)\n"
        "    path = args[args.index('-c') + 1]\n"
        "    cfg = json.load(open(path))\n"
        "    port = int(cfg['inbounds'][0]['port'])\n"
        "    srv = socket.socket()\n"
        "    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"    srv.bind(('127.0.0.1', {node_port}))\n"
        "    srv.listen(8)\n"
        "    while True:\n"
        "        conn, _ = srv.accept()\n"
        "        conn.close()\n"
        "if args[:1] == ['api']:\n"
        "    cmd = args[1] if len(args) > 1 else ''\n"
        "    if cmd == 'statsquery':\n"
        "        sec = int(time.time()) % 100000\n"
        "        stats = [\n"
        f"            {{'name': 'user>>>{CLIENT_EMAIL}>>>traffic>>>uplink', 'value': 1000 + sec}},\n"
        f"            {{'name': 'user>>>{CLIENT_EMAIL}>>>traffic>>>downlink', 'value': 2000 + sec * 2}},\n"
        "            {'name': 'inbound>>>vless-in>>>traffic>>>uplink', 'value': sec},\n"
        "            {'name': 'outbound>>>direct>>>traffic>>>downlink', 'value': sec},\n"
        "        ]\n"
        "        print(json.dumps({'stat': stats}))\n"
        "        sys.exit(0)\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    xray.chmod(0o755)

    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  is-active) echo active; exit 0 ;;\n"
        "  show) printf 'LoadState=loaded\\nActiveState=active\\nSubState=running\\nMainPID=4242\\nActiveEnterTimestampMonotonic=1000000\\n'; exit 0 ;;\n"
        "  restart|start)\n"
        f"    nohup {xray} run -c {base}/etc/xray.json >/dev/null 2>&1 &\n"
        "    sleep 0.2; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    systemctl.chmod(0o755)


def wait_for_health(base_url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/healthz", timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


class Panel:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict | list | None, dict]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read()
                headers = dict(response.headers)
                return response.status, (json.loads(raw) if raw else None), headers
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = None
            return error.code, body, dict(error.headers)

    def login(self) -> None:
        status, body, _ = self.request(
            "POST", "/api/v1/auth/login", {"email": ADMIN, "password": ADMIN_PASSWORD}
        )
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"login failed: {status} {body}")
        self.csrf = str(body["csrf_token"])

    def wait(self, path: str, predicate, timeout: float = 40.0, interval: float = 0.5):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            status, body, _ = self.request("GET", path)
            last = (status, body)
            if status == 200 and predicate(body):
                return body
            time.sleep(interval)
        raise SystemExit(f"timeout waiting for {path}: {last}")


async def seed(settings, base: Path, node_port: int, revision_config: dict, sha: str) -> tuple[int, int]:
    from app.core.db import create_all, dispose_engine, get_session_factory, init_engine
    from app.core.crypto import SecretBox
    from app.core.security import sha256_hex
    from app.models.routing import Client, RoutingRevision
    from app.models.servers import Server, ServerConfig, ServerSecret
    from app.services.reality import generate_x25519, new_short_id

    init_engine(settings)
    await create_all()
    private_key, public_key = generate_x25519()
    box = SecretBox(settings.encryption_key)
    async with get_session_factory()() as session:
        server = Server(
            name="e2e-node",
            host="127.0.0.1",
            roles="exit",
            status="provisioning",
            base_dir=str(base),
            agent_token_hash=sha256_hex(TOKEN),
        )
        session.add(server)
        await session.flush()
        session.add(
            ServerSecret(
                server_id=server.id,
                reality_private_key_enc=box.encrypt(private_key),
                reality_public_key=public_key,
                short_ids_json=[new_short_id()],
                interconnect_uuid_enc=box.encrypt("00000000-0000-4000-8000-000000000001"),
                real_dest="www.apple.com",
                real_sni="www.apple.com",
            )
        )
        session.add(
            Client(
                name="stand-client",
                uuid="11111111-1111-4111-8111-111111111111",
                email_tag=CLIENT_EMAIL,
                sub_token="stand-sub-token",
            )
        )
        revision = RoutingRevision(
            status="pending", profile_json={"enabled": True}, config_hashes_json={}
        )
        session.add(revision)
        await session.flush()
        session.add(
            ServerConfig(
                server_id=server.id,
                revision_id=revision.id,
                config_json=revision_config,
                sha256=sha,
                result=None,
            )
        )
        server.revision_id = revision.id
        revision.config_hashes_json = {str(server.id): sha}
        await session.commit()
        server_id, revision_id = server.id, revision.id
    await dispose_engine()
    return server_id, revision_id


async def make_exit_config(server_id: int, node_port: int) -> tuple[dict, str]:
    sys.path.insert(0, str(BACKEND))
    from app.compiler import (
        EntryPeerSpec,
        ExitCompileInput,
        ExitNodeSpec,
        RealityInboundSpec,
        canonical_json,
        compile_exit_config,
        sha256_json,
    )
    from app.services.reality import generate_x25519

    private_key, _public = generate_x25519()
    config = compile_exit_config(
        ExitCompileInput(
            node=ExitNodeSpec(
                server_id=server_id,
                inbound=RealityInboundSpec(
                    listen="0.0.0.0",
                    port=node_port,
                    dest="www.apple.com:443",
                    server_names=("www.apple.com",),
                    private_key=private_key,
                    short_ids=("0123456789abcdef",),
                ),
            ),
            peers=(
                EntryPeerSpec(
                    entry_server_id=1,
                    uuid="22222222-2222-4222-8222-222222222222",
                    email="entry-1",
                ),
            ),
        )
    )
    canonical_json(config)
    return config, sha256_json(config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the stand directory for debugging")
    args = parser.parse_args()

    if not VENV_PY.is_file():
        raise SystemExit(f"venv python not found: {VENV_PY}")

    work = Path(tempfile.mkdtemp(prefix="vpn-e2e-"))
    base = work / "node"
    (base / "etc").mkdir(parents=True)
    for name in ("bin", "log", "state"):
        (base / name).mkdir(parents=True)
    panel_port = free_port()
    node_port = free_port()
    database = work / "panel.db"
    base_url = f"http://127.0.0.1:{panel_port}"
    agent_path = build_agent()
    write_fake_binaries(base, node_port)

    sys.path.insert(0, str(BACKEND))
    from cryptography.fernet import Fernet

    env = {
        **os.environ,
        "ADMIN": ADMIN,
        "PASSWORD": ADMIN_PASSWORD,
        "SECRET_KEY": "e2e-secret-key-0123456789abcdef",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "SESSION_COOKIE_SECURE": "false",
        "SCHEDULER_ENABLED": "false",
        "AUTO_CREATE_SCHEMA": "true",
        "PUBLIC_BASE_URL": base_url,
        "APP_ENV": "test",
        "ARTIFACTS_DIR": str(ROOT / "artifacts"),
        "PYTHONPATH": str(BACKEND),
    }

    from app.core.config import Settings

    settings = Settings(
        admin=ADMIN,
        password=ADMIN_PASSWORD,
        secret_key=env["SECRET_KEY"],
        encryption_key=env["ENCRYPTION_KEY"],
        public_base_url=base_url,
        database_url=env["DATABASE_URL"],
        session_cookie_secure=False,
        scheduler_enabled=False,
        auto_create_schema=True,
        app_env="test",
    )
    config, sha = asyncio.run(make_exit_config(1, node_port))
    server_id, revision_id = asyncio.run(seed(settings, base, node_port, config, sha))

    (base / "etc" / "agent.json").write_text(
        json.dumps(
            {
                "panel_url": base_url,
                "token": TOKEN,
                "server_id": server_id,
                "base_dir": str(base),
                "heartbeat_interval_s": 5,
                "config_poll_s": 1,
                "metrics_interval_s": 5,
                "request_timeout_s": 5,
                "health_timeout_s": 5,
                "apply_test_timeout_s": 10,
                "apply_restart_timeout_s": 10,
                "xray_api_addr": "127.0.0.1:10085",
                "xray_binary": str(base / "bin" / "xray"),
                "systemctl_bin": str(base / "bin" / "systemctl"),
                "xray_asset_dir": str(base / "etc"),
                "unit_dir": str(base / "etc" / "units"),
                "log_level": "info",
            },
            indent=2,
        )
    )
    (base / "etc" / "agent.json").chmod(0o600)
    (base / "etc" / "units").mkdir()

    panel_log = open(work / "panel.log", "w")
    agent_log = open(work / "agent.log", "w")
    panel = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(panel_port), "--log-level", "warning"],
        cwd=BACKEND, env=env, stdout=panel_log, stderr=subprocess.STDOUT,
    )
    agent = None
    try:
        check("panel_healthz", wait_for_health(base_url))
        client = Panel(base_url)
        client.login()
        check("login", True)
        agent = subprocess.Popen(
            [str(agent_path), "serve", "--base", str(base), "--log-level", "info"],
            env={**os.environ, "VPN_AGENT_BASE": str(base)},
            stdout=agent_log, stderr=subprocess.STDOUT,
        )

        server = client.wait(
            f"/api/v1/servers/{server_id}",
            lambda body: isinstance(body, dict) and body.get("status") == "online",
        )
        check("agent_register_online", True, f"status={server['status']}")
        check("agent_version_reported", bool(server.get("agent_version")), str(server.get("agent_version")))

        revision = client.wait(
            f"/api/v1/routing/revisions/{revision_id}",
            lambda body: isinstance(body, dict) and body.get("status") == "applied",
        )
        check("config_applied_via_agent", True, f"applied_at={revision.get('applied_at')}")
        check("config_sha256_present", bool(revision.get("hashes")))

        metrics = client.wait(
            f"/api/v1/metrics/node/{server_id}",
            lambda body: isinstance(body, dict) and int(body.get("total_bytes") or 0) > 0,
            timeout=45,
        )
        check("metrics_deltas_ingested", int(metrics.get("total_bytes") or 0) > 0, f"total_bytes={metrics.get('total_bytes')}")

        status, action, _ = client.request(
            "POST", f"/api/v1/servers/{server_id}/actions", {"action": "diagnose"}
        )
        check("diagnose_command_queued", status == 202, f"http={status}")
        job_id = int(action["job_id"])
        job = client.wait(
            f"/api/v1/jobs/{job_id}",
            lambda body: isinstance(body, dict) and body.get("status") in {"succeeded", "failed"},
        )
        check("diagnose_command_result", job.get("status") == "succeeded", f"status={job.get('status')}")
        diagnostics = client.request("GET", f"/api/v1/servers/{server_id}/diagnostics")[1]
        check(
            "diagnostics_report_stored",
            isinstance(diagnostics, dict) and diagnostics.get("report") is not None,
        )

        agent_info = client.request("GET", "/api/v1/servers")[1]
        check("agent_online_in_list", any(item["id"] == server_id and item["status"] == "online" for item in agent_info))
    finally:
        for process in (agent, panel):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        panel_log.close()
        agent_log.close()
        if args.keep:
            print(f"stand directory kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print()
    print(f"e2e-node: PASS={PASS} FAIL={FAIL}")
    if FAILED:
        print("failed checks: " + ", ".join(FAILED))
        return 1
    print("e2e-node: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
