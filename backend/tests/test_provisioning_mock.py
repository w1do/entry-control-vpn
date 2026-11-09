from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.crypto import SecretBox
from app.core.db import create_all, dispose_engine, get_session_factory, init_engine
from app.models.ops import Job
from app.models.servers import Server, ServerSecret
from app.services import ssh
from app.services.provisioning import execute_job
from app.services.reality import generate_x25519, new_short_id
from tests.conftest import make_settings

HOME = "/root"
BASE = f"{HOME}/config/vpn"


class FakeSFTP:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def __aenter__(self) -> FakeSFTP:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def put(self, local_path: str, remote_path: str) -> None:
        self.files[remote_path] = Path(local_path).read_bytes()

    async def chmod(self, remote_path: str, mode: int) -> None:
        return None


class FakeConn:
    def __init__(self, files: dict[str, bytes], fail_test: bool = False) -> None:
        self.files = files
        self.fail_test = fail_test
        self.commands: list[str] = []

    def start_sftp_client(self) -> FakeSFTP:
        return FakeSFTP(self.files)

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _cleanup_engine():
    asyncio.run(dispose_engine())
    yield
    asyncio.run(dispose_engine())


def _prepare(tmp_path) -> tuple[object, SecretBox, int, int]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for name in ("xray-linux-amd64", "vpn-agent-linux-amd64"):
        (artifacts / name).write_bytes(b"#!/bin/sh\necho fake-binary\n")
    (artifacts / "geoip.dat").write_bytes(b"geoip")
    (artifacts / "geosite.dat").write_bytes(b"geosite")
    settings = make_settings(tmp_path)
    settings = settings.model_copy(update={"artifacts_dir": str(artifacts)})
    box = SecretBox(settings.encryption_key)
    private_key, public_key = generate_x25519()

    async def run() -> tuple[int, int]:
        init_engine(settings)
        await create_all()
        async with get_session_factory()() as db:
            server = Server(
                name="node-mock",
                host="203.0.113.55",
                ssh_user="root",
                auth_type="password",
                secret_enc=box.encrypt("ssh-password"),
                roles="entry",
                status="new",
            )
            db.add(server)
            await db.flush()
            db.add(
                ServerSecret(
                    server_id=server.id,
                    reality_private_key_enc=box.encrypt(private_key),
                    reality_public_key=public_key,
                    short_ids_json=[new_short_id()],
                    interconnect_uuid_enc=box.encrypt("00000000-0000-4000-8000-000000000001"),
                    real_dest="www.microsoft.com",
                    real_sni="www.microsoft.com",
                )
            )
            job = Job(type="provision", server_id=server.id, status="queued", payload_json={})
            db.add(job)
            await db.commit()
            return server.id, job.id

    server_id, job_id = asyncio.run(run())
    return settings, box, server_id, job_id


def _assert_state(server_id: int, job_id: int, expect_status: str) -> tuple[Server, Job]:
    async def run() -> tuple[Server, Job]:
        async with get_session_factory()() as db:
            server = await db.get(Server, server_id)
            job = await db.get(Job, job_id)
            assert server is not None and job is not None
            db.expunge(server)
            db.expunge(job)
            return server, job

    return asyncio.run(run())


def test_provision_job_success_with_fake_ssh(tmp_path, monkeypatch) -> None:
    settings, box, server_id, job_id = _prepare(tmp_path)
    files: dict[str, bytes] = {}

    class SuccessConn(FakeConn):
        async def run(self, command: str, check: bool = False, timeout: float = 120, input=None):
            self.commands.append(command)
            if "echo $HOME" in command:
                return ssh.SSHResult(0, HOME + "\n", "")
            if "xray" in command and "-test" in command:
                return ssh.SSHResult(0, "Configuration OK.", "")
            if "systemctl" in command:
                return ssh.SSHResult(0, "active\nactive\n", "")
            return ssh.SSHResult(0, "", "")

    conn = SuccessConn(files)

    async def fake_run(c, command, *, sudo=False, input_data=None, timeout=120):
        return await c.run(command)

    monkeypatch.setattr(ssh, "connect", lambda **kwargs: _async(conn))
    monkeypatch.setattr(ssh, "run", fake_run)

    async def fake_put_text(c, path, content, *, mode=0o600, sudo=False, timeout=60):
        files[path] = content.encode()

    monkeypatch.setattr(ssh, "put_text", fake_put_text)
    async def fake_put_file(c, local_path, remote_path, *, mode=0o644, sudo=False):
        files[remote_path] = Path(local_path).read_bytes()

    monkeypatch.setattr(ssh, "put_file", fake_put_file)

    asyncio.run(execute_job(job_id, settings, box, get_session_factory()))
    server, job = _assert_state(server_id, job_id, "succeeded")
    assert job.status == "succeeded", job.error
    # Provisioning no longer claims "online" before the agent registers: the
    # node is reachable but the panel has not seen a heartbeat yet.
    assert server.status == "degraded"
    assert server.degraded_reason
    assert server.agent_token_hash
    assert server.base_dir == BASE
    # Binaries are written next to the running file and moved into place, so a
    # reinstall over a live system cannot fail with ETXTBSY.
    assert f"{BASE}/bin/xray.new" in files
    assert f"{BASE}/bin/vpn-agent.new" in files
    assert any(f"mv -f {BASE}/bin/xray.new {BASE}/bin/xray" in cmd for cmd in conn.commands)
    assert f"{BASE}/etc/geoip.dat" in files
    assert f"{BASE}/etc/geosite.dat" in files
    assert f"{BASE}/etc/xray.json" in files
    assert f"{BASE}/etc/agent.json" in files
    assert "/etc/systemd/system/xray.service" in files
    assert "/etc/systemd/system/vpn-agent.service" in files
    xray_unit = files["/etc/systemd/system/xray.service"].decode()
    assert f"XRAY_LOCATION_ASSET={BASE}/etc" in xray_unit
    agent_cfg = files[f"{BASE}/etc/agent.json"].decode()
    assert "\"panel_url\"" in agent_cfg
    assert "\"token\"" in agent_cfg
    # xray -test must run with asset location
    assert any("XRAY_LOCATION_ASSET=" in command for command in conn.commands)


def test_provision_job_fails_when_xray_test_fails(tmp_path, monkeypatch) -> None:
    settings, box, server_id, job_id = _prepare(tmp_path)
    files: dict[str, bytes] = {}

    class FailConn(FakeConn):
        async def run(self, command: str, check: bool = False, timeout: float = 120, input=None):
            self.commands.append(command)
            if "echo $HOME" in command:
                return ssh.SSHResult(0, HOME + "\n", "")
            if "-test" in command:
                return ssh.SSHResult(1, "", "invalid config")
            return ssh.SSHResult(0, "", "")

    conn = FailConn(files, fail_test=True)

    async def fake_run(c, command, *, sudo=False, input_data=None, timeout=120):
        return await c.run(command)

    monkeypatch.setattr(ssh, "connect", lambda **kwargs: _async(conn))
    monkeypatch.setattr(ssh, "run", fake_run)

    async def fake_put_text(c, path, content, *, mode=0o600, sudo=False, timeout=60):
        files[path] = content.encode()

    monkeypatch.setattr(ssh, "put_text", fake_put_text)
    async def fake_put_file(c, local_path, remote_path, *, mode=0o644, sudo=False):
        files[remote_path] = Path(local_path).read_bytes()

    monkeypatch.setattr(ssh, "put_file", fake_put_file)

    asyncio.run(execute_job(job_id, settings, box, get_session_factory()))
    server, job = _assert_state(server_id, job_id, "failed")
    assert job.status == "failed"
    assert "xray -test" in (job.error or "")
    assert server.status == "failed"
    assert server.agent_token_hash is None


def test_provision_job_fails_without_artifacts(tmp_path, monkeypatch) -> None:
    settings, box, server_id, job_id = _prepare(tmp_path)
    (Path(settings.artifacts_dir) / "xray-linux-amd64").unlink()
    conn = FakeConn({})
    files: dict[str, bytes] = {}

    async def fake_run(c, command, *, sudo=False, input_data=None, timeout=120):
        return ssh.SSHResult(0, HOME + "\n", "")

    monkeypatch.setattr(ssh, "connect", lambda **kwargs: _async(conn))
    monkeypatch.setattr(ssh, "run", fake_run)

    async def fake_put_text(c, path, content, *, mode=0o600, sudo=False, timeout=60):
        return None

    monkeypatch.setattr(ssh, "put_text", fake_put_text)
    async def fake_put_file(c, local_path, remote_path, *, mode=0o644, sudo=False):
        files[remote_path] = Path(local_path).read_bytes()

    monkeypatch.setattr(ssh, "put_file", fake_put_file)

    asyncio.run(execute_job(job_id, settings, box, get_session_factory()))
    server, job = _assert_state(server_id, job_id, "failed")
    assert job.status == "failed"
    assert "Артефакты узла не найдены" in (job.error or "")
    assert server.status == "failed"


def _async(value):
    async def wrapper():
        return value

    return wrapper()
