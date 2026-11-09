from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

import asyncssh

CONNECT_TIMEOUT = 15


class SSHConnectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SSHResult:
    exit_status: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_status == 0


def _client_keys(private_key: str | None) -> list[asyncssh.SSHKey] | None:
    if not private_key:
        return None
    try:
        return [asyncssh.import_private_key(private_key)]
    except Exception as exc:  # noqa: BLE001
        raise SSHConnectionError("invalid_private_key", f"Не удалось прочитать приватный ключ: {exc}") from exc


async def connect(
    *,
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    private_key: str | None = None,
    known_host_key: bytes | None = None,
) -> asyncssh.SSHClientConnection:
    try:
        return await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            client_keys=_client_keys(private_key),
            known_hosts=None if known_host_key is None else [(host, known_host_key)],
            login_timeout=CONNECT_TIMEOUT,
            keepalive_interval=30,
            connect_timeout=CONNECT_TIMEOUT,
        )
    except (asyncssh.PermissionDenied, asyncssh.DisconnectError) as exc:
        raise SSHConnectionError("auth_failed", f"SSH-аутентификация не удалась: {exc}") from exc
    except (OSError, TimeoutError, asyncssh.Error) as exc:
        raise SSHConnectionError("host_unreachable", f"SSH недоступен: {exc}") from exc


def host_key_fingerprint(conn: asyncssh.SSHClientConnection) -> str:
    key = conn.get_server_host_key()
    if key is None:
        return ""
    data = key.export_public_key()
    return "SHA256:" + hashlib.sha256(data).hexdigest()


async def run(
    conn: asyncssh.SSHClientConnection,
    command: str,
    *,
    sudo: bool = False,
    input_data: str | bytes | None = None,
    timeout: float = 120,
) -> SSHResult:
    full = f"sudo -n {command}" if sudo else command
    try:
        result = await conn.run(full, input=input_data, check=False, timeout=timeout)
    except asyncssh.ProcessError as exc:
        return SSHResult(exit_status=1, stdout=str(exc.stdout or ""), stderr=str(exc.stderr or ""))
    return SSHResult(
        exit_status=result.exit_status,
        stdout=str(result.stdout or ""),
        stderr=str(result.stderr or ""),
    )


async def put_bytes(
    conn: asyncssh.SSHConnection,
    path: str,
    data: bytes,
    *,
    mode: int = 0o600,
    sudo: bool = False,
    timeout: float = 300,
) -> None:
    """Upload bytes without SFTP: stream them into "tee" over the exec channel.

    This works on hardened nodes where the SFTP subsystem is disabled and keeps
    the provisioning path independent from optional sshd features.
    """
    encoded = base64.b64encode(data).decode()
    if sudo:
        command = f"base64 -d | sudo -n tee {path} >/dev/null"
    else:
        command = f"base64 -d > {path}"
    result = await run(conn, command, input_data=encoded, timeout=timeout)
    if not result.ok:
        raise SSHConnectionError("upload_failed", f"Не удалось записать {path}: {result.stderr[:400]}")
    chmod_result = await run(conn, f"chmod {mode:o} {path}", sudo=sudo, timeout=30)
    if not chmod_result.ok:
        raise SSHConnectionError("upload_failed", f"chmod {path}: {chmod_result.stderr[:400]}")


async def put_file(
    conn: asyncssh.SSHConnection,
    local_path: str | Path,
    remote_path: str,
    *,
    mode: int = 0o644,
    sudo: bool = False,
) -> None:
    data = Path(local_path).read_bytes()
    await put_bytes(conn, remote_path, data, mode=mode, sudo=sudo)


async def put_text(
    conn: asyncssh.SSHConnection,
    path: str,
    content: str,
    *,
    mode: int = 0o600,
    sudo: bool = False,
    timeout: float = 60,
) -> None:
    await put_bytes(conn, path, content.encode(), mode=mode, sudo=sudo, timeout=timeout)


async def preflight(
    *,
    host: str,
    port: int,
    username: str,
    password: str | None,
    private_key: str | None,
) -> dict:
    conn = await connect(
        host=host, port=port, username=username, password=password, private_key=private_key
    )
    try:
        os_release = await run(conn, "cat /etc/os-release 2>/dev/null | head -8")
        uname = await run(conn, "uname -m && uname -r")
        uid = await run(conn, "id -u")
        sudo = await run(conn, "command -v sudo >/dev/null && sudo -n true 2>/dev/null; echo $?")
        port443 = await run(conn, "ss -lnt 2>/dev/null | grep -c ':443 ' || true")
        existing = await run(conn, "test -d \"$HOME/config/vpn\" && echo present || echo absent")
        os_text = os_release.stdout.strip()
        pretty = "Linux"
        for line in os_text.splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
        arch = uname.stdout.splitlines()[0].strip() if uname.stdout.splitlines() else "unknown"
        kernel = uname.stdout.splitlines()[1].strip() if len(uname.stdout.splitlines()) > 1 else ""
        is_root = uid.stdout.strip() == "0"
        sudo_ok = is_root or sudo.stdout.strip() == "0"
        return {
            "ok": True,
            "os": pretty,
            "kernel": kernel,
            "arch": arch,
            "root": is_root,
            "sudo": sudo_ok,
            "port_443_free": port443.stdout.strip().strip() == "0",
            "existing_install": existing.stdout.strip() == "present",
            "host_fingerprint": host_key_fingerprint(conn),
            "warnings": [] if sudo_ok else ["Нет root/sudo — провижининг невозможен"],
        }
    finally:
        conn.close()
        await conn.wait_closed()
