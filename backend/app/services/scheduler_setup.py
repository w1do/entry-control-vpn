"""APScheduler wiring for the panel background jobs.

main.py calls setup_scheduler(app, settings) once during startup (inside the
lifespan, when settings.scheduler_enabled) and shuts the returned scheduler down
on exit.  Jobs:

* mark_offline (every 30 s) - online server without heartbeat -> offline;
* metrics_aggregate (every 60 s) - 15s -> 1m -> 5m -> 1h rollup;
* metrics_purge (hourly) - retention cleanup;
* health_monitor (every 30 s) - server/exit incidents, see health_monitor.py.

Each job opens its own session from app.core.db.get_session_factory and commits;
failures are logged and never kill the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.timeutils import ensure_utc, utcnow
from app.models.servers import Server
from app.services.health_monitor import run_health_monitor
from app.services.metrics_agg import aggregate_buckets, purge_expired

log = get_logger("app.scheduler")

MARK_OFFLINE_SECONDS = 30
AGGREGATE_SECONDS = 60
PURGE_SECONDS = 3600
HEALTH_MONITOR_SECONDS = 30

JOB_IDS = (
    "mark_offline",
    "metrics_aggregate",
    "metrics_purge",
    "health_monitor",
)


async def mark_offline_servers(
    session: AsyncSession,
    *,
    offline_seconds: int,
    now: datetime | None = None,
) -> int:
    """Mark online servers without a fresh heartbeat as offline; returns row count."""
    moment = ensure_utc(now) or utcnow()
    cutoff = moment - timedelta(seconds=offline_seconds)
    result = await session.execute(
        update(Server)
        .where(
            Server.status == "online",
            Server.last_seen_at.is_not(None),
            Server.last_seen_at < cutoff,
        )
        .values(status="offline")
    )
    return int(result.rowcount or 0)


def _log_job_failure(job: str) -> None:
    log.exception("scheduler_job_failed", extra={"data": {"job": job}})


def setup_scheduler(app: FastAPI, settings: Settings) -> AsyncIOScheduler:
    """Create and start the panel scheduler; return it for shutdown in lifespan."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def job_mark_offline() -> None:
        try:
            async with get_session_factory()() as session:
                count = await mark_offline_servers(
                    session, offline_seconds=settings.heartbeat_offline_seconds
                )
                await session.commit()
            if count:
                log.info("servers_marked_offline", extra={"data": {"count": count}})
        except Exception:  # noqa: BLE001 - background job must never crash the loop
            _log_job_failure("mark_offline")

    async def job_metrics_aggregate() -> None:
        try:
            async with get_session_factory()() as session:
                written = await aggregate_buckets(session)
                await session.commit()
            if written.get("total"):
                log.info("metrics_aggregated", extra={"data": written})
        except Exception:  # noqa: BLE001
            _log_job_failure("metrics_aggregate")

    async def job_metrics_purge() -> None:
        try:
            async with get_session_factory()() as session:
                deleted = await purge_expired(session)
                await session.commit()
            if deleted.get("total"):
                log.info("metrics_purged", extra={"data": deleted})
        except Exception:  # noqa: BLE001
            _log_job_failure("metrics_purge")

    async def job_health_monitor() -> None:
        try:
            async with get_session_factory()() as session:
                summary = await run_health_monitor(session)
                await session.commit()
            if summary.get("created") or summary.get("resolved"):
                log.info("health_monitor", extra={"data": summary})
        except Exception:  # noqa: BLE001
            _log_job_failure("health_monitor")

    scheduler.add_job(
        job_mark_offline,
        "interval",
        seconds=MARK_OFFLINE_SECONDS,
        id="mark_offline",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_metrics_aggregate,
        "interval",
        seconds=AGGREGATE_SECONDS,
        id="metrics_aggregate",
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),
    )
    scheduler.add_job(
        job_metrics_purge,
        "interval",
        seconds=PURGE_SECONDS,
        id="metrics_purge",
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow() + timedelta(seconds=5),
    )
    scheduler.add_job(
        job_health_monitor,
        "interval",
        seconds=HEALTH_MONITOR_SECONDS,
        id="health_monitor",
        max_instances=1,
        coalesce=True,
        next_run_time=utcnow(),
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("scheduler_started", extra={"data": {"jobs": list(JOB_IDS)}})
    return scheduler
