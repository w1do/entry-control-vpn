from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.db import get_session
from app.core.security import sha256_hex
from app.core.timeutils import ensure_utc, utcnow
from app.models.ops import Event, Incident, MetricsBucket
from app.models.routing import Client
from app.models.servers import Server, ServerConfig
from app.schemas.agent import (
    ApplyResultRequest,
    CommandResultRequest,
    DiagnoseRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    MetricsPayload,
    RegisterRequest,
    RegisterResponse,
)

router = APIRouter(prefix="/agent/v1", tags=["agent"])

_UNIT_STATUS = {"active", "inactive", "failed", "unknown"}


async def get_agent(request: Request, db: AsyncSession = Depends(get_session)) -> Server:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError("unauthorized", "Требуется токен агента", 401)
    token = header[7:].strip()
    if not token:
        raise ApiError("unauthorized", "Пустой токен агента", 401)
    server = await db.scalar(select(Server).where(Server.agent_token_hash == sha256_hex(token)))
    if server is None:
        raise ApiError("unauthorized", "Неизвестный токен агента", 401)
    return server


async def _touch(server: Server, *, status: str | None = None) -> None:
    server.last_seen_at = utcnow()
    if status is not None:
        server.status = status


@router.post("/register", response_model=RegisterResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> RegisterResponse:
    server.agent_version = payload.agent_version or server.agent_version
    server.xray_version = payload.xray_version or server.xray_version
    await _touch(server, status="online" if server.status in {"new", "provisioning", "offline"} else None)
    db.add(
        Event(
            kind="agent_registered",
            actor=f"server:{server.id}",
            payload_json={
                "agent_version": payload.agent_version,
                "xray_version": payload.xray_version,
                "hostname": payload.hostname,
                "arch": payload.arch,
            },
        )
    )
    await db.commit()
    return RegisterResponse(
        server_id=server.id,
        revision=server.revision_id,
        heartbeat_interval_s=30,
        config_poll_s=10,
        metrics_interval_s=15,
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    payload: HeartbeatRequest,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> HeartbeatResponse:
    if payload.xray.get("version"):
        server.xray_version = str(payload.xray["version"])
    if payload.degraded_reason:
        server.status = "degraded"
        server.degraded_reason = payload.degraded_reason[:255]
    elif server.status in {"new", "provisioning", "offline"}:
        server.status = "online"
        server.degraded_reason = None
    await _touch(server)
    await db.commit()
    desired_revision = server.revision_id
    apply_pending = bool(
        desired_revision is not None
        and payload.revision != desired_revision
        and payload.revision is not None
    )
    return HeartbeatResponse(revision=desired_revision, apply_pending=apply_pending, commands=[])


@router.get("/config")
async def get_config(
    revision: int | None = None,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> Response:
    stmt = (
        select(ServerConfig)
        .where(ServerConfig.server_id == server.id)
        .order_by(ServerConfig.id.desc())
        .limit(1)
    )
    row = await db.scalar(stmt)
    if row is None:
        return Response(status_code=204)
    if revision is not None and row.revision_id == revision:
        return Response(status_code=204)
    return Response(
        content=__import__("json").dumps(
            {
                "revision": row.revision_id,
                "sha256": row.sha256,
                "config": row.config_json,
            },
            ensure_ascii=False,
        ),
        media_type="application/json",
    )


@router.post("/apply-result")
async def apply_result(
    payload: ApplyResultRequest,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row: ServerConfig | None = None
    if payload.revision is not None:
        row = await db.scalar(
            select(ServerConfig)
            .where(ServerConfig.server_id == server.id, ServerConfig.revision_id == payload.revision)
            .order_by(ServerConfig.id.desc())
            .limit(1)
        )
    if row is not None:
        row.result = payload.status
        row.error = payload.error
        row.applied_at = utcnow()
    if payload.status == "applied":
        if payload.revision is not None:
            server.revision_id = payload.revision
        server.status = "online"
        server.degraded_reason = None
        db.add(
            Event(
                kind="config_applied",
                actor=f"server:{server.id}",
                payload_json={"revision": payload.revision, "sha256": payload.sha256},
            )
        )
    else:
        server.status = "degraded"
        server.degraded_reason = (payload.error or "apply failed")[:255]
        db.add(
            Incident(
                severity="critical",
                kind="apply_failed",
                subject=f"server:{server.id}",
                details_json={"revision": payload.revision, "error": payload.error},
            )
        )
    await _touch(server)
    await db.commit()
    return {"ok": True}


def _bucket_ts(ts) -> Any:
    value = ensure_utc(ts) if ts is not None else utcnow()
    assert value is not None
    return value.replace(second=(value.second // 15) * 15, microsecond=0)


@router.post("/metrics")
async def ingest_metrics(
    payload: MetricsPayload,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    bucket_ts = _bucket_ts(payload.ts)
    up = int(payload.traffic_delta.get("up", 0))
    down = int(payload.traffic_delta.get("down", 0))
    accepted = int(payload.connections.get("accepted_15s", 0))
    blocked = int(payload.connections.get("blocked_15s", 0))
    online = int(payload.connections.get("online", 0))
    rejected = int(payload.errors.get("rejected_15s", 0))
    xray_error = int(payload.errors.get("xray_error_15s", 0))

    existing_server = await db.scalar(
        select(MetricsBucket).where(
            MetricsBucket.server_id == server.id,
            MetricsBucket.client_id.is_(None),
            MetricsBucket.bucket_ts == bucket_ts,
            MetricsBucket.resolution == "15s",
        )
    )
    errors_json = {
        "blocked": blocked,
        "rejected": rejected,
        "xray_error": xray_error,
        "online": online,
        "replay": payload.replay,
    }
    if existing_server is None:
        db.add(
            MetricsBucket(
                server_id=server.id,
                client_id=None,
                bucket_ts=bucket_ts,
                resolution="15s",
                up_bytes=up,
                down_bytes=down,
                conns=accepted,
                errors_json=errors_json,
            )
        )
    else:
        existing_server.up_bytes = (existing_server.up_bytes or 0) + up
        existing_server.down_bytes = (existing_server.down_bytes or 0) + down
        existing_server.conns = accepted
        existing_server.errors_json = errors_json

    for item in payload.clients:
        client = await db.scalar(select(Client).where(Client.email_tag == item.email))
        if client is None:
            continue
        existing = await db.scalar(
            select(MetricsBucket).where(
                MetricsBucket.server_id == server.id,
                MetricsBucket.client_id == client.id,
                MetricsBucket.bucket_ts == bucket_ts,
                MetricsBucket.resolution == "15s",
            )
        )
        if existing is None:
            db.add(
                MetricsBucket(
                    server_id=server.id,
                    client_id=client.id,
                    bucket_ts=bucket_ts,
                    resolution="15s",
                    up_bytes=item.up,
                    down_bytes=item.down,
                    conns=1 if item.online else 0,
                    errors_json={"ips": item.ips},
                )
            )
        else:
            existing.up_bytes = (existing.up_bytes or 0) + item.up
            existing.down_bytes = (existing.down_bytes or 0) + item.down
            existing.conns = 1 if item.online else 0
            existing.errors_json = {"ips": item.ips}

    if payload.xray.get("version"):
        server.xray_version = str(payload.xray["version"])
    server.status = "online"
    server.degraded_reason = None
    await _touch(server)
    await db.commit()
    return {"ok": True, "accepted": True, "commands": []}


@router.post("/diagnose")
async def post_diagnose(
    payload: DiagnoseRequest,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    report = payload.report or {}
    server.xray_version = str(report.get("xray_version") or server.xray_version or "") or None
    server.agent_version = str(report.get("agent_version") or server.agent_version or "") or None
    db.add(
        Event(
            kind="agent_diagnose",
            actor=f"server:{server.id}",
            payload_json={"report": report},
        )
    )
    await _touch(server)
    await db.commit()
    return {"ok": True}


@router.post("/command-result")
async def command_result(
    payload: CommandResultRequest,
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    db.add(
        Event(
            kind="agent_command_result",
            actor=f"server:{server.id}",
            payload_json={
                "command_id": payload.command_id,
                "status": payload.status,
                "output": (payload.output or "")[:4000],
            },
        )
    )
    await _touch(server)
    await db.commit()
    return {"ok": True}


@router.post("/uninstall")
async def agent_uninstalled(
    server: Server = Depends(get_agent),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    server.status = "uninstalled"
    await _touch(server)
    db.add(Event(kind="agent_uninstalled", actor=f"server:{server.id}", payload_json={}))
    await db.commit()
    return {"ok": True}


@router.get("/artifacts/{name}")
async def get_artifact(
    name: str,
    request: Request,
    server: Server = Depends(get_agent),
) -> FileResponse:
    if "/" in name or ".." in name or not name:
        raise ApiError("invalid_artifact", "Некорректное имя артефакта", 400)
    artifacts_dir = Path(request.app.state.settings.artifacts_dir)
    path = artifacts_dir / name
    if not path.is_file():
        raise ApiError("artifact_not_found", "Артефакт не найден", 404)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return FileResponse(path, headers={"X-Checksum-Sha256": checksum})
