import asyncio
import logging
from contextlib import suppress

from sqlalchemy.exc import SQLAlchemyError

from services.database import Database

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, database: Database, shutdown_seconds: float):
        self.database = database
        self.shutdown_seconds = shutdown_seconds
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._started.is_set() and self._task is not None and not self._task.done()

    async def run(self) -> None:
        try:
            await self.database.ping()
        except (SQLAlchemyError, OSError, TimeoutError):
            raise RuntimeError("Worker startup failed: database unavailable") from None
        self._started.set()
        logger.info("Worker ready (lifecycle skeleton; no job processing)")
        await self._stop.wait()

    def _finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Worker failed; readiness is unavailable")
        elif not self._stop.is_set():
            logger.error("Worker stopped unexpectedly; readiness is unavailable")

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Worker instances can only be started once")
        self._task = asyncio.create_task(self.run(), name="ovrly-worker")
        self._task.add_done_callback(self._finished)
        ready = asyncio.create_task(self._started.wait())
        try:
            await asyncio.wait({ready, self._task}, return_when=asyncio.FIRST_COMPLETED)
            if self._task.done():
                await self._task
                raise RuntimeError("Worker stopped before startup completed")
        finally:
            ready.cancel()
            with suppress(asyncio.CancelledError):
                await ready

    def request_stop(self) -> None:
        self._stop.set()

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("Worker has not started")
        await self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self.request_stop()
        if self._task.cancelled():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=self.shutdown_seconds)
        except TimeoutError:
            logger.error("Worker shutdown timed out; cancelling the owned task")
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            raise RuntimeError("Worker shutdown timed out") from None
