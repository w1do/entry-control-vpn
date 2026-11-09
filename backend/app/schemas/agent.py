from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    agent_version: str = ""
    xray_version: str = ""
    hostname: str = ""
    os: str = ""
    arch: str = ""
    capabilities: list[str] = Field(default_factory=list)


class RegisterResponse(BaseModel):
    server_id: int
    revision: int | None
    heartbeat_interval_s: int = 30
    config_poll_s: int = 10
    metrics_interval_s: int = 15


class HeartbeatRequest(BaseModel):
    revision: int | None = None
    xray: dict[str, Any] = Field(default_factory=dict)
    config_sha256: str | None = None
    units: dict[str, str] = Field(default_factory=dict)
    degraded_reason: str | None = None


class HeartbeatResponse(BaseModel):
    revision: int | None
    apply_pending: bool = False
    commands: list[dict[str, Any]] = Field(default_factory=list)


class ApplyResultRequest(BaseModel):
    revision: int | None = None
    status: Literal["applied", "failed"]
    error: str | None = None
    log_tail: str | None = None
    sha256: str | None = None


class ClientTraffic(BaseModel):
    email: str
    up: int = 0
    down: int = 0
    online: bool = False
    ips: list[str] = Field(default_factory=list)


class MetricsPayload(BaseModel):
    ts: datetime | None = None
    revision: int | None = None
    replay: bool = False
    xray: dict[str, Any] = Field(default_factory=dict)
    traffic_delta: dict[str, int] = Field(default_factory=dict)
    clients: list[ClientTraffic] = Field(default_factory=list)
    connections: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, int] = Field(default_factory=dict)
    exits: list[dict[str, Any]] = Field(default_factory=list)


class DiagnoseRequest(BaseModel):
    report: dict[str, Any] = Field(default_factory=dict)


class CommandResultRequest(BaseModel):
    command_id: str
    status: str
    output: str | None = None
