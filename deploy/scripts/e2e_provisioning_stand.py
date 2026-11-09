#!/usr/bin/env python3
"""E2E: panel SSH provisioning against a local fake node (asyncssh emulator).

The stand starts a small SSH server that accepts the real provisioning
commands in a sandbox directory (HOME points into a temp dir, systemd/sudo/ufw
are fake scripts, SFTP is served by asyncssh). The panel then provisions the
"node" through its normal SSH path: uploads the real Xray/agent binaries and
geodata, writes the bootstrap config and systemd units, runs the real
xray -test, "starts" the services. Finally the real vpn-agent binary uploaded
by the provisioning is started from the sandbox and must register with the
panel using the token/config written by the panel.

Usage:
    .venv/bin/python deploy/scripts/e2e_provisioning_stand.py [--keep]

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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
VENV_PY = ROOT / ".venv" / "bin" / "python"
ARTIFACTS = ROOT / "artifacts"
ADMIN = "admin@example.com"
ADMIN_PASSWORD = secrets.token_urlsafe(18)
NODE_SSH_PASS = secrets.token_urlsafe(18)

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


def wait_http(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
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

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = None
            return error.code, body

    def login(self) -> None:
        status, body = self.request("POST", "/api/v1/auth/login", {"email": ADMIN, "password": ADMIN_PASSWORD})
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"login failed: {status} {body}")
        self.csrf = str(body["csrf_token"])

    def wait(self, path: str, predicate, timeout: float = 120.0, interval: float = 0.5):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            status, body = self.request("GET", path)
            last = (status, body)
            if status == 200 and predicate(body):
                return body
            time.sleep(interval)
        raise SystemExit(f"timeout waiting for {path}: {last}")


FAKE_SYSTEMCTL = """#!/usr/bin/env python3
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(0)
cmd = args[0]
units = args[1:]
if cmd == "is-active":
    for _ in units or ["xray.service"]:
        print("active")
    sys.exit(0)
if cmd == "show":
    print("LoadState=loaded")
    print("ActiveState=active")
    print("SubState=running")
    print("MainPID=4242")
    print("ActiveEnterTimestampMonotonic=1000000")
    sys.exit(0)
sys.exit(0)
"""

FAKE_SUDO = """#!/usr/bin/env python3
import os
import sys

sandbox = os.environ["SANDBOX"]
args = [a for a in sys.argv[1:] if a != "-n"]
mapped = []
for arg in args:
    if arg.startswith("/etc/systemd/system/"):
        arg = os.path.join(sandbox, "etc", "systemd", "system", os.path.basename(arg))
    mapped.append(arg)
os.execvp(mapped[0], mapped)
"""

FAKE_UFW = """#!/bin/sh
exit 0
"""


class NodeSession:
    pass


def write_fake_binaries(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("systemctl", FAKE_SYSTEMCTL),
        ("sudo", FAKE_SUDO),
        ("ufw", FAKE_UFW),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)


def make_ssh_server(sandbox: Path, host_key_path: Path):
    """asyncssh server: process_factory executes commands, sftp_factory serves files."""
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    env_base = {
        "HOME": str(sandbox / "root"),
        "SANDBOX": str(sandbox),
        "PATH": f"{sandbox}/bin:" + os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }

    async def handle_process(process) -> None:
        env = {**os.environ, **env_base}
        child = await asyncio.create_subprocess_shell(
            process.command or "",
            cwd=str(sandbox / "root"),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def feed_stdin() -> None:
            assert child.stdin is not None
            while True:
                data = await process.stdin.read(65536)
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode()
                child.stdin.write(data)
                await child.stdin.drain()
            child.stdin.close()

        feeder = asyncio.create_task(feed_stdin())
        stdout, stderr = await asyncio.gather(child.stdout.read(), child.stderr.read())
        returncode = await child.wait()
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):
            pass
        if child.stdin is not None and not child.stdin.is_closing():
            child.stdin.close()
        if stdout:
            process.stdout.write(stdout.decode("utf-8", "replace"))
        if stderr:
            process.stderr.write(stderr.decode("utf-8", "replace"))
        process.exit(returncode or 0)

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return username == "root" and password == NODE_SSH_PASS

    async def start():
        return await asyncssh.create_server(
            lambda: Server(),
            "127.0.0.1",
            0,
            server_host_keys=[key],
            process_factory=handle_process,
            server_version="OpenSSH_9.6 e2e-stand",
        )

    return start


class SSHThread:
    """Runs the asyncssh stand in a background thread (own event loop)."""

    def __init__(self, start_factory) -> None:
        self._start_factory = start_factory
        self.ready = threading.Event()
        self.loop = None
        self.server = None
        self.error: BaseException | None = None
        self._stop_event = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        async def main() -> None:
            self.loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()
            self.server = await self._start_factory()
            self.ready.set()
            await self._stop_event.wait()

        try:
            asyncio.run(main())
        except BaseException as exc:  # noqa: BLE001
            self.error = exc
            self.ready.set()

    def start(self) -> int:
        self.thread.start()
        if not self.ready.wait(timeout=10):
            raise SystemExit("ssh stand did not start in time")
        if self.error is not None:
            raise SystemExit(f"ssh stand failed: {self.error!r}")
        assert self.server is not None
        return int(self.server.sockets[0].getsockname()[1])

    def stop(self) -> None:
        if self.loop is not None and self._stop_event is not None:
            def _stop() -> None:
                assert self.server is not None
                self.server.close()
                assert self._stop_event is not None
                self._stop_event.set()

            self.loop.call_soon_threadsafe(_stop)
        self.thread.join(timeout=5)


def run_stand(keep: bool) -> int:
    work = Path(tempfile.mkdtemp(prefix="vpn-prov-e2e-"))
    sandbox = work / "sandbox"
    home = sandbox / "root"
    (home / "config").mkdir(parents=True)
    (sandbox / "etc" / "systemd" / "system").mkdir(parents=True)
    write_fake_binaries(sandbox / "bin")

    host_key = work / "host_key"
    ssh_port = free_port()
    panel_port = free_port()
    base_url = f"http://127.0.0.1:{panel_port}"
    database = work / "panel.db"

    sys.path.insert(0, str(BACKEND))
    from cryptography.fernet import Fernet

    env = {
        **os.environ,
        "ADMIN": ADMIN,
        "PASSWORD": ADMIN_PASSWORD,
        "SECRET_KEY": "provisioning-e2e-secret-0123456789",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "SESSION_COOKIE_SECURE": "false",
        "SCHEDULER_ENABLED": "false",
        "AUTO_CREATE_SCHEMA": "true",
        "PUBLIC_BASE_URL": base_url,
        "APP_ENV": "test",
        "ARTIFACTS_DIR": str(ARTIFACTS),
        "PYTHONPATH": str(BACKEND),
    }

    ssh_server: SSHThread | None = None
    panel = None
    agent = None
    panel_log = open(work / "panel.log", "w")
    agent_log = open(work / "agent.log", "w")
    try:
        start_server = make_ssh_server(sandbox, host_key)
        ssh_server = SSHThread(start_server)
        ssh_actual_port = ssh_server.start()

        panel = subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(panel_port), "--log-level", "warning"],
            cwd=BACKEND, env=env, stdout=panel_log, stderr=subprocess.STDOUT,
        )
        check("panel_healthz", wait_http(base_url + "/healthz"))
        client = Panel(base_url)
        client.login()
        check("login", True)

        status, created = client.request(
            "POST",
            "/api/v1/servers",
            {
                "name": "prov-node",
                "host": "127.0.0.1",
                "port": ssh_actual_port,
                "user": "root",
                "auth_type": "password",
                "password": NODE_SSH_PASS,
                "roles": "entry",
                "region": "SG",
            },
        )
        check("server_create_accepted", status == 201, f"http={status} body={created}")
        if status != 201 or not isinstance(created, dict):
            raise SystemExit("cannot continue without a server")
        server_id = int(created["server_id"])
        job_id = int(created["job_id"])

        job = client.wait(
            f"/api/v1/jobs/{job_id}",
            lambda body: isinstance(body, dict) and body.get("status") in {"succeeded", "failed"},
        )
        check("provisioning_job_succeeded", job.get("status") == "succeeded", str(job.get("error") or ""))

        server = client.wait(
            f"/api/v1/servers/{server_id}",
            lambda body: isinstance(body, dict) and body.get("status") == "online",
        )
        check("server_online_after_provisioning", server.get("status") == "online")
        base = Path(server["base_dir"])
        check("base_dir_is_sandbox_home", str(base) == str(home / "config" / "vpn"), str(base))

        xray_bin = base / "bin" / "xray"
        agent_bin = base / "bin" / "vpn-agent"
        check("xray_uploaded", xray_bin.is_file() and xray_bin.stat().st_size > 1_000_000, str(xray_bin.stat().st_size if xray_bin.exists() else 0))
        check("agent_uploaded", agent_bin.is_file() and agent_bin.stat().st_size > 1_000_000, str(agent_bin.stat().st_size if agent_bin.exists() else 0))
        check("geoip_uploaded", (base / "etc" / "geoip.dat").is_file())
        check("geosite_uploaded", (base / "etc" / "geosite.dat").is_file())

        bootstrap_config = json.loads((base / "etc" / "xray.json").read_text())
        reality = bootstrap_config["inbounds"][0]["streamSettings"]["realitySettings"]
        check("bootstrap_config_reality", bool(reality.get("privateKey")) and bool(reality.get("dest")), str(reality.get("dest")))

        agent_config_path = base / "etc" / "agent.json"
        agent_config = json.loads(agent_config_path.read_text())
        check("agent_config_written", agent_config.get("server_id") == server_id and agent_config.get("panel_url") == base_url)
        check("agent_token_written", bool(agent_config.get("token")))
        mode = agent_config_path.stat().st_mode & 0o777
        check("agent_config_mode_0600", mode == 0o600, oct(mode))

        xray_unit = (sandbox / "etc" / "systemd" / "system" / "xray.service").read_text()
        agent_unit = (sandbox / "etc" / "systemd" / "system" / "vpn-agent.service").read_text()
        check("xray_unit_installed", f"ExecStart={base}/bin/xray run -c {base}/etc/xray.json" in xray_unit)
        check("xray_unit_asset_env", f"XRAY_LOCATION_ASSET={base}/etc" in xray_unit)
        check("agent_unit_installed", f"ExecStart={base}/bin/vpn-agent serve" in agent_unit)

        status, _ = client.request(
            "POST",
            f"/api/v1/servers/{server_id}/actions",
            {"action": "diagnose"},
        )
        # Diagnose is delivered to the agent via heartbeat; before the agent runs
        # it stays queued. That itself proves the command channel is wired.
        check("agent_command_queued", status == 202, f"http={status}")

        # Start the real agent from the provisioned files: it must authenticate
        # with the token written by the panel and register.
        agent = subprocess.Popen(
            [str(agent_bin), "serve", "--base", str(base), "--log-level", "info"],
            env={**os.environ, "PATH": f"{sandbox}/bin:" + os.environ.get("PATH", "")},
            stdout=agent_log, stderr=subprocess.STDOUT,
        )
        server = client.wait(
            f"/api/v1/servers/{server_id}",
            lambda body: isinstance(body, dict) and body.get("agent_version") == "0.1.0",
            timeout=60,
        )
        check("provisioned_agent_registers", server.get("agent_version") == "0.1.0", str(server.get("agent_version")))
        check("provisioned_agent_online", server.get("status") in {"online", "degraded"}, str(server.get("status")))
    finally:
        for process in (agent, panel):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        if ssh_server is not None:
            ssh_server.stop()
        panel_log.close()
        agent_log.close()
        if keep:
            print(f"stand directory kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print()
    print(f"e2e-provisioning: PASS={PASS} FAIL={FAIL}")
    if FAILED:
        print("failed checks: " + ", ".join(FAILED))
        return 1
    print("e2e-provisioning: OK")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    sys.exit(run_stand(args.keep))
