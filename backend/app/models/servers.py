from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UTCDateTime, utcnow


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (
        Index("ix_servers_status", "status"),
        Index("ix_servers_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), default="root")
    auth_type: Mapped[str] = mapped_column(String(16), default="password")  # password | key
    secret_enc: Mapped[str | None] = mapped_column(Text, default=None)
    host_fingerprint: Mapped[str | None] = mapped_column(String(255), default=None)
    roles: Mapped[str] = mapped_column(String(16), default="entry")  # entry | exit | both
    region: Mapped[str | None] = mapped_column(String(32), default=None)
    status: Mapped[str] = mapped_column(String(24), default="new")
    agent_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    agent_version: Mapped[str | None] = mapped_column(String(32), default=None)
    xray_version: Mapped[str | None] = mapped_column(String(32), default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    degraded_reason: Mapped[str | None] = mapped_column(String(255), default=None)
    revision_id: Mapped[int | None] = mapped_column(Integer, default=None)
    base_dir: Mapped[str] = mapped_column(String(255), default="/root/config/vpn")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    secret: Mapped[ServerSecret | None] = relationship(
        back_populates="server", cascade="all, delete-orphan", uselist=False
    )

    @property
    def is_entry(self) -> bool:
        return self.roles in {"entry", "both"}

    @property
    def is_exit(self) -> bool:
        return self.roles in {"exit", "both"}


class ServerSecret(Base):
    __tablename__ = "server_secrets"

    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    reality_private_key_enc: Mapped[str | None] = mapped_column(Text, default=None)
    reality_public_key: Mapped[str | None] = mapped_column(String(64), default=None)
    short_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    interconnect_uuid_enc: Mapped[str | None] = mapped_column(Text, default=None)
    real_dest: Mapped[str | None] = mapped_column(String(255), default=None)
    real_sni: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    server: Mapped[Server] = relationship(back_populates="secret")


class ServerConfig(Base):
    __tablename__ = "server_configs"
    __table_args__ = (Index("ix_server_configs_server_id", "server_id", "revision_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("routing_revisions.id", ondelete="SET NULL"), default=None
    )
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    result: Mapped[str | None] = mapped_column(String(24), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
