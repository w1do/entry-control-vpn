from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth
from app.api.errors import ApiError
from app.core.db import get_session
from app.core.timeutils import utcnow
from app.models.ops import Incident, MetricsBucket
from app.models.routing import Client, RoutingProfile
from app.models.servers import Server
from app.services.metrics_agg import (
    compute_service_uptime,
    compute_uptime,
    compute_uptime_ranges,
    service_uptime_seconds,
)

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])
prometheus_router = APIRouter(tags=["prometheus"])

RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def _range(range_name: str) -> timedelta:
    if range_name not in RANGES:
        raise ApiError("invalid_range", "range: 24h, 7d или 30d", 400)
    return RANGES[range_name]


async def _sum_traffic(db: AsyncSession, *, since: datetime, server_id: int | None = None, client_id: int | None = None) -> int:
    stmt = select(
        func.coalesce(func.sum(MetricsBucket.up_bytes + MetricsBucket.down_bytes), 0)
    ).where(MetricsBucket.bucket_ts >= since)
    if server_id is not None:
        stmt = stmt.where(MetricsBucket.server_id == server_id)
    if client_id is not None:
        stmt = stmt.where(MetricsBucket.client_id == client_id)
    return int(await db.scalar(stmt) or 0)


@router.get("/overview")
async def overview(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    servers = list((await db.scalars(select(Server))).all())
    online = sum(1 for server in servers if server.status == "online")
    degraded = sum(1 for server in servers if server.status == "degraded")
    traffic_today = await _sum_traffic(db, since=today)
    latest = (
        await db.scalars(
            select(MetricsBucket)
            .where(MetricsBucket.client_id.is_(None), MetricsBucket.bucket_ts >= now - timedelta(minutes=2))
        )
    ).all()
    active_connections = sum(int(row.conns or 0) for row in latest)
    errors = (
        await db.scalars(select(MetricsBucket).where(MetricsBucket.bucket_ts >= now - timedelta(hours=1)))
    ).all()
    errors_total = 0
    for row in errors:
        payload = row.errors_json or {}
        errors_total += int(payload.get("rejected", 0)) + int(payload.get("xray_error", 0))
    open_incidents = int(
        await db.scalar(select(func.count(Incident.id)).where(Incident.status == "open")) or 0
    )
    profile = await db.get(RoutingProfile, 1)
    routing_enabled = bool(profile and profile.enabled and profile.default_exit_ids_json)
    return {
        "nodes": {"total": len(servers), "online": online, "degraded": degraded},
        "service_uptime": await compute_service_uptime(db, now),
        "traffic_today_bytes": traffic_today,
        "active_connections": active_connections,
        "errors_last_hour": errors_total,
        "open_incidents": open_incidents,
        "routing_enabled": routing_enabled,
        "generated_at": now.isoformat(),
    }


def _aggregate(rows, key: str) -> list[dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"up": 0, "down": 0})
    for row in rows:
        ts = row.bucket_ts
        label = ts.strftime("%Y-%m-%dT%H:00:00Z") if key == "hour" else ts.isoformat()
        buckets[label]["up"] += int(row.up_bytes or 0)
        buckets[label]["down"] += int(row.down_bytes or 0)
    return [
        {"ts": label, "up_bytes": value["up"], "down_bytes": value["down"]}
        for label, value in sorted(buckets.items())
    ]


@router.get("/traffic")
async def traffic(
    range_name: str = Query(default="24h", alias="range"),
    group_by: str = Query(default="server"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    since = utcnow() - _range(range_name)
    if group_by == "server":
        rows = (
            await db.scalars(
                select(MetricsBucket)
                .where(MetricsBucket.bucket_ts >= since, MetricsBucket.client_id.is_(None))
                .order_by(MetricsBucket.bucket_ts)
            )
        ).all()
        grouped: dict[int, list] = defaultdict(list)
        for row in rows:
            grouped[int(row.server_id or 0)].append(row)
        return {
            "range": range_name,
            "group_by": "server",
            "series": [
                {"server_id": server_id, "points": _aggregate(items, "hour")}
                for server_id, items in sorted(grouped.items())
            ],
        }
    if group_by == "client":
        rows = (
            await db.scalars(
                select(MetricsBucket)
                .where(MetricsBucket.bucket_ts >= since, MetricsBucket.client_id.is_not(None))
                .order_by(MetricsBucket.bucket_ts)
            )
        ).all()
        grouped_clients: dict[int, list] = defaultdict(list)
        for row in rows:
            grouped_clients[int(row.client_id or 0)].append(row)
        clients = {client.id: client.name for client in (await db.scalars(select(Client))).all()}
        return {
            "range": range_name,
            "group_by": "client",
            "series": [
                {"client_id": client_id, "name": clients.get(client_id), "points": _aggregate(items, "hour")}
                for client_id, items in sorted(grouped_clients.items())
            ],
        }
    raise ApiError("invalid_group_by", "group_by: server или client", 400)


@router.get("/node/{server_id}")
async def node_metrics(
    server_id: int,
    range_name: str = Query(default="24h", alias="range"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    server = await db.get(Server, server_id)
    if server is None:
        raise ApiError("server_not_found", "Сервер не найден", 404)
    since = utcnow() - _range(range_name)
    rows = (
        await db.scalars(
            select(MetricsBucket)
            .where(
                MetricsBucket.server_id == server_id,
                MetricsBucket.client_id.is_(None),
                MetricsBucket.bucket_ts >= since,
            )
            .order_by(MetricsBucket.bucket_ts)
        )
    ).all()
    per_client = (
        await db.scalars(
            select(MetricsBucket).where(
                MetricsBucket.server_id == server_id,
                MetricsBucket.client_id.is_not(None),
                MetricsBucket.bucket_ts >= since,
            )
        )
    ).all()
    clients: dict[int, dict] = defaultdict(lambda: {"up": 0, "down": 0})
    for row in per_client:
        clients[int(row.client_id or 0)]["up"] += int(row.up_bytes or 0)
        clients[int(row.client_id or 0)]["down"] += int(row.down_bytes or 0)
    now = utcnow()
    uptime = await compute_uptime(db, server, range_name, now=now)
    by_range = await compute_uptime_ranges(db, server, now=now)
    return {
        "server_id": server_id,
        "status": server.status,
        "uptime": {**uptime, "by_range": by_range},
        "downtime": {
            "total_seconds": uptime["downtime_seconds"],
            "gaps": uptime["gaps"],
        },
        "series": _aggregate(rows, "hour"),
        "clients": [
            {"client_id": client_id, "up_bytes": value["up"], "down_bytes": value["down"]}
            for client_id, value in sorted(clients.items())
        ],
        "total_bytes": await _sum_traffic(db, since=since, server_id=server_id),
    }


@router.get("/client/{client_id}")
async def client_metrics(
    client_id: int,
    range_name: str = Query(default="24h", alias="range"),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    client = await db.get(Client, client_id)
    if client is None:
        raise ApiError("client_not_found", "Клиент не найден", 404)
    since = utcnow() - _range(range_name)
    rows = (
        await db.scalars(
            select(MetricsBucket)
            .where(MetricsBucket.client_id == client_id, MetricsBucket.bucket_ts >= since)
            .order_by(MetricsBucket.bucket_ts)
        )
    ).all()
    return {
        "client_id": client_id,
        "name": client.name,
        "enabled": client.enabled,
        "traffic_limit_bytes": client.traffic_limit_bytes,
        "total_bytes": await _sum_traffic(db, since=since, client_id=client_id),
        "series": _aggregate(rows, "hour"),
    }


@prometheus_router.get("/metrics")
async def prometheus_metrics(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> Response:
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.metrics_token:
        raise ApiError("metrics_disabled", "METRICS_TOKEN не задан", 404)
    expected = f"Bearer {settings.metrics_token}"
    if authorization != expected:
        raise ApiError("unauthorized", "Неверный METRICS_TOKEN", 401)
    now = utcnow()
    servers = list((await db.scalars(select(Server))).all())
    lines: list[str] = []
    lines.append("# HELP vpn_node_up Node online status")
    lines.append("# TYPE vpn_node_up gauge")
    for server in servers:
        up = 1 if server.status == "online" else 0
        lines.append(f'vpn_node_up{{server="{server.name}"}} {up}')
    lines.append("# HELP vpn_xray_up Xray process status reported by heartbeat")
    lines.append("# TYPE vpn_xray_up gauge")
    for server in servers:
        up = 1 if server.status in {"online", "degraded"} else 0
        lines.append(f'vpn_xray_up{{server="{server.name}"}} {up}')
    totals = await db.execute(
        select(
            MetricsBucket.server_id,
            func.sum(MetricsBucket.up_bytes + MetricsBucket.down_bytes),
            func.sum(MetricsBucket.conns),
        )
        .where(MetricsBucket.bucket_ts >= now - timedelta(hours=24), MetricsBucket.client_id.is_(None))
        .group_by(MetricsBucket.server_id)
    )
    lines.append("# HELP vpn_traffic_bytes_total Traffic in the last 24h")
    lines.append("# TYPE vpn_traffic_bytes_total counter")
    lines.append("# HELP vpn_connections_active Accepted connections in the last 15s bucket")
    lines.append("# TYPE vpn_connections_active gauge")
    names = {server.id: server.name for server in servers}
    for server_id, total, conns in totals.all():
        name = names.get(int(server_id or 0), str(server_id))
        lines.append(f'vpn_traffic_bytes_total{{server="{name}"}} {int(total or 0)}')
        lines.append(f'vpn_connections_active{{server="{name}"}} {int(conns or 0)}')
    open_incidents = int(
        await db.scalar(select(func.count(Incident.id)).where(Incident.status == "open")) or 0
    )
    lines.append("# HELP vpn_incidents_open Open incidents")
    lines.append("# TYPE vpn_incidents_open gauge")
    lines.append(f"vpn_incidents_open {open_incidents}")
    lines.append("# HELP vpn_uptime_seconds Panel process uptime in seconds")
    lines.append("# TYPE vpn_uptime_seconds gauge")
    lines.append(f"vpn_uptime_seconds {service_uptime_seconds(now)}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
