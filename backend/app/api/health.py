from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.db import get_session_factory

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    db_status = "ok"
    status_code = 200
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health check must never raise
        db_status = "error"
        status_code = 503
    payload = {
        "status": "ok" if status_code == 200 else "degraded",
        "db": db_status,
        "version": settings.version,
    }
    return JSONResponse(payload, status_code=status_code)
