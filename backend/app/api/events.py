from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth
from app.core.db import get_session
from app.models.ops import Event

router = APIRouter(prefix="/api/v1/events", tags=["audit"])


@router.get("")
async def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    kind: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(Event).order_by(Event.id.desc()).limit(limit)
    if kind:
        stmt = stmt.where(Event.kind == kind)
    rows = (await db.scalars(stmt)).all()
    return [
        {
            "id": row.id,
            "ts": row.ts.isoformat() if row.ts else None,
            "kind": row.kind,
            "actor": row.actor,
            "ip": row.ip,
            "user_agent": row.user_agent,
            "payload": row.payload_json,
        }
        for row in rows
    ]
