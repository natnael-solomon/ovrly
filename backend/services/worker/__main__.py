import asyncio
import logging
import signal

from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.jobs.handlers import default_handlers
from services.jobs.retries import RetryPolicy
from services.logging import configure_logging
from services.privacy import RETENTION_STAGE, Retention
from services.settings import load_settings
from services.storage import LocalFilesystemStore
from services.worker.runtime import Worker


async def serve() -> None:
    settings = load_settings()
    database = Database(settings)
    retention = Retention(database, settings, LocalFilesystemStore(settings.storage_dir))
    handlers = dict(default_handlers())
    if settings.retention_enabled:
        handlers[RETENTION_STAGE] = retention.run
    else:
        logging.getLogger(__name__).warning("Retention policy disabled; no automatic deletion")
    worker = Worker(
        database,
        settings.worker_shutdown_seconds,
        handlers=handlers,
        maintenance=retention.schedule if settings.retention_enabled else None,
        lease_seconds=settings.job_lease_seconds,
        poll_seconds=settings.job_poll_seconds,
        retry_policy=RetryPolicy.from_settings(settings),
    )
    loop = asyncio.get_running_loop()
    installed = []
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, worker.request_stop)
            installed.append(signum)
        await worker.start()
        await worker.wait()
    finally:
        try:
            await worker.stop()
        finally:
            await database.close()
            for signum in installed:
                loop.remove_signal_handler(signum)


def main() -> None:
    configure_logging()
    try:
        asyncio.run(serve())
    except (OSError, RuntimeError, TimeoutError, SQLAlchemyError):
        logging.getLogger(__name__).error("Worker could not run; check database and configuration")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
