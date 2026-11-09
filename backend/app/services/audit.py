from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import Event


def add_event(
    session: AsyncSession,
    kind: str,
    *,
    actor: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Queue a security/audit event. Caller commits."""
    event = Event(kind=kind, actor=actor, ip=ip, user_agent=user_agent, payload_json=payload or {})
    session.add(event)
    return event
