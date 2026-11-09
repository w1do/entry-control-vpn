from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth, require_csrf
from app.api.errors import ApiError
from app.core.db import get_session
from app.models.routing import Exit, SiteRule
from app.models.servers import Server
from app.services.jobs import create_job

router = APIRouter(prefix="/api/v1/exits", tags=["exits"])

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
PROTOCOLS = {"vless_reality", "http", "socks5"}


def _exit_dict(exit: Exit, server: Server | None = None, used_by: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": exit.id,
        "kind": exit.kind,
        "server_id": exit.server_id,
        "server_name": server.name if server else None,
        "label": exit.label,
        "region": exit.region,
        "endpoint_host": exit.endpoint_host,
        "endpoint_port": exit.endpoint_port,
        "protocol": exit.protocol,
        "expected_exit_ip": exit.expected_exit_ip,
        "enabled": exit.enabled,
        "health": exit.health,
        "last_check_at": exit.last_check_at.isoformat() if exit.last_check_at else None,
        "last_delay_ms": exit.last_delay_ms,
        "bandwidth_budget_bytes": exit.bandwidth_budget_bytes,
        "used_bytes": exit.used_bytes,
        "used_by_sites": used_by or [],
    }


@router.get("")
async def list_exits(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    exits = (await db.scalars(select(Exit).order_by(Exit.id))).all()
    servers = {server.id: server for server in (await db.scalars(select(Server))).all()}
    sites = (await db.scalars(select(SiteRule))).all()
    result = []
    for exit in exits:
        used_by = [
            site.name for site in sites if exit.id in (site.allowed_exit_ids_json or [])
        ]
        result.append(_exit_dict(exit, servers.get(exit.server_id), used_by))
    return result


@router.post("", status_code=201)
async def create_exit(
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    kind = str(payload.get("kind", "node"))
    if kind not in {"node", "external"}:
        raise ApiError("invalid_kind", "kind: node или external", 400)
    label = str(payload.get("label", "")).strip().lower()
    if not _LABEL_RE.match(label):
        raise ApiError("invalid_label", "label: [a-z0-9-], до 24 символов", 400)
    if await db.scalar(select(Exit.id).where(Exit.label == label)) is not None:
        raise ApiError("label_taken", "Выход с такой меткой уже существует", 409)
    protocol = str(payload.get("protocol", "vless_reality"))
    if protocol not in PROTOCOLS:
        raise ApiError("invalid_protocol", "protocol: vless_reality, http или socks5", 400)
    server_id = payload.get("server_id")
    if kind == "node":
        server = await db.get(Server, int(server_id)) if server_id else None
        if server is None or server.roles not in {"exit", "both"}:
            raise ApiError("invalid_server", "Для node-выхода нужен сервер с ролью exit", 400)
        endpoint_host = server.host
        endpoint_port = 18443
        protocol = "vless_reality"
    else:
        endpoint_host = str(payload.get("endpoint_host", "")).strip()
        if not endpoint_host:
            raise ApiError("invalid_endpoint", "Укажите endpoint_host", 400)
        try:
            endpoint_port = int(payload.get("endpoint_port", 0))
        except (TypeError, ValueError) as exc:
            raise ApiError("invalid_endpoint", "Некорректный endpoint_port", 400) from exc
        if not (0 < endpoint_port < 65536):
            raise ApiError("invalid_endpoint", "Некорректный endpoint_port", 400)
        if protocol == "vless_reality":
            raise ApiError("invalid_protocol", "Внешний выход: http или socks5", 400)
    credentials: dict = {}
    if kind == "external":
        if payload.get("username"):
            credentials["username"] = str(payload["username"])
        if payload.get("password"):
            credentials["password"] = str(payload["password"])
    box = request.app.state.secret_box
    secret_enc = None
    if credentials:
        secret_enc = box.encrypt(json.dumps(credentials))
    exit = Exit(
        kind=kind,
        server_id=int(server_id) if server_id else None,
        label=label,
        region=(str(payload.get("region", "")).strip() or None),
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        protocol=protocol,
        secret_enc=secret_enc,
        expected_exit_ip=(str(payload.get("expected_exit_ip", "")).strip() or None),
        bandwidth_budget_bytes=payload.get("bandwidth_budget_bytes"),
        enabled=True,
        health="unknown",
    )
    db.add(exit)
    await db.commit()
    return _exit_dict(exit)


@router.patch("/{exit_id}")
async def update_exit(
    exit_id: int,
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    exit = await db.get(Exit, exit_id)
    if exit is None:
        raise ApiError("exit_not_found", "Выход не найден", 404)
    if exit.kind == "external":
        if "label" in payload:
            label = str(payload["label"]).strip().lower()
            if not _LABEL_RE.match(label):
                raise ApiError("invalid_label", "label: [a-z0-9-], до 24 символов", 400)
            duplicate = await db.scalar(
                select(Exit.id).where(Exit.label == label, Exit.id != exit_id)
            )
            if duplicate is not None:
                raise ApiError("label_taken", "Выход с такой меткой уже существует", 409)
            exit.label = label
        if "endpoint_host" in payload:
            host = str(payload["endpoint_host"]).strip()
            if not host:
                raise ApiError("invalid_endpoint", "Укажите endpoint_host", 400)
            exit.endpoint_host = host
        if "endpoint_port" in payload:
            try:
                port = int(payload["endpoint_port"])
            except (TypeError, ValueError) as exc:
                raise ApiError("invalid_endpoint", "Некорректный endpoint_port", 400) from exc
            if not (0 < port < 65536):
                raise ApiError("invalid_endpoint", "Некорректный endpoint_port", 400)
            exit.endpoint_port = port
        if "protocol" in payload:
            protocol = str(payload["protocol"])
            if protocol not in {"http", "socks5"}:
                raise ApiError("invalid_protocol", "Внешний выход: http или socks5", 400)
            exit.protocol = protocol
    if "enabled" in payload:
        exit.enabled = bool(payload["enabled"])
    if "region" in payload:
        exit.region = str(payload["region"]).strip() or None
    if "expected_exit_ip" in payload:
        exit.expected_exit_ip = str(payload["expected_exit_ip"]).strip() or None
    if "bandwidth_budget_bytes" in payload:
        exit.bandwidth_budget_bytes = payload["bandwidth_budget_bytes"]
    if "username" in payload or "password" in payload:
        box = request.app.state.secret_box
        credentials: dict = {}
        if exit.secret_enc:
            try:
                credentials = json.loads(box.decrypt(exit.secret_enc))
            except ValueError:
                credentials = {}
        if "username" in payload:
            credentials["username"] = str(payload["username"])
        if "password" in payload:
            credentials["password"] = str(payload["password"])
        exit.secret_enc = box.encrypt(json.dumps(credentials)) if credentials else None
    await db.commit()
    return _exit_dict(exit)


@router.delete("/{exit_id}", status_code=204)
async def delete_exit(
    exit_id: int,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> Response:
    exit = await db.get(Exit, exit_id)
    if exit is None:
        raise ApiError("exit_not_found", "Выход не найден", 404)
    used_by = [
        site.name
        for site in (await db.scalars(select(SiteRule))).all()
        if exit_id in (site.allowed_exit_ids_json or [])
    ]
    if used_by:
        raise ApiError(
            "exit_in_use",
            "Выход используется в правилах сайтов: " + ", ".join(used_by),
            409,
        )
    await db.delete(exit)
    await db.commit()
    return Response(status_code=204)


@router.post("/{exit_id}/check")
async def check_exit(
    exit_id: int,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    exit = await db.get(Exit, exit_id)
    if exit is None:
        raise ApiError("exit_not_found", "Выход не найден", 404)
    job = await create_job(db, "exit_health_check", server_id=exit.server_id, payload={"exit_id": exit.id})
    await db.commit()
    return {"job_id": job.id, "status": job.status}
