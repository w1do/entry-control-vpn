from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UTCDateTime, utcnow


class Exit(Base):
    __tablename__ = "exits"
    __table_args__ = (
        Index("ix_exits_health", "health"),
        Index("ix_exits_server_id", "server_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="node")  # node | external
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), default=None
    )
    label: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    region: Mapped[str | None] = mapped_column(String(32), default=None)
    endpoint_host: Mapped[str] = mapped_column(String(255))
    endpoint_port: Mapped[int] = mapped_column(Integer, default=18443)
    protocol: Mapped[str] = mapped_column(String(32), default="vless_reality")
    secret_enc: Mapped[str | None] = mapped_column(Text, default=None)
    expected_exit_ip: Mapped[str | None] = mapped_column(String(64), default=None)
    bandwidth_budget_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    used_bytes: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(16), default="unknown")  # up | down | unknown
    last_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    last_delay_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    @property
    def is_available(self) -> bool:
        return self.enabled and self.health == "up"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email_tag: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    traffic_limit_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    sub_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SiteRule(Base):
    __tablename__ = "site_rules"
    __table_args__ = (Index("ix_site_rules_position", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    match_type: Mapped[str] = mapped_column(String(16), default="domain")
    values_json: Mapped[list] = mapped_column(JSON, default=list)
    allowed_exit_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    preset_key: Mapped[str | None] = mapped_column(String(64), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    @property
    def allowed_exit_ids(self) -> list[int]:
        return [int(x) for x in (self.allowed_exit_ids_json or [])]


class RoutingProfile(Base):
    __tablename__ = "routing_profile"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    default_exit_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    @property
    def default_exit_ids(self) -> list[int]:
        return [int(x) for x in (self.default_exit_ids_json or [])]


class RoutingRevision(Base):
    __tablename__ = "routing_revisions"
    __table_args__ = (Index("ix_routing_revisions_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    config_hashes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
