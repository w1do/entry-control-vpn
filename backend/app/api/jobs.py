from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import AuthContext, get_auth
from app.api.errors import ApiError
from app.core.db import get_session, get_session_factory
from app.models.ops import Job, JobEvent
from app.services.jobs import event_to_dict, job_to_dict, stream_job_events

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    server_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    if server_id is not None:
        stmt = stmt.where(Job.server_id == server_id)
    rows = (await db.scalars(stmt)).all()
    return [job_to_dict(job) for job in rows]


@router.get("/{job_id}")
async def get_job(
    job_id: int,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    job = await db.get(Job, job_id)
    if job is None:
        raise ApiError("job_not_found", "Задача не найдена", 404)
    payload = job_to_dict(job)
    events = (
        await db.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)
        )
    ).all()
    payload["events"] = [event_to_dict(event) for event in events]
    return payload


@router.get("/{job_id}/stream")
async def stream_job(
    job_id: int,
    request: Request,
    after: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_auth),
) -> EventSourceResponse:
    factory = get_session_factory()
    return EventSourceResponse(
        stream_job_events(factory, job_id, after_seq=after),
        ping=15,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
