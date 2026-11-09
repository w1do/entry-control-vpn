from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.base import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _sqlite_path(url: str) -> str | None:
    if not url.startswith("sqlite+aiosqlite:///"):
        return None
    return url.replace("sqlite+aiosqlite:///", "", 1)


def _configure_sqlite(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    path = _sqlite_path(settings.database_url)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"pool_pre_ping": True}
    if settings.is_sqlite:
        kwargs["connect_args"] = {"timeout": 30}
    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.is_sqlite:
        _configure_sqlite(engine)
    _engine = engine
    _session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("database is not initialised")
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def create_all() -> None:
    if _engine is None:
        raise RuntimeError("database is not initialised")
    async with _engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    if _engine is None:
        return
    async with _engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def check_url_parsed(url: str) -> None:
    """Sanity check used by tests/startup."""
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError("DATABASE_URL must contain a scheme")
