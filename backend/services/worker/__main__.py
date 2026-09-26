import asyncio
import logging
import signal

from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.settings import load_settings
from services.worker.runtime import Worker


async def serve() -> None:
    settings = load_settings()
    database = Database(settings)
    worker = Worker(database, settings.worker_shutdown_seconds)
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
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve())
    except (OSError, RuntimeError, TimeoutError, SQLAlchemyError):
        logging.getLogger(__name__).error("Worker could not run; check database and configuration")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
