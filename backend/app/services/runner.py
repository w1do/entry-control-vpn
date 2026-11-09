from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.logging import get_logger
from app.services.provisioning import execute_job

log = get_logger("app.runner")


class JobRunner:
    """Runs provisioning/apply jobs in-process (single worker deployment)."""

    def __init__(self, settings: Settings, secret_box: SecretBox, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._settings = settings
        self._secret_box = secret_box
        self._factory = session_factory
        self._tasks: set[asyncio.Task] = set()

    def start(self, job_id: int) -> None:
        task = asyncio.create_task(self._run(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: int) -> None:
        try:
            await execute_job(job_id, self._settings, self._secret_box, self._factory)
        except Exception:  # noqa: BLE001
            log.exception("job_runner_failed", extra={"job_id": job_id})

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
