from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth as auth_api
from app.api import health, stubs
from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.core.crypto import SecretBox
from app.core.db import create_all, dispose_engine, get_session_factory, init_engine
from app.core.logging import configure_logging, get_logger
from app.core.timeutils import utcnow
from app.services.auth import bootstrap_admin
from app.services.rate_limit import LoginRateLimiter

log = get_logger("app.main")


def _start_scheduler(app: FastAPI, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def mark_offline() -> None:
        from datetime import timedelta

        from app.models.servers import Server

        try:
            async with get_session_factory()() as db:
                cutoff = utcnow() - timedelta(seconds=settings.heartbeat_offline_seconds)
                await db.execute(
                    update(Server)
                    .where(
                        Server.status == "online",
                        Server.last_seen_at.is_not(None),
                        Server.last_seen_at < cutoff,
                    )
                    .values(status="offline")
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - background job must not die silently
            log.exception("heartbeat_monitor_failed")

    scheduler.add_job(mark_offline, "interval", seconds=30, id="mark_offline", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    init_engine(settings)
    if settings.auto_create_schema:
        await create_all()
    async with get_session_factory()() as db:
        created = await bootstrap_admin(db, settings)
        if created is not None:
            log.info(
                "admin_bootstrapped",
                extra={"data": {"email": created.email, "weak_password": created.must_change_password}},
            )
    scheduler: AsyncIOScheduler | None = None
    if settings.scheduler_enabled:
        try:
            scheduler = _start_scheduler(app, settings)
        except Exception:  # noqa: BLE001
            log.exception("scheduler_start_failed")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.secret_box = SecretBox(settings.encryption_key)
    app.state.login_limiter = LoginRateLimiter(
        max_attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
        block_seconds=settings.login_block_seconds,
    )

    @app.middleware("http")
    async def observability(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", extra={"request_id": request_id})
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if settings.session_cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path != "/healthz":
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "duration_ms": duration_ms,
                    "data": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                    },
                },
            )
        return response

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        payload: dict = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details:
            payload["error"]["details"] = exc.details
        return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": ".".join(str(part) for part in error.get("loc", [])), "msg": error.get("msg", "")}
            for error in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Некорректный запрос",
                    "details": {"fields": fields},
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            {"error": {"code": code, "message": str(exc.detail)}},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None) or None,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "Внутренняя ошибка сервера"}},
            status_code=500,
        )

    app.include_router(health.router)
    app.include_router(auth_api.router)
    app.include_router(stubs.router)

    static_dir = Path(settings.static_dir) if settings.static_dir else Path(__file__).parent / "static"
    index_file = static_dir / "index.html"
    if index_file.is_file():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            if full_path:
                candidate = (static_dir / full_path).resolve()
                try:
                    candidate.relative_to(static_dir.resolve())
                except ValueError:
                    return FileResponse(index_file)
                if candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(index_file)

    return app


app = create_app()
