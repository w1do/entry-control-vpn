from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutils import utcnow
from app.models.ops import Job, JobEvent

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


async def create_job(
    db: AsyncSession,
    job_type: str,
    *,
    server_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Job:
    job = Job(type=job_type, server_id=server_id, payload_json=payload or {}, status="queued")
    db.add(job)
    await db.flush()
    await append_event(db, job.id, step="created", message="Задача создана")
    return job


async def append_event(
    db: AsyncSession,
    job_id: int,
    *,
    step: str | None = None,
    message: str = "",
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> JobEvent:
    last_seq = await db.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    event = JobEvent(
        job_id=job_id,
        seq=(last_seq or 0) + 1,
        step=step,
        message=message,
        level=level,
        data_json=data or None,
    )
    db.add(event)
    await db.flush()
    return event


async def start_job(db: AsyncSession, job: Job, *, progress: int = 0) -> Job:
    job.status = "running"
    job.started_at = utcnow()
    job.progress = progress
    await append_event(db, job.id, step="started", message="Задача запущена")
    return job


async def finish_job(
    db: AsyncSession,
    job: Job,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    progress: int = 100,
) -> Job:
    job.status = status
    job.finished_at = utcnow()
    job.progress = progress if status == "succeeded" else job.progress
    job.result_json = result
    job.error = error
    message = "Задача завершена" if status == "succeeded" else f"Задача завершена: {status}"
    await append_event(db, job.id, step="finished", message=message, level="info" if status == "succeeded" else "error")
    return job


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "server_id": job.server_id,
        "status": job.status,
        "progress": job.progress,
        "result": job.result_json,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def event_to_dict(event: JobEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "seq": event.seq,
        "ts": event.ts.isoformat() if event.ts else None,
        "level": event.level,
        "step": event.step,
        "message": event.message,
        "data": event.data_json,
    }


async def stream_job_events(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    after_seq: int = 0,
    poll_interval: float = 0.5,
    max_seconds: int = 900,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE payloads for a job until it reaches a terminal status."""
    last_seq = after_seq
    started = asyncio.get_event_loop().time()
    while True:
        async with session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                yield {"event": "error", "data": {"code": "job_not_found", "message": "Задача не найдена"}}
                return
            events = (
                await db.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.seq > last_seq)
                    .order_by(JobEvent.seq)
                )
            ).all()
            for event in events:
                last_seq = event.seq
                yield {"id": event.seq, "event": "progress", "data": event_to_dict(event)}
            if job.status in TERMINAL_STATUSES:
                yield {"event": "done", "data": job_to_dict(job)}
                return
        if asyncio.get_event_loop().time() - started > max_seconds:
            yield {"event": "error", "data": {"code": "timeout", "message": "Поток задачи истёк"}}
            return
        await asyncio.sleep(poll_interval)
