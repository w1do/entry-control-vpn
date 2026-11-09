from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.security import new_token, sha256_hex
from app.models.base import utcnow
from app.models.ops import Job
from app.models.servers import Server, ServerSecret
from app.services import ssh
from app.services.jobs import append_event, finish_job, start_job

AGENT_UNIT = """[Unit]
Description=VPN node agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={base}/bin/vpn-agent serve
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=no
ReadWritePaths={base}
PrivateTmp=yes
UMask=0077
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""

XRAY_UNIT = """[Unit]
Description=VPN Xray node
After=network-online.target nss-lookup.target
Wants=network-online.target

[Service]
Type=simple
Environment=XRAY_LOCATION_ASSET={base}/etc
ExecStart={base}/bin/xray run -c {base}/etc/xray.json
Restart=on-failure
RestartPreventExitStatus=23
RestartSec=2
LimitNOFILE=1000000
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=no
ReadWritePaths={base}
PrivateTmp=yes
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def bootstrap_xray_config(secret: ServerSecret, base_dir: str) -> dict[str, Any]:
    return {
        "log": {
            "loglevel": "warning",
            "access": f"{base_dir}/log/access.log",
            "error": f"{base_dir}/log/xray-error.log",
        },
        "inbounds": [
            {
                "tag": "vless-in",
                "listen": "0.0.0.0",
                "port": 443,
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": f"{secret.real_dest}:443",
                        "xver": 0,
                        "serverNames": [secret.real_sni],
                        "privateKey": "__PRIVATE_KEY__",
                        "shortIds": secret.short_ids_json or [""],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
            }
        ],
        "outbounds": [
            {"tag": "block", "protocol": "blackhole"},
            {"tag": "direct", "protocol": "freedom"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "block"}],
        },
        "api": {
            "tag": "api",
            "listen": "127.0.0.1:10085",
            "services": ["HandlerService", "LoggerService", "StatsService"],
        },
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                    "statsUserOnline": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
    }


class ProvisionError(Exception):
    pass


async def _artifacts(settings: Settings) -> tuple[Path, Path, Path, Path]:
    artifacts = Path(settings.artifacts_dir)
    xray = artifacts / "xray-linux-amd64"
    agent = artifacts / "vpn-agent-linux-amd64"
    geoip = artifacts / "geoip.dat"
    geosite = artifacts / "geosite.dat"
    missing = [str(path) for path in (xray, agent, geoip, geosite) if not path.is_file()]
    if missing:
        raise ProvisionError(
            "Артефакты узла не найдены: "
            + ", ".join(missing)
            + ". Соберите vpn-agent (make agent), положите Xray и geodata в artifacts "
            "(см. deploy/README или Makefile target artifacts)."
        )
    return xray, agent, geoip, geosite


async def run_provision_job(
    job_id: int,
    server_id: int,
    settings: Settings,
    secret_box: SecretBox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        server = await db.get(Server, server_id)
        if job is None or server is None:
            return
        secret = server.secret
        if secret is None:
            await finish_job(db, job, status="failed", error="у сервера нет секретов REALITY")
            await db.commit()
            return
        server.status = "provisioning"
        await start_job(db, job, progress=5)
        await db.commit()

    conn = None
    try:
        await _event(session_factory, job_id, progress=10, step="ssh_connect", message="Подключение по SSH")
        if server.auth_type == "key":
            private_key = secret_box.decrypt(server.secret_enc) if server.secret_enc else None
            password = None
        else:
            password = secret_box.decrypt(server.secret_enc) if server.secret_enc else None
            private_key = None
        conn = await ssh.connect(
            host=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=password,
            private_key=private_key,
        )

        home = await ssh.run(conn, "bash -lc 'echo $HOME'")
        base_dir = home.stdout.strip() or "/root"
        base = f"{base_dir}/config/vpn"
        server.base_dir = base

        await _event(session_factory, job_id, progress=15, step="dirs", message=f"Создание {base}")
        mk = await ssh.run(conn, f"mkdir -p {base}/bin {base}/etc {base}/log {base}/state && chmod 700 {base}")
        if not mk.ok:
            raise ProvisionError(f"mkdir: {mk.stderr[:400]}")

        xray_bin, agent_bin, geoip_dat, geosite_dat = await _artifacts(settings)

        await _event(session_factory, job_id, progress=25, step="upload_xray", message="Загрузка Xray")
        async with conn.start_sftp_client() as sftp:
            await sftp.put(str(xray_bin), f"{base}/bin/xray")
            await sftp.chmod(f"{base}/bin/xray", 0o755)

        await _event(session_factory, job_id, progress=30, step="upload_geodata", message="Загрузка geosite/geoip")
        async with conn.start_sftp_client() as sftp:
            await sftp.put(str(geoip_dat), f"{base}/etc/geoip.dat")
            await sftp.put(str(geosite_dat), f"{base}/etc/geosite.dat")
            await sftp.chmod(f"{base}/etc/geoip.dat", 0o644)
            await sftp.chmod(f"{base}/etc/geosite.dat", 0o644)

        await _event(session_factory, job_id, progress=35, step="upload_agent", message="Загрузка vpn-agent")
        agent_token = new_token(32)
        async with conn.start_sftp_client() as sftp:
            await sftp.put(str(agent_bin), f"{base}/bin/vpn-agent")
            await sftp.chmod(f"{base}/bin/vpn-agent", 0o755)

        agent_cfg = {
            "panel_url": settings.public_base_url,
            "token": agent_token,
            "server_id": server.id,
            "base_dir": base,
            "heartbeat_interval_s": 30,
            "config_poll_s": 10,
            "metrics_interval_s": 15,
        }
        await ssh.put_text(conn, f"{base}/etc/agent.json", json.dumps(agent_cfg, indent=2), mode=0o600)

        await _event(session_factory, job_id, progress=45, step="config", message="Запись начального конфига Xray")
        private_key_reality = (
            secret_box.decrypt(secret.reality_private_key_enc) if secret.reality_private_key_enc else ""
        )
        config = bootstrap_xray_config(secret, base)
        config["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] = private_key_reality
        await ssh.put_text(conn, f"{base}/etc/xray.json", json.dumps(config, indent=2), mode=0o600)

        await _event(session_factory, job_id, progress=55, step="units", message="Установка systemd-юнитов")
        unit_dir = "/etc/systemd/system"
        await ssh.put_text(conn, f"{unit_dir}/xray.service", XRAY_UNIT.format(base=base), mode=0o644, sudo=True)
        await ssh.put_text(conn, f"{unit_dir}/vpn-agent.service", AGENT_UNIT.format(base=base), mode=0o644, sudo=True)

        await _event(session_factory, job_id, progress=65, step="validate", message="Проверка конфигурации Xray")
        test = await ssh.run(
            conn,
            f"XRAY_LOCATION_ASSET={base}/etc {base}/bin/xray run -test -c {base}/etc/xray.json",
            timeout=60,
        )
        if not test.ok:
            raise ProvisionError(f"xray -test failed: {test.stderr[:800]}")

        await _event(session_factory, job_id, progress=75, step="firewall", message="Настройка firewall (best-effort)")
        fw = await ssh.run(
            conn,
            "if command -v ufw >/dev/null 2>&1; then ufw allow 22/tcp >/dev/null 2>&1; "
            "ufw allow 443/tcp >/dev/null 2>&1; ufw allow 18443/tcp >/dev/null 2>&1; echo ufw; "
            "elif command -v nft >/dev/null 2>&1; then echo nft; else echo none; fi",
        )
        fw_kind = fw.stdout.strip() or "none"

        await _event(session_factory, job_id, progress=85, step="start", message="Запуск сервисов")
        start = await ssh.run(
            conn,
            "systemctl daemon-reload && systemctl enable --now xray.service vpn-agent.service && "
            "systemctl is-active xray.service vpn-agent.service",
            sudo=True,
            timeout=120,
        )
        if not start.ok:
            logs = await ssh.run(conn, "journalctl -u xray -n 40 --no-pager 2>&1 | tail -40", sudo=True)
            raise ProvisionError(f"Не удалось запустить юниты: {start.stderr[:400]}\n{logs.stdout[-1200:]}")

        async with session_factory() as db:
            server_row = await db.get(Server, server_id)
            if server_row is not None:
                server_row.agent_token_hash = sha256_hex(agent_token)
                server_row.status = "online"
                server_row.base_dir = base
                server_row.updated_at = utcnow()
            job_row = await db.get(Job, job_id)
            if job_row is not None:
                await finish_job(
                    db,
                    job_row,
                    status="succeeded",
                    result={
                        "base_dir": base,
                        "firewall": fw_kind,
                        "xray_test": "ok",
                        "services": start.stdout.strip().splitlines(),
                    },
                )
            await append_event(db, job_id, step="waiting_agent", message="Ожидание первого heartbeat агента")
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - report any provisioning failure to the job
        message = str(exc)
        async with session_factory() as db:
            server_row = await db.get(Server, server_id)
            if server_row is not None:
                server_row.status = "failed"
            job_row = await db.get(Job, job_id)
            if job_row is not None:
                await finish_job(db, job_row, status="failed", error=message[:2000])
            await db.commit()
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


async def _event(session_factory, job_id: int, *, progress: int, step: str, message: str) -> None:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            job.progress = progress
            await append_event(db, job_id, step=step, message=message)
        await db.commit()


async def run_uninstall_job(
    job_id: int,
    server_id: int,
    settings: Settings,
    secret_box: SecretBox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        server = await db.get(Server, server_id)
        if job is None or server is None:
            return
        server.status = "delete_pending"
        await start_job(db, job, progress=5)
        await db.commit()

    conn = None
    try:
        if server.auth_type == "key":
            private_key = secret_box.decrypt(server.secret_enc) if server.secret_enc else None
            password = None
        else:
            password = secret_box.decrypt(server.secret_enc) if server.secret_enc else None
            private_key = None
        await _event(session_factory, job_id, progress=15, step="ssh_connect", message="Подключение по SSH")
        conn = await ssh.connect(
            host=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=password,
            private_key=private_key,
        )
        await _event(session_factory, job_id, progress=35, step="uninstall", message="Остановка сервисов и удаление каталога")
        result = await ssh.run(
            conn,
            f"{server.base_dir}/bin/vpn-agent uninstall --yes 2>&1 || true",
            sudo=True,
            timeout=120,
        )
        check = await ssh.run(
            conn,
            "systemctl is-enabled xray.service vpn-agent.service 2>/dev/null; "
            'test -d "$HOME/config/vpn" && echo dir_present || echo dir_absent',
            sudo=True,
        )
        output = (result.stdout or "") + (check.stdout or "")
        if "dir_present" in output:
            raise ProvisionError("каталог ~/config/vpn всё ещё существует после uninstall: " + output[-600:])
        async with session_factory() as db:
            job_row = await db.get(Job, job_id)
            server_row = await db.get(Server, server_id)
            if job_row is not None:
                await finish_job(db, job_row, status="succeeded", result={"output": output[-2000:]})
            if server_row is not None:
                await db.delete(server_row)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        async with session_factory() as db:
            job_row = await db.get(Job, job_id)
            server_row = await db.get(Server, server_id)
            if server_row is not None and server_row.status == "delete_pending":
                pass
            if job_row is not None:
                await finish_job(db, job_row, status="failed", error=str(exc)[:2000])
            await db.commit()
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


async def execute_job(
    job_id: int,
    settings: Settings,
    secret_box: SecretBox,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        job_type = job.type
        server_id = job.server_id
    if job_type in {"provision", "reinstall"} and server_id is not None:
        await run_provision_job(job_id, server_id, settings, secret_box, session_factory)
        return
    if job_type == "uninstall" and server_id is not None:
        await run_uninstall_job(job_id, server_id, settings, secret_box, session_factory)
        return
    if job_type == "apply":
        async with session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None:
                await finish_job(
                    db,
                    job,
                    status="succeeded",
                    result={"revision_id": (job.payload_json or {}).get("revision_id"), "dispatched": True},
                )
                await append_event(
                    db,
                    job_id,
                    step="dispatched",
                    message="Конфигурация сохранена; агенты применят её при следующем опросе",
                )
            await db.commit()
        return
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            await finish_job(db, job, status="failed", error=f"job type {job_type} ещё не реализован")
            await db.commit()
