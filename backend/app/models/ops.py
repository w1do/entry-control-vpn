from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_server_id", "server_id"),
        Index("ix_jobs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_job_events_job_id_seq"),
        Index("ix_job_events_job_id_seq", "job_id", "seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    level: Mapped[str] = mapped_column(String(16), default="info")
    step: Mapped[str | None] = mapped_column(String(64), default=None)
    message: Mapped[str] = mapped_column(Text, default="")
    data_json: Mapped[dict | None] = mapped_column(JSON, default=None)


class MetricsBucket(Base):
    __tablename__ = "metrics_buckets"
    __table_args__ = (
        Index("ix_metrics_buckets_res_bucket", "resolution", "bucket_ts"),
        Index("ix_metrics_buckets_server_bucket", "server_id", "resolution", "bucket_ts"),
        Index("ix_metrics_buckets_client_bucket", "client_id", "resolution", "bucket_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), default=None
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), default=None
    )
    bucket_ts: Mapped[datetime] = mapped_column(UTCDateTime())
    resolution: Mapped[str] = mapped_column(String(8), default="15s")
    up_bytes: Mapped[int] = mapped_column(Integer, default=0)
    down_bytes: Mapped[int] = mapped_column(Integer, default=0)
    conns: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[dict | None] = mapped_column(JSON, default=None)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    kind: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    details_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_ts", "ts"), Index("ix_events_kind", "kind"))

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    kind: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str | None] = mapped_column(String(320), default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(512), default=None)
    payload_json: Mapped[dict | None] = mapped_column(JSON, default=None)
