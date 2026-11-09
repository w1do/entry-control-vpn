"""Observability: aggregation/retention, uptime, incidents, scheduler and APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import incidents as incidents_api
from app.core.db import create_all, dispose_engine, get_session_factory, init_engine
from app.main import create_app
from app.models.ops import Event, Incident, MetricsBucket
from app.models.routing import Client, Exit, SiteRule
from app.models.servers import Server
from app.services.health_monitor import run_health_monitor
from app.services.metrics_agg import (
    aggregate_buckets,
    compute_service_uptime,
    compute_uptime,
    compute_uptime_ranges,
    purge_expired,
)
from app.services.scheduler_setup import JOB_IDS, mark_offline_servers, setup_scheduler
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, make_settings

NOW = datetime(2026, 5, 20, 12, 0, 30, tzinfo=UTC)
DAY = timedelta(days=1)


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture()
async def db(tmp_path):
    settings = make_settings(tmp_path)
    init_engine(settings)
    await create_all()
    async with get_session_factory()() as session:
        yield session
    await dispose_engine()


def make_server(**overrides: Any) -> Server:
    values: dict[str, Any] = {
        "name": "node-1",
        "host": "203.0.113.10",
        "roles": "entry",
        "status": "online",
        "created_at": NOW - DAY,
        "last_seen_at": NOW,
        "base_dir": "/root/config/vpn",
    }
    values.update(overrides)
    return Server(**values)


def bucket(
    server_id: int | None,
    bucket_ts: datetime,
    *,
    resolution: str = "15s",
    up: int = 10,
    down: int = 20,
    conns: int = 1,
    errors: dict[str, Any] | None = None,
    client_id: int | None = None,
) -> MetricsBucket:
    return MetricsBucket(
        server_id=server_id,
        client_id=client_id,
        bucket_ts=bucket_ts,
        resolution=resolution,
        up_bytes=up,
        down_bytes=down,
        conns=conns,
        errors_json=errors,
    )


def floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


async def _commit_all(db, *items: Any) -> None:
    db.add_all(list(items))
    await db.commit()


async def _seed_rows(
    session, server_id: int, *, minutes: int, skip: set[int], resolution: str = "1m", now: datetime = NOW
) -> datetime:
    start = floor_minute(now) - timedelta(minutes=minutes)
    for index in range(minutes):
        if index in skip:
            continue
        session.add(bucket(server_id, start + timedelta(minutes=index), resolution=resolution))
    await session.commit()
    return start


# --------------------------------------------------------------------------- #
# aggregation / retention
# --------------------------------------------------------------------------- #


async def test_aggregate_15s_to_1m_and_idempotent(db) -> None:
    server = make_server()
    await _commit_all(db, server)
    minute = floor_minute(NOW - timedelta(minutes=1))
    for offset in (0, 15, 30, 45):
        db.add(
            bucket(
                server.id,
                minute + timedelta(seconds=offset),
                errors={"blocked": 1, "rejected": 0, "xray_error": 0, "online": 2, "replay": False},
            )
        )
    await db.commit()

    first = await aggregate_buckets(db, now=NOW)
    await db.commit()
    assert first["1m"] == 1

    rows = list(
        (await db.scalars(select(MetricsBucket).where(MetricsBucket.resolution == "1m"))).all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row.up_bytes, row.down_bytes, row.conns) == (40, 80, 4)
    assert row.errors_json == {"blocked": 4, "rejected": 0, "xray_error": 0, "online": 2}
    assert row.bucket_ts == minute

    second = await aggregate_buckets(db, now=NOW)
    await db.commit()
    rows = list(
        (await db.scalars(select(MetricsBucket).where(MetricsBucket.resolution == "1m"))).all()
    )
    assert second["1m"] == 1
    assert len(rows) == 1
    assert (rows[0].up_bytes, rows[0].down_bytes) == (40, 80)


async def test_aggregate_backfills_5m_and_1h(db) -> None:
    server = make_server()
    await _commit_all(db, server)
    minute = floor_minute(NOW - timedelta(hours=3))
    for offset in (0, 15, 30, 45):
        db.add(bucket(server.id, minute + timedelta(seconds=offset), up=100, down=50))
    await db.commit()

    written = await aggregate_buckets(db, now=NOW)
    await db.commit()
    assert written["1m"] == 1
    assert written["5m"] == 1
    assert written["1h"] == 1

    one_minute = await db.scalar(select(MetricsBucket).where(MetricsBucket.resolution == "1m"))
    five_minutes = await db.scalar(select(MetricsBucket).where(MetricsBucket.resolution == "5m"))
    one_hour = await db.scalar(select(MetricsBucket).where(MetricsBucket.resolution == "1h"))
    assert one_minute is not None and five_minutes is not None and one_hour is not None
    assert (one_minute.up_bytes, five_minutes.up_bytes, one_hour.up_bytes) == (400, 400, 400)
    assert five_minutes.bucket_ts == minute.replace(minute=minute.minute - minute.minute % 5)
    assert one_hour.bucket_ts == minute.replace(minute=0)


async def test_aggregate_late_replay_recomputes_targets(db) -> None:
    server = make_server()
    await _commit_all(db, server)
    minute = floor_minute(NOW - timedelta(minutes=2))
    db.add(bucket(server.id, minute, up=10))
    db.add(bucket(server.id, minute + timedelta(seconds=15), up=10))
    await db.commit()
    await aggregate_buckets(db, now=NOW)
    await db.commit()

    # agent replayed two buffered 15s points for the same minute
    db.add(bucket(server.id, minute + timedelta(seconds=30), up=10))
    db.add(bucket(server.id, minute + timedelta(seconds=45), up=10))
    await db.commit()
    await aggregate_buckets(db, now=NOW)
    await db.commit()

    rows = list(
        (
            await db.scalars(
                select(MetricsBucket).where(
                    MetricsBucket.resolution == "1m", MetricsBucket.bucket_ts == minute
                )
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].up_bytes == 40
    five = await db.scalar(
        select(MetricsBucket).where(
            MetricsBucket.resolution == "5m",
            MetricsBucket.bucket_ts == minute.replace(minute=minute.minute - minute.minute % 5),
        )
    )
    assert five is not None and five.up_bytes == 40


async def test_aggregate_skips_incomplete_current_bucket(db) -> None:
    server = make_server()
    await _commit_all(db, server)
    current = floor_minute(NOW)
    db.add(bucket(server.id, current + timedelta(seconds=15)))
    await db.commit()
    await aggregate_buckets(db, now=NOW)
    await db.commit()
    row = await db.scalar(select(MetricsBucket).where(MetricsBucket.resolution == "1m"))
    assert row is None


async def test_aggregate_client_buckets_keep_ips(db) -> None:
    server = make_server()
    client = Client(name="c1", uuid="uuid-1", email_tag="client-1@vpn", sub_token="tok-1")
    await _commit_all(db, server, client)
    minute = floor_minute(NOW - timedelta(minutes=1))
    db.add(
        bucket(
            server.id,
            minute,
            client_id=client.id,
            up=5,
            errors={"ips": ["198.51.100.7"]},
            conns=1,
        )
    )
    db.add(
        bucket(
            server.id,
            minute + timedelta(seconds=15),
            client_id=client.id,
            up=7,
            errors={"ips": []},
            conns=0,
        )
    )
    await db.commit()
    await aggregate_buckets(db, now=NOW)
    await db.commit()
    row = await db.scalar(
        select(MetricsBucket).where(
            MetricsBucket.resolution == "1m", MetricsBucket.client_id == client.id
        )
    )
    assert row is not None
    assert row.up_bytes == 12
    assert row.conns == 1
    assert (row.errors_json or {}).get("ips") == ["198.51.100.7"]


async def test_purge_expired_respects_retention(db) -> None:
    old = [
        bucket(None, NOW - timedelta(hours=25), resolution="15s"),
        bucket(None, NOW - timedelta(days=31), resolution="1m"),
        bucket(None, NOW - timedelta(days=366), resolution="5m"),
        bucket(None, NOW - timedelta(days=366), resolution="1h"),
    ]
    fresh = [
        bucket(None, NOW - timedelta(hours=1), resolution="15s"),
        bucket(None, NOW - timedelta(days=2), resolution="1m"),
        bucket(None, NOW - timedelta(days=2), resolution="5m"),
        bucket(None, NOW - timedelta(days=2), resolution="1h"),
    ]
    await _commit_all(db, *old, *fresh)
    deleted = await purge_expired(db, now=NOW)
    await db.commit()
    assert deleted["15s"] == 1
    assert deleted["1m"] == 1
    assert deleted["5m"] == 1
    assert deleted["1h"] == 1
    remaining = list((await db.scalars(select(MetricsBucket))).all())
    assert len(remaining) == 4
    assert all(row.bucket_ts >= NOW - timedelta(days=400) for row in remaining)


# --------------------------------------------------------------------------- #
# uptime
# --------------------------------------------------------------------------- #


async def test_uptime_with_gap(db) -> None:
    server = make_server(created_at=NOW - timedelta(hours=2))
    await _commit_all(db, server)
    await _seed_rows(db, server.id, minutes=120, skip=set(range(50, 60)))

    report = await compute_uptime(db, server, "24h", now=NOW)
    assert report["range"] == "24h"
    assert report["resolution"] == "1m"
    assert report["expected_buckets"] == 120
    assert report["present_buckets"] == 110
    assert report["uptime_pct"] == round(100 * 110 / 120, 2)
    assert report["downtime_seconds"] == 600
    assert len(report["gaps"]) == 1
    gap = report["gaps"][0]
    assert gap["duration_seconds"] == 600
    assert gap["reason"] == "no_metrics"
    assert gap["start"] < gap["end"]


async def test_uptime_is_clipped_by_server_creation(db) -> None:
    server = make_server(created_at=NOW - timedelta(minutes=60))
    await _commit_all(db, server)
    await _seed_rows(db, server.id, minutes=60, skip=set())

    report = await compute_uptime(db, server, "24h", now=NOW)
    assert report["expected_buckets"] == 60
    assert report["present_buckets"] == 60
    assert report["uptime_pct"] == 100.0
    assert report["gaps"] == []


async def test_uptime_gap_reason_offline_from_incident(db) -> None:
    server = make_server(created_at=NOW - timedelta(hours=2))
    await _commit_all(db, server)
    start = await _seed_rows(db, server.id, minutes=120, skip={60, 61, 62, 63, 64})
    gap_start = start + timedelta(minutes=60)
    gap_end = gap_start + timedelta(minutes=5)
    db.add(
        Incident(
            severity="critical",
            kind="server_offline",
            subject=f"server:{server.id}",
            status="resolved",
            created_at=gap_start,
            resolved_at=gap_end,
        )
    )
    await db.commit()

    report = await compute_uptime(db, server, "24h", now=NOW)
    assert len(report["gaps"]) == 1
    assert report["gaps"][0]["reason"] == "offline"
    assert report["gaps"][0]["duration_seconds"] == 300


async def test_uptime_gap_reason_offline_for_current_status(db) -> None:
    server = make_server(
        status="offline",
        created_at=NOW - timedelta(hours=1),
        last_seen_at=NOW - timedelta(minutes=20),
    )
    await _commit_all(db, server)
    # only the first 40 minutes have metrics; the last 20 are a current outage
    start = floor_minute(NOW) - timedelta(minutes=60)
    for index in range(40):
        db.add(bucket(server.id, start + timedelta(minutes=index), resolution="1m"))
    await db.commit()

    report = await compute_uptime(db, server, "24h", now=NOW)
    assert report["downtime_seconds"] == 20 * 60
    assert report["gaps"][-1]["reason"] == "offline"


async def test_uptime_percentages_and_service_uptime(db) -> None:
    server = make_server()
    await _commit_all(db, server)
    start = floor_minute(NOW) - timedelta(hours=2)
    for index in range(120):
        db.add(bucket(server.id, start + timedelta(minutes=index), resolution="1m"))
    await db.commit()

    percentages = await compute_uptime_ranges(db, server, now=NOW)
    assert set(percentages) == {"24h", "7d", "30d"}
    assert 0.0 <= percentages["24h"] <= 100.0
    assert 0.0 <= percentages["7d"] <= 100.0
    assert 0.0 <= percentages["30d"] <= 100.0

    service = await compute_service_uptime(db, now=NOW)
    assert service["nodes_total"] == 1
    assert service["nodes_online"] == 1
    assert service["nodes_online_pct"] == 100.0
    assert 0.0 <= service["uptime_pct"] <= 100.0
    assert service["process_uptime_seconds"] >= 0


# --------------------------------------------------------------------------- #
# health monitor
# --------------------------------------------------------------------------- #


async def test_server_offline_incident_lifecycle(db) -> None:
    server = make_server(status="offline", last_seen_at=NOW - timedelta(minutes=5))
    await _commit_all(db, server)

    summary = await run_health_monitor(db, now=NOW)
    await db.commit()
    assert summary["created"] == 1
    incidents = list(
        (
            await db.scalars(select(Incident).where(Incident.kind == "server_offline"))
        ).all()
    )
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.severity == "critical"
    assert incident.subject == f"server:{server.id}"
    assert incident.status == "open"

    again = await run_health_monitor(db, now=NOW + timedelta(seconds=30))
    await db.commit()
    assert again["created"] == 0
    assert len(
        (await db.scalars(select(Incident).where(Incident.kind == "server_offline"))).all()
    ) == 1

    server.status = "online"
    await db.commit()
    resolved = await run_health_monitor(db, now=NOW + timedelta(seconds=60))
    await db.commit()
    assert resolved["resolved"] == 1
    refreshed = await db.get(Incident, incident.id)
    assert refreshed is not None and refreshed.status == "resolved"
    assert refreshed.resolved_at is not None


async def test_exit_down_incident_lists_affected_sites(db) -> None:
    exit_spec = Exit(
        kind="node", label="sg-1", endpoint_host="203.0.113.20", health="down", enabled=True
    )
    db.add(exit_spec)
    await db.flush()
    rule = SiteRule(
        name="Anthropic",
        match_type="domain",
        values_json=["anthropic.com"],
        allowed_exit_ids_json=[exit_spec.id],
        enabled=True,
        position=0,
    )
    db.add(rule)
    await db.commit()

    summary = await run_health_monitor(db, now=NOW)
    await db.commit()
    assert summary["created"] == 1
    incident = await db.scalar(select(Incident).where(Incident.kind == "exit_down"))
    assert incident is not None
    assert incident.severity == "warning"
    assert incident.subject == f"exit:{exit_spec.id}"
    assert (incident.details_json or {})["sites"] == [{"id": rule.id, "name": "Anthropic"}]

    exit_spec.health = "up"
    await db.commit()
    resolved = await run_health_monitor(db, now=NOW + timedelta(seconds=30))
    await db.commit()
    assert resolved["resolved"] == 1


async def test_exit_down_without_allowlist_has_no_incident(db) -> None:
    exit_spec = Exit(kind="external", label="res-1", endpoint_host="proxy.example.net", health="down")
    await _commit_all(db, exit_spec)
    summary = await run_health_monitor(db, now=NOW)
    await db.commit()
    assert summary["created"] == 0
    assert await db.scalar(select(Incident)) is None


async def test_exit_down_resolved_when_rule_disabled(db) -> None:
    exit_spec = Exit(kind="node", label="sg-2", endpoint_host="203.0.113.21", health="down")
    db.add(exit_spec)
    await db.flush()
    rule = SiteRule(
        name="OpenAI",
        values_json=["openai.com"],
        allowed_exit_ids_json=[exit_spec.id],
        enabled=True,
    )
    db.add(rule)
    await db.commit()
    await run_health_monitor(db, now=NOW)
    await db.commit()
    rule.enabled = False
    await db.commit()
    summary = await run_health_monitor(db, now=NOW + timedelta(seconds=30))
    await db.commit()
    assert summary["resolved"] == 1


async def test_acknowledged_incident_is_not_duplicated(db) -> None:
    server = make_server(status="offline", last_seen_at=None)
    await _commit_all(db, server)
    await run_health_monitor(db, now=NOW)
    await db.commit()
    incident = await db.scalar(select(Incident))
    assert incident is not None
    incident.status = "ack"
    incident.acked_at = NOW
    await db.commit()
    summary = await run_health_monitor(db, now=NOW + timedelta(seconds=30))
    await db.commit()
    assert summary["created"] == 0
    assert len((await db.scalars(select(Incident))).all()) == 1


# --------------------------------------------------------------------------- #
# scheduler
# --------------------------------------------------------------------------- #


async def test_mark_offline_servers(db) -> None:
    stale = make_server(name="stale", status="online", last_seen_at=NOW - timedelta(seconds=300))
    fresh = make_server(name="fresh", status="online", last_seen_at=NOW - timedelta(seconds=10))
    await _commit_all(db, stale, fresh)
    count = await mark_offline_servers(db, offline_seconds=90, now=NOW)
    await db.commit()
    assert count == 1
    assert stale.status == "offline"
    assert fresh.status == "online"


async def test_setup_scheduler_registers_jobs(tmp_path) -> None:
    settings = make_settings(tmp_path)
    init_engine(settings)
    await create_all()
    app = create_app(settings)
    scheduler = setup_scheduler(app, settings)
    try:
        assert {job.id for job in scheduler.get_jobs()} == set(JOB_IDS)
        assert app.state.scheduler is scheduler
    finally:
        scheduler.shutdown(wait=False)
        await dispose_engine()


# --------------------------------------------------------------------------- #
# incidents API
# --------------------------------------------------------------------------- #


def _seed_sync(settings: Any, builder: Callable[[Any], Awaitable[Any]]) -> Any:
    async def run() -> Any:
        init_engine(settings)
        await create_all()
        try:
            async with get_session_factory()() as session:
                value = await builder(session)
                await session.commit()
                return value
        finally:
            await dispose_engine()

    return asyncio.run(run())


def _fetch_events(settings: Any, kind: str) -> list[tuple[str | None, Any]]:
    async def run() -> list[tuple[str | None, Any]]:
        init_engine(settings)
        await create_all()
        try:
            async with get_session_factory()() as session:
                rows = (await session.scalars(select(Event).where(Event.kind == kind))).all()
                return [(row.actor, row.payload_json) for row in rows]
        finally:
            await dispose_engine()

    return asyncio.run(run())


def _incidents_client(settings: Any) -> TestClient:
    application = create_app(settings)
    application.include_router(incidents_api.router)
    # keep the catch-all /api/v1 stub after the real incident routes
    catch_all = [
        route for route in application.routes if getattr(route, "path", "") == "/api/v1/{full_path:path}"
    ]
    for route in catch_all:
        application.routes.remove(route)
        application.routes.append(route)
    return TestClient(application)


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    return {"X-CSRF-Token": csrf}


def test_incidents_require_auth(tmp_path) -> None:
    settings = make_settings(tmp_path)
    with _incidents_client(settings) as client:
        response = client.get("/api/v1/incidents")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_incidents_list_filters(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def seed(session: Any) -> None:
        session.add_all(
            [
                Incident(severity="critical", kind="server_offline", subject="server:1", status="open"),
                Incident(severity="warning", kind="exit_down", subject="exit:2", status="ack"),
                Incident(severity="info", kind="note", subject="x", status="resolved"),
            ]
        )

    _seed_sync(settings, seed)
    with _incidents_client(settings) as client:
        headers = _login(client)
        del headers
        all_rows = client.get("/api/v1/incidents").json()
        assert len(all_rows) == 3
        assert {"id", "severity", "kind", "subject", "status", "created_at"} <= set(all_rows[0])
        assert len(client.get("/api/v1/incidents?status=open").json()) == 1
        assert len(client.get("/api/v1/incidents?severity=warning").json()) == 1
        assert len(client.get("/api/v1/incidents?limit=2").json()) == 2
        assert client.get("/api/v1/incidents?status=bogus").status_code == 400
        assert client.get("/api/v1/incidents?severity=bogus").status_code == 400


def test_incident_ack_requires_csrf_and_writes_audit(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def seed(session: Any) -> int:
        incident = Incident(
            severity="critical", kind="server_offline", subject="server:7", status="open"
        )
        session.add(incident)
        await session.flush()
        return incident.id

    incident_id = _seed_sync(settings, seed)
    with _incidents_client(settings) as client:
        _login(client)
        without_csrf = client.post(f"/api/v1/incidents/{incident_id}/ack")
        assert without_csrf.status_code == 403
        assert without_csrf.json()["error"]["code"] == "csrf_failed"

        response = client.post(
            f"/api/v1/incidents/{incident_id}/ack", headers={"X-CSRF-Token": "bad"}
        )
        assert response.status_code == 403

    with _incidents_client(settings) as client:
        headers = _login(client)
        response = client.post(f"/api/v1/incidents/{incident_id}/ack", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ack"
        assert body["acked_at"] is not None

        resolved = client.post(f"/api/v1/incidents/{incident_id}/resolve", headers=headers)
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"] is not None

        again = client.post(f"/api/v1/incidents/{incident_id}/resolve", headers=headers)
        assert again.status_code == 200
        ack_resolved = client.post(f"/api/v1/incidents/{incident_id}/ack", headers=headers)
        assert ack_resolved.status_code == 409
        missing = client.post("/api/v1/incidents/999/ack", headers=headers)
        assert missing.status_code == 404

    ack_events = _fetch_events(settings, "incident_ack")
    resolve_events = _fetch_events(settings, "incident_resolve")
    assert ack_events and ack_events[0][0] == ADMIN_EMAIL
    assert resolve_events and resolve_events[0][0] == ADMIN_EMAIL
    assert ack_events[0][1]["incident_id"] == incident_id


# --------------------------------------------------------------------------- #
# metrics API uptime
# --------------------------------------------------------------------------- #


def test_node_metrics_uptime_and_downtime(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def seed(session: Any) -> int:
        server = make_server(created_at=NOW - timedelta(hours=2))
        session.add(server)
        await session.flush()
        start = floor_minute(NOW) - timedelta(minutes=120)
        for index in range(120):
            if 50 <= index < 60:
                continue
            session.add(
                bucket(server.id, start + timedelta(minutes=index), resolution="1m", up=1, down=2)
            )
        await session.flush()
        return server.id

    server_id = _seed_sync(settings, seed)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/v1/metrics/node/{server_id}?range=24h")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["uptime"]["uptime_pct"] == round(100 * 110 / 120, 2)
        assert body["uptime"]["by_range"]["24h"] == body["uptime"]["uptime_pct"]
        assert set(body["uptime"]["by_range"]) == {"24h", "7d", "30d"}
        assert body["downtime"]["total_seconds"] == 600
        assert len(body["downtime"]["gaps"]) == 1
        assert body["downtime"]["gaps"][0]["reason"] == "no_metrics"
        assert body["total_bytes"] == 110 * 3


def test_overview_service_uptime(tmp_path) -> None:
    settings = make_settings(tmp_path)

    async def seed(session: Any) -> None:
        server = make_server()
        session.add(server)
        await session.flush()
        start = floor_minute(NOW) - timedelta(minutes=60)
        for index in range(60):
            session.add(bucket(server.id, start + timedelta(minutes=index), resolution="1m"))

    _seed_sync(settings, seed)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/metrics/overview")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["nodes"] == {"total": 1, "online": 1, "degraded": 0}
        service = body["service_uptime"]
        assert service["nodes_online_pct"] == 100.0
        assert 0.0 <= service["uptime_pct"] <= 100.0
        assert service["process_uptime_seconds"] >= 0


def test_prometheus_uptime_gauge(tmp_path) -> None:
    settings = make_settings(tmp_path, metrics_token="metrics-secret")

    async def seed(session: Any) -> None:
        session.add(make_server())

    _seed_sync(settings, seed)
    with TestClient(create_app(settings)) as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
        assert response.status_code == 200
        assert "vpn_uptime_seconds" in response.text
        assert "vpn_node_up" in response.text
