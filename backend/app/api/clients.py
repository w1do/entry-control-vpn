from __future__ import annotations

import io
from typing import Literal

import segno
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth, require_csrf
from app.api.errors import ApiError
from app.core.db import get_session
from app.core.security import new_token, new_uuid
from app.models.ops import MetricsBucket
from app.models.routing import Client
from app.models.servers import Server, ServerSecret
from app.services.links import build_entry_endpoint, build_smart_profile, build_vless_url

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


async def _entry_server(db: AsyncSession, server_id: int | None) -> tuple[Server, ServerSecret]:
    stmt = select(Server, ServerSecret).join(ServerSecret, ServerSecret.server_id == Server.id)
    if server_id is not None:
        stmt = stmt.where(Server.id == server_id)
    stmt = stmt.where(Server.roles.in_(["entry", "both"])).order_by(Server.id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise ApiError("entry_unavailable", "Нет entry-узла с настроенными ключами REALITY", 409)
    server, secret = row
    return server, secret


async def _client_or_404(db: AsyncSession, client_id: int) -> Client:
    client = await db.get(Client, client_id)
    if client is None:
        raise ApiError("client_not_found", "Клиент не найден", 404)
    return client


def _client_dict(client: Client) -> dict:
    return {
        "id": client.id,
        "name": client.name,
        "uuid": client.uuid,
        "email_tag": client.email_tag,
        "enabled": client.enabled,
        "traffic_limit_bytes": client.traffic_limit_bytes,
        "created_at": client.created_at.isoformat() if client.created_at else None,
    }


@router.get("")
async def list_clients(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.scalars(select(Client).order_by(Client.id))).all()
    totals: dict[int, int] = {}
    traffic = await db.execute(
        select(MetricsBucket.client_id, func.sum(MetricsBucket.up_bytes + MetricsBucket.down_bytes))
        .where(MetricsBucket.client_id.is_not(None))
        .group_by(MetricsBucket.client_id)
    )
    for client_id, total in traffic.all():
        totals[int(client_id)] = int(total or 0)
    result = []
    for client in rows:
        item = _client_dict(client)
        item["traffic_bytes"] = totals.get(client.id, 0)
        result.append(item)
    return result


@router.post("", status_code=201)
async def create_client(
    payload: dict,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ApiError("invalid_name", "Имя клиента обязательно", 400)
    existing = await db.scalar(select(Client).where(Client.name == name))
    if existing is not None:
        raise ApiError("name_taken", "Клиент с таким именем уже существует", 409)
    limit = payload.get("traffic_limit_bytes")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ApiError("invalid_limit", "Лимит трафика должен быть числом", 400) from exc
        if limit <= 0:
            raise ApiError("invalid_limit", "Лимит трафика должен быть положительным", 400)
    client = Client(
        name=name,
        uuid=new_uuid(),
        email_tag=f"pending-{new_token(6)}",
        enabled=True,
        traffic_limit_bytes=limit,
        sub_token=new_token(24),
    )
    db.add(client)
    await db.flush()
    client.email_tag = f"client-{client.id}@vpn"
    await db.commit()
    return _client_dict(client)


@router.patch("/{client_id}")
async def update_client(
    client_id: int,
    payload: dict,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    client = await _client_or_404(db, client_id)
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not name:
            raise ApiError("invalid_name", "Имя клиента обязательно", 400)
        duplicate = await db.scalar(select(Client).where(Client.name == name, Client.id != client_id))
        if duplicate is not None:
            raise ApiError("name_taken", "Клиент с таким именем уже существует", 409)
        client.name = name
    if "enabled" in payload:
        client.enabled = bool(payload["enabled"])
    if "traffic_limit_bytes" in payload:
        raw = payload["traffic_limit_bytes"]
        if raw is None:
            client.traffic_limit_bytes = None
        else:
            client.traffic_limit_bytes = int(raw)
    await db.commit()
    return _client_dict(client)


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: int,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await _client_or_404(db, client_id)
    await db.delete(client)
    await db.commit()
    return Response(status_code=204)


@router.get("/{client_id}/link")
async def client_link(
    client_id: int,
    server_id: int | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    client = await _client_or_404(db, client_id)
    server, secret = await _entry_server(db, server_id)
    endpoint = build_entry_endpoint(server, secret, client)
    return {
        "url": build_vless_url(endpoint, label=client.name),
        "server_id": server.id,
        "server_name": server.name,
        "mode": "vless",
    }


@router.get("/{client_id}/qr.svg")
async def client_qr(
    client_id: int,
    request: Request,
    server_id: int | None = Query(default=None),
    mode: Literal["smart", "vless"] = Query(default="vless"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await _client_or_404(db, client_id)
    server, secret = await _entry_server(db, server_id)
    endpoint = build_entry_endpoint(server, secret, client)
    if mode == "smart":
        base = request.app.state.settings.public_base_url.rstrip("/")
        value = f"{base}/sub/{client.sub_token}"
    else:
        value = build_vless_url(endpoint, label=client.name)
    qr = segno.make(value, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff")
    return Response(content=buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/{client_id}/profile")
async def client_profile(
    client_id: int,
    mode: Literal["smart", "vless"] = Query(default="smart"),
    server_id: int | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await _client_or_404(db, client_id)
    server, secret = await _entry_server(db, server_id)
    endpoint = build_entry_endpoint(server, secret, client)
    if mode == "vless":
        return Response(
            content=build_vless_url(endpoint, label=client.name),
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )
    payload = build_smart_profile(endpoint, profile_title=client.name)
    return Response(
        content=__import__("json").dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{client_id}/stats")
async def client_stats(
    client_id: int,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    client = await _client_or_404(db, client_id)
    from datetime import timedelta

    from app.core.timeutils import utcnow

    now = utcnow()
    result: dict[str, int] = {}
    for label, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30))):
        total = await db.scalar(
            select(func.sum(MetricsBucket.up_bytes + MetricsBucket.down_bytes)).where(
                MetricsBucket.client_id == client.id,
                MetricsBucket.bucket_ts >= now - delta,
            )
        )
        result[label] = int(total or 0)
    return {"client_id": client.id, "traffic": result, "traffic_limit_bytes": client.traffic_limit_bytes}
