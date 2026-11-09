from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.db import get_session
from app.models.ops import MetricsBucket
from app.models.routing import Client
from app.models.servers import Server, ServerSecret
from app.services.links import (
    build_clash_profile,
    build_entry_endpoint,
    build_v2ray_subscription,
    build_vless_url,
    choose_subscription_format,
    smart_profile_bytes,
    subscription_headers,
)

router = APIRouter(tags=["subscription"])


@router.get("/sub/{token}")
async def subscription(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await db.scalar(select(Client).where(Client.sub_token == token))
    if client is None or not client.enabled:
        raise ApiError("subscription_not_found", "Подписка не найдена", 404)
    row = (
        await db.execute(
            select(Server, ServerSecret)
            .join(ServerSecret, ServerSecret.server_id == Server.id)
            .where(Server.roles.in_(["entry", "both"]))
            .order_by(Server.id)
        )
    ).first()
    if row is None:
        raise ApiError("entry_unavailable", "Нет доступного entry-узла", 409)
    server, secret = row
    endpoint = build_entry_endpoint(server, secret, client)
    upload = int(
        await db.scalar(
            select(func.coalesce(func.sum(MetricsBucket.up_bytes), 0)).where(
                MetricsBucket.client_id == client.id
            )
        )
        or 0
    )
    download = int(
        await db.scalar(
            select(func.coalesce(func.sum(MetricsBucket.down_bytes), 0)).where(
                MetricsBucket.client_id == client.id
            )
        )
        or 0
    )
    total = client.traffic_limit_bytes or 0
    headers = subscription_headers(title=client.name, upload=upload, download=download, total=total)
    headers["Content-Disposition"] = f"inline; filename=\"{client.name}\".json"
    fmt = choose_subscription_format(request.headers.get("user-agent"))
    if fmt == "sing-box":
        return Response(
            smart_profile_bytes(endpoint, title=client.name),
            media_type="application/json",
            headers=headers,
        )
    if fmt == "clash":
        return Response(
            build_clash_profile(endpoint, profile_title=client.name).encode(),
            media_type="text/yaml; charset=utf-8",
            headers=headers,
        )
    return Response(
        build_v2ray_subscription(endpoint), media_type="text/plain; charset=utf-8", headers=headers
    )


@router.get("/sub/{token}/vless")
async def subscription_vless(
    token: str,
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await db.scalar(select(Client).where(Client.sub_token == token))
    if client is None or not client.enabled:
        raise ApiError("subscription_not_found", "Подписка не найдена", 404)
    row = (
        await db.execute(
            select(Server, ServerSecret)
            .join(ServerSecret, ServerSecret.server_id == Server.id)
            .where(Server.roles.in_(["entry", "both"]))
            .order_by(Server.id)
        )
    ).first()
    if row is None:
        raise ApiError("entry_unavailable", "Нет доступного entry-узла", 409)
    server, secret = row
    endpoint = build_entry_endpoint(server, secret, client)
    return Response(build_vless_url(endpoint, label=client.name), media_type="text/plain")
