"""Metrics aggregation, retention and uptime.

Background responsibilities of the scheduler (see scheduler_setup.py):

* roll 15s buckets up to 1m (raw kept 24 h), 1m -> 5m (30 days), 5m -> 1h
  (365 days);
* delete data that exceeded its retention window;
* compute node uptime and downtime gaps from the presence of heartbeat buckets.

Everything is idempotent: a target bucket is always recomputed from its source
buckets and overwritten, never added to.  A 24 h overlap window keeps recent
target buckets correct when a node replays buffered metrics (agent buffer is up
to 24 h) or when the scheduler was briefly down; on the first run (or after a
longer outage) the aggregate backfills from the newest target bucket, clamped by
the source retention window, so nothing outside retention is ever lost.

The implementation is deliberately simple (plain GROUP BY in Python over rows
loaded for the window) to stay SQLite-friendly: no window functions, no
dialect-specific UPSERT, no unique constraints on the existing metrics table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import ensure_utc, utcnow
from app.models.ops import Incident, MetricsBucket
from app.models.servers import Server

#: Panel process start (used for the service availability card / Prometheus).
SERVICE_STARTED_AT = utcnow()

RESOLUTIONS = ("15s", "1m", "5m", "1h")
SECONDS: dict[str, int] = {"15s": 15, "1m": 60, "5m": 300, "1h": 3600}

RAW_RETENTION = timedelta(hours=24)
MINUTE_RETENTION = timedelta(days=30)
FIVE_MINUTE_RETENTION = timedelta(days=365)
HOUR_RETENTION = timedelta(days=365)

#: Recent target buckets are always recomputed to absorb late replayed deltas.
OVERLAP_WINDOW = timedelta(hours=24)

#: Uptime range -> the resolution used for heartbeat windows.
RANGE_SPECS: dict[str, dict[str, Any]] = {
    "24h": {"window": timedelta(hours=24), "resolution": "1m", "fallbacks": ("15s",)},
    "7d": {"window": timedelta(days=7), "resolution": "5m", "fallbacks": ("1m",)},
    "30d": {"window": timedelta(days=30), "resolution": "5m", "fallbacks": ()},
}

_COUNTER_KEYS = {"blocked", "rejected", "xray_error"}
_GAUGE_KEYS = {"online"}


def _floor(value: datetime, seconds: int) -> datetime:
    ts = int(ensure_utc(value).timestamp())
    return datetime.fromtimestamp(ts - ts % seconds, UTC)


def _retention_start(now: datetime, source_retention: timedelta, target_seconds: int) -> datetime:
    """First target bucket fully covered by the retained source rows."""
    return _floor(now - source_retention, target_seconds) + timedelta(seconds=target_seconds)


@dataclass
class _Accumulator:
    up_bytes: int = 0
    down_bytes: int = 0
    conns: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, int] = field(default_factory=dict)
    ips: list[str] | None = None

    def add(self, row: MetricsBucket) -> None:
        self.up_bytes += int(row.up_bytes or 0)
        self.down_bytes += int(row.down_bytes or 0)
        self.conns += int(row.conns or 0)
        payload = row.errors_json or {}
        if not isinstance(payload, dict):
            return
        for key, value in payload.items():
            if key == "replay":
                continue
            if key == "ips":
                if value:
                    self.ips = [str(item)[:64] for item in list(value)[:64]]
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if key in _GAUGE_KEYS:
                self.gauges[key] = max(self.gauges.get(key, 0), int(value))
            else:
                self.counters[key] = self.counters.get(key, 0) + int(value)

    def errors_json(self) -> dict[str, Any] | None:
        payload = dict(self.counters)
        payload.update(self.gauges)
        if self.ips:
            payload["ips"] = self.ips
        return payload or None


async def _last_bucket_ts(session: AsyncSession, resolution: str) -> datetime | None:
    value = await session.scalar(
        select(func.max(MetricsBucket.bucket_ts)).where(MetricsBucket.resolution == resolution)
    )
    return ensure_utc(value) if value is not None else None


async def _incremental_start(
    session: AsyncSession,
    *,
    target_resolution: str,
    now: datetime,
    source_retention: timedelta,
    target_seconds: int,
    overlap: timedelta,
) -> datetime:
    """Start of the target range to recompute: newest bucket or overlap window."""
    retention_start = _retention_start(now, source_retention, target_seconds)
    cursor = await _last_bucket_ts(session, target_resolution)
    if cursor is None:
        return retention_start
    return max(retention_start, min(now - overlap, cursor))


async def _aggregate_transition(
    session: AsyncSession,
    source_resolution: str,
    target_resolution: str,
    *,
    start: datetime,
    end: datetime,
    now: datetime,
) -> int:
    """Recompute target buckets from source buckets in [start, end); return rows written."""
    target_seconds = SECONDS[target_resolution]
    if start >= end:
        return 0
    sources = (
        await session.scalars(
            select(MetricsBucket)
            .where(
                MetricsBucket.resolution == source_resolution,
                MetricsBucket.bucket_ts >= start,
                MetricsBucket.bucket_ts < end,
            )
            .order_by(MetricsBucket.bucket_ts)
        )
    ).all()
    grouped: dict[tuple[int | None, int | None, datetime], _Accumulator] = {}
    for row in sources:
        target_ts = _floor(row.bucket_ts, target_seconds)
        if target_ts + timedelta(seconds=target_seconds) > now:
            continue  # bucket is still filling up
        key = (row.server_id, row.client_id, target_ts)
        accumulator = grouped.get(key)
        if accumulator is None:
            accumulator = _Accumulator()
            grouped[key] = accumulator
        accumulator.add(row)
    if not grouped:
        return 0

    targets = (
        await session.scalars(
            select(MetricsBucket).where(
                MetricsBucket.resolution == target_resolution,
                MetricsBucket.bucket_ts >= _floor(start, target_seconds),
                MetricsBucket.bucket_ts < end,
            )
        )
    ).all()
    existing = {(row.server_id, row.client_id, row.bucket_ts): row for row in targets}

    written = 0
    for (server_id, client_id, target_ts), accumulator in grouped.items():
        row = existing.get((server_id, client_id, target_ts))
        errors = accumulator.errors_json()
        if row is None:
            session.add(
                MetricsBucket(
                    server_id=server_id,
                    client_id=client_id,
                    bucket_ts=target_ts,
                    resolution=target_resolution,
                    up_bytes=accumulator.up_bytes,
                    down_bytes=accumulator.down_bytes,
                    conns=accumulator.conns,
                    errors_json=errors,
                )
            )
        else:
            row.up_bytes = accumulator.up_bytes
            row.down_bytes = accumulator.down_bytes
            row.conns = accumulator.conns
            row.errors_json = errors
        written += 1
    return written


async def aggregate_buckets(
    session: AsyncSession, now: datetime | None = None
) -> dict[str, int]:
    """Run the 15s -> 1m -> 5m -> 1h rollup; returns written rows per resolution.

    The caller commits.  Repeated calls over the same source data produce the
    same target rows (idempotent overwrite), so running the job twice or
    concurrently safe-guarded by max_instances=1 never double-counts traffic.
    """
    moment = ensure_utc(now) or utcnow()
    written: dict[str, int] = {}

    start_1m = _retention_start(moment, RAW_RETENTION, SECONDS["1m"])
    written["1m"] = await _aggregate_transition(
        session, "15s", "1m", start=start_1m, end=moment, now=moment
    )

    start_5m = await _incremental_start(
        session,
        target_resolution="5m",
        now=moment,
        source_retention=MINUTE_RETENTION,
        target_seconds=SECONDS["5m"],
        overlap=OVERLAP_WINDOW,
    )
    written["5m"] = await _aggregate_transition(
        session, "1m", "5m", start=start_5m, end=moment, now=moment
    )

    start_1h = await _incremental_start(
        session,
        target_resolution="1h",
        now=moment,
        source_retention=FIVE_MINUTE_RETENTION,
        target_seconds=SECONDS["1h"],
        overlap=OVERLAP_WINDOW,
    )
    written["1h"] = await _aggregate_transition(
        session, "5m", "1h", start=start_1h, end=moment, now=moment
    )

    await session.flush()
    written["total"] = sum(value for key, value in written.items())
    return written


async def purge_expired(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Delete buckets beyond their retention window; returns deleted rows per resolution."""
    moment = ensure_utc(now) or utcnow()
    plan = (
        ("15s", moment - RAW_RETENTION),
        ("1m", moment - MINUTE_RETENTION),
        ("5m", moment - FIVE_MINUTE_RETENTION),
        ("1h", moment - HOUR_RETENTION),
    )
    deleted: dict[str, int] = {}
    for resolution, cutoff in plan:
        result = await session.execute(
            delete(MetricsBucket).where(
                MetricsBucket.resolution == resolution,
                MetricsBucket.bucket_ts < cutoff,
            )
        )
        deleted[resolution] = int(result.rowcount or 0)
    deleted["total"] = sum(deleted.values())
    await session.flush()
    return deleted


def range_spec(range_name: str) -> dict[str, Any]:
    spec = RANGE_SPECS.get(range_name)
    if spec is None:
        raise ValueError(f"unsupported range {range_name!r}; expected {', '.join(RANGE_SPECS)}")
    return spec


def _group_gaps(missing: list[datetime], step_seconds: int) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    step = timedelta(seconds=step_seconds)
    for ts in missing:
        if gaps and gaps[-1]["end_dt"] == ts:
            gaps[-1]["end_dt"] = ts + step
        else:
            gaps.append({"start_dt": ts, "end_dt": ts + step})
    return gaps


async def _server_offline_incidents(session: AsyncSession, server_id: int) -> list[Incident]:
    rows = await session.scalars(
        select(Incident)
        .where(
            Incident.kind == "server_offline",
            Incident.subject == f"server:{server_id}",
        )
        .order_by(Incident.created_at.desc())
        .limit(100)
    )
    return list(rows.all())


def _gap_reason(
    *,
    gap_start: datetime,
    gap_end: datetime,
    server: Server,
    offline_incidents: list[Incident],
    now: datetime,
) -> str:
    for incident in offline_incidents:
        incident_start = ensure_utc(incident.created_at)
        incident_end = ensure_utc(incident.resolved_at) or now
        if incident_start is None:
            continue
        if incident_start < gap_end and incident_end > gap_start:
            return "offline"
    last_seen = ensure_utc(server.last_seen_at)
    if server.status == "offline" and (last_seen is None or gap_end > last_seen):
        return "offline"
    return "no_metrics"


def service_uptime_seconds(now: datetime | None = None) -> int:
    """Panel process uptime in seconds (Prometheus endpoint)."""
    moment = ensure_utc(now) or utcnow()
    started = ensure_utc(SERVICE_STARTED_AT) or moment
    return int(max(0.0, (moment - started).total_seconds()))


async def compute_uptime(
    session: AsyncSession,
    server: Server,
    range_name: str = "24h",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Uptime % and downtime gaps of one node for a range.

    Uptime is measured by heartbeat windows: every expected bucket start of the
    range resolution must have a metrics bucket (1m for 24h, 5m for 7d/30d; the
    finer fallback covers the not-yet-aggregated tail).  The range is clipped by
    the server creation time, so a fresh node is not reported as long downtime.
    """
    moment = ensure_utc(now) or utcnow()
    spec = range_spec(range_name)
    resolution = str(spec["resolution"])
    step_seconds = SECONDS[resolution]
    step = timedelta(seconds=step_seconds)
    created = ensure_utc(server.created_at)
    start = max(moment - spec["window"], created) if created else moment - spec["window"]
    start = _floor(start, step_seconds)
    end = _floor(moment, step_seconds)
    if start >= end:
        return {
            "range": range_name,
            "resolution": resolution,
            "uptime_pct": 100.0,
            "expected_buckets": 0,
            "present_buckets": 0,
            "downtime_seconds": 0,
            "gaps": [],
        }
    expected = [
        start + step * index for index in range(int((end - start).total_seconds() // step_seconds))
    ]
    fallback_resolutions = tuple(spec["fallbacks"])
    rows = (
        await session.scalars(
            select(MetricsBucket).where(
                MetricsBucket.server_id == server.id,
                MetricsBucket.client_id.is_(None),
                MetricsBucket.resolution.in_((resolution, *fallback_resolutions)),
                MetricsBucket.bucket_ts >= start,
                MetricsBucket.bucket_ts < end + step,
            )
        )
    ).all()
    present = {_floor(row.bucket_ts, step_seconds) for row in rows}
    missing = [ts for ts in expected if ts not in present]
    gaps = _group_gaps(missing, step_seconds)
    offline_incidents = await _server_offline_incidents(session, server.id)
    serialized_gaps: list[dict[str, Any]] = []
    for gap in gaps:
        gap_start = gap["start_dt"]
        gap_end = gap["end_dt"]
        serialized_gaps.append(
            {
                "start": gap_start.isoformat(),
                "end": gap_end.isoformat(),
                "duration_seconds": int((gap_end - gap_start).total_seconds()),
                "reason": _gap_reason(
                    gap_start=gap_start,
                    gap_end=gap_end,
                    server=server,
                    offline_incidents=offline_incidents,
                    now=moment,
                ),
            }
        )
    expected_count = len(expected)
    present_count = expected_count - len(missing)
    uptime_pct = round(100.0 * present_count / expected_count, 2) if expected_count else 100.0
    return {
        "range": range_name,
        "resolution": resolution,
        "uptime_pct": uptime_pct,
        "expected_buckets": expected_count,
        "present_buckets": present_count,
        "downtime_seconds": sum(gap["duration_seconds"] for gap in serialized_gaps),
        "gaps": serialized_gaps,
    }


async def compute_uptime_ranges(
    session: AsyncSession, server: Server, now: datetime | None = None
) -> dict[str, float]:
    """Uptime percentages for all supported ranges (24h / 7d / 30d)."""
    moment = ensure_utc(now) or utcnow()
    result: dict[str, float] = {}
    for range_name in RANGE_SPECS:
        report = await compute_uptime(session, server, range_name, now=moment)
        result[range_name] = report["uptime_pct"]
    return result


async def compute_service_uptime(
    session: AsyncSession, now: datetime | None = None
) -> dict[str, Any]:
    """Service availability card: API process uptime + fleet metrics coverage."""
    moment = ensure_utc(now) or utcnow()
    servers = list((await session.scalars(select(Server))).all())
    total = len(servers)
    online = sum(1 for server in servers if server.status in {"online", "degraded"})
    started_at = ensure_utc(SERVICE_STARTED_AT) or moment
    step_seconds = SECONDS["1m"]
    start = _floor(moment - timedelta(hours=24), step_seconds)
    end = _floor(moment, step_seconds)
    expected_per_server = max(0, int((end - start).total_seconds() // step_seconds))
    expected = total * expected_per_server
    coverage_pct = 100.0
    if expected:
        rows = (
            await session.execute(
                select(MetricsBucket.server_id, MetricsBucket.bucket_ts).where(
                    MetricsBucket.client_id.is_(None),
                    MetricsBucket.resolution.in_(("1m", "15s")),
                    MetricsBucket.bucket_ts >= start,
                    MetricsBucket.bucket_ts < end + timedelta(seconds=step_seconds),
                )
            )
        ).all()
        present = {
            (int(server_id or 0), _floor(bucket_ts, step_seconds)) for server_id, bucket_ts in rows
        }
        coverage_pct = round(100.0 * len(present) / expected, 2)
    return {
        "process_started_at": started_at.isoformat(),
        "process_uptime_seconds": int(max(0.0, (moment - started_at).total_seconds())),
        "nodes_total": total,
        "nodes_online": online,
        "nodes_online_pct": round(100.0 * online / total, 2) if total else 100.0,
        "metrics_coverage_24h_pct": coverage_pct,
        "uptime_pct": coverage_pct,
    }
