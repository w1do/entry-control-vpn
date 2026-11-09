from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import AuthContext, get_auth, require_csrf
from app.api.errors import ApiError
from app.core.db import get_session
from app.core.security import new_uuid
from app.core.timeutils import utcnow
from app.models.ops import Event, MetricsBucket
from app.models.servers import Server, ServerSecret
from app.services.jobs import create_job
from app.services.reality import generate_x25519, new_short_id

router = APIRouter(prefix="/api/v1/servers", tags=["servers"])

ROLES = {"entry", "exit", "both"}
ACTIONS = {"reapply", "restart", "reinstall", "diagnose"}
REALITY_DESTS = ["www.microsoft.com", "www.apple.com", "www.cloudflare.com", "www.amazon.com"]


def _server_dict(server: Server, traffic_24h: int = 0, uptime_24h_pct: int | None = None) -> dict[str, Any]:
    secret = server.secret
    return {
        "id": server.id,
        "name": server.name,
        "host": server.host,
        "ssh_port": server.ssh_port,
        "ssh_user": server.ssh_user,
        "auth_type": server.auth_type,
        "roles": server.roles,
        "region": server.region,
        "status": server.status,
        "agent_version": server.agent_version,
        "xray_version": server.xray_version,
        "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "host_fingerprint": server.host_fingerprint,
        "revision_id": server.revision_id,
        "degraded_reason": server.degraded_reason,
        "base_dir": server.base_dir,
        "traffic_24h": traffic_24h,
        "uptime_24h_pct": uptime_24h_pct,
        "reality": None
        if secret is None
        else {
            "public_key": secret.reality_public_key,
            "dest": secret.real_dest,
            "sni": secret.real_sni,
            "short_ids": secret.short_ids_json,
        },
    }


async def _traffic_map(db: AsyncSession, server_ids: list[int]) -> dict[int, int]:
    if not server_ids:
        return {}
    rows = await db.execute(
        select(MetricsBucket.server_id, func.sum(MetricsBucket.up_bytes + MetricsBucket.down_bytes))
        .where(
            MetricsBucket.server_id.in_(server_ids),
            MetricsBucket.client_id.is_(None),
            MetricsBucket.bucket_ts >= utcnow() - timedelta(hours=24),
        )
        .group_by(MetricsBucket.server_id)
    )
    return {int(server_id): int(total or 0) for server_id, total in rows.all()}


async def _uptime_map(db: AsyncSession, server_ids: list[int]) -> dict[int, int]:
    if not server_ids:
        return {}
    rows = await db.execute(
        select(MetricsBucket.server_id, func.count(func.distinct(MetricsBucket.bucket_ts)))
        .where(
            MetricsBucket.server_id.in_(server_ids),
            MetricsBucket.client_id.is_(None),
            MetricsBucket.resolution == "15s",
            MetricsBucket.bucket_ts >= utcnow() - timedelta(hours=24),
        )
        .group_by(MetricsBucket.server_id)
    )
    expected = max(1, (24 * 3600) // 15)
    return {
        int(server_id): min(100, round(int(count or 0) / expected * 100))
        for server_id, count in rows.all()
    }


@router.get("")
async def list_servers(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    servers = list(
        (await db.scalars(select(Server).options(selectinload(Server.secret)).order_by(Server.id))).all()
    )
    traffic = await _traffic_map(db, [server.id for server in servers])
    uptime = await _uptime_map(db, [server.id for server in servers])
    return [
        _server_dict(server, traffic.get(server.id, 0), uptime.get(server.id))
        for server in servers
    ]


@router.post("/preflight")
async def preflight(
    payload: dict,
    auth: AuthContext = Depends(require_csrf),
) -> dict:
    host = str(payload.get("host", "")).strip()
    if not host:
        raise ApiError("invalid_host", "Укажите host", 400)
    try:
        port = int(payload.get("port", 22))
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_port", "Некорректный SSH-порт", 400) from exc
    auth_type = str(payload.get("auth_type", "password"))
    if auth_type not in {"password", "key"}:
        raise ApiError("invalid_auth_type", "auth_type: password или key", 400)
    password = payload.get("password") if auth_type == "password" else None
    private_key = payload.get("private_key") if auth_type == "key" else None
    if auth_type == "password" and not password:
        raise ApiError("missing_password", "Укажите SSH-пароль", 400)
    if auth_type == "key" and not private_key:
        raise ApiError("missing_key", "Укажите приватный SSH-ключ", 400)
    from app.services.ssh import SSHConnectionError
    from app.services.ssh import preflight as ssh_preflight

    try:
        result = await ssh_preflight(
            host=host,
            port=port,
            username=str(payload.get("user", "root")),
            password=password,
            private_key=private_key,
        )
    except SSHConnectionError as exc:
        raise ApiError(exc.code, exc.message, 400) from exc
    return result


@router.post("", status_code=201)
async def create_server(
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    name = str(payload.get("name", "")).strip()
    host = str(payload.get("host", "")).strip()
    roles = str(payload.get("roles", "entry"))
    region = str(payload.get("region", "")).strip() or None
    if not name or not host:
        raise ApiError("invalid_server", "name и host обязательны", 400)
    if roles not in ROLES:
        raise ApiError("invalid_roles", "roles: entry, exit или both", 400)
    if await db.scalar(select(Server.id).where(Server.name == name)) is not None:
        raise ApiError("name_taken", "Сервер с таким именем уже существует", 409)
    auth_type = str(payload.get("auth_type", "password"))
    if auth_type not in {"password", "key"}:
        raise ApiError("invalid_auth_type", "auth_type: password или key", 400)
    secret_value = payload.get("password") if auth_type == "password" else payload.get("private_key")
    if not secret_value:
        raise ApiError("missing_credentials", "Нужны SSH-креды", 400)
    try:
        ssh_port = int(payload.get("port", 22))
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_port", "Некорректный SSH-порт", 400) from exc
    box = request.app.state.secret_box
    server = Server(
        name=name,
        host=host,
        ssh_port=ssh_port,
        ssh_user=str(payload.get("user", "root")),
        auth_type=auth_type,
        secret_enc=box.encrypt(str(secret_value)),
        roles=roles,
        region=region,
        status="new",
        base_dir="/root/config/vpn",
    )
    db.add(server)
    await db.flush()
    private_key, public_key = generate_x25519()
    reality_target = str(payload.get("reality_target", "") or "").strip().lower()
    if reality_target:
        if not re.fullmatch(r"[a-z0-9.-]{1,253}", reality_target) or "." not in reality_target:
            raise ApiError("invalid_reality_target", "reality_target: ожидается домен (например example.com)", 400)
        dest = reality_target
    else:
        dest = REALITY_DESTS[0]
    db.add(
        ServerSecret(
            server_id=server.id,
            reality_private_key_enc=box.encrypt(private_key),
            reality_public_key=public_key,
            short_ids_json=[new_short_id()],
            interconnect_uuid_enc=box.encrypt(new_uuid()),
            real_dest=dest,
            real_sni=dest,
        )
    )
    db.add(
        Event(
            kind="server_created",
            actor=auth.user.email,
            payload_json={"server_id": server.id, "roles": roles, "host": host},
        )
    )
    job = await create_job(db, "provision", server_id=server.id)
    await db.commit()
    request.app.state.job_runner.start(job.id)
    return {"server_id": server.id, "job_id": job.id, "status": server.status}


@router.get("/{server_id}")
async def get_server(
    server_id: int,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    server = await db.scalar(
        select(Server).options(selectinload(Server.secret)).where(Server.id == server_id)
    )
    if server is None:
        raise ApiError("server_not_found", "Сервер не найден", 404)
    traffic = await _traffic_map(db, [server.id])
    uptime = await _uptime_map(db, [server.id])
    payload = _server_dict(server, traffic.get(server.id, 0), uptime.get(server.id))
    payload["actions"] = sorted(ACTIONS)
    return payload


@router.post("/{server_id}/actions", status_code=202)
async def server_action(
    server_id: int,
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    server = await db.get(Server, server_id)
    if server is None:
        raise ApiError("server_not_found", "Сервер не найден", 404)
    action = str(payload.get("action", ""))
    if action not in ACTIONS:
        raise ApiError("invalid_action", "action: " + ", ".join(sorted(ACTIONS)), 400)
    job_type = {
        "reapply": "reapply",
        "restart": "restart_services",
        "reinstall": "reinstall",
        "diagnose": "diagnose",
    }[action]
    agent_delivered = action in {"reapply", "restart", "diagnose"}
    job = await create_job(db, job_type, server_id=server.id, payload={"action": action})
    db.add(
        Event(
            kind="server_action",
            actor=auth.user.email,
            payload_json={"server_id": server.id, "action": action, "job_id": job.id},
        )
    )
    await db.commit()
    if not agent_delivered:
        request.app.state.job_runner.start(job.id)
    return {"job_id": job.id, "status": job.status}


@router.get("/{server_id}/diagnostics")
async def server_diagnostics(
    server_id: int,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    server = await db.get(Server, server_id)
    if server is None:
        raise ApiError("server_not_found", "Сервер не найден", 404)
    event = await db.scalar(
        select(Event)
        .where(Event.kind == "agent_diagnose", Event.actor == f"server:{server_id}")
        .order_by(Event.id.desc())
        .limit(1)
    )
    return {
        "server_id": server_id,
        "status": server.status,
        "last_seen_at": server.last_seen_at.isoformat() if server.last_seen_at else None,
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "host_fingerprint": server.host_fingerprint,
        "report": (event.payload_json or {}).get("report") if event else None,
    }


@router.delete("/{server_id}", status_code=202)
async def delete_server(
    server_id: int,
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    server = await db.get(Server, server_id)
    if server is None:
        raise ApiError("server_not_found", "Сервер не найден", 404)
    confirm = str(payload.get("confirm_name", ""))
    force = bool(payload.get("force", False))
    if not force and confirm != server.name:
        raise ApiError("confirm_mismatch", "Введите точное имя сервера для подтверждения", 400)
    if force:
        await db.delete(server)
        db.add(
            Event(
                kind="server_force_deleted",
                actor=auth.user.email,
                payload_json={"server_id": server_id, "name": server.name},
            )
        )
        await db.commit()
        return {"deleted": True, "force": True}
    server.status = "delete_pending"
    job = await create_job(db, "uninstall", server_id=server.id)
    db.add(
        Event(
            kind="server_delete_requested",
            actor=auth.user.email,
            payload_json={"server_id": server_id, "job_id": job.id},
        )
    )
    await db.commit()
    request.app.state.job_runner.start(job.id)
    return {"job_id": job.id, "status": "delete_pending"}
