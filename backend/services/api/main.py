import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.settings import Settings, load_settings
from services.worker.runtime import Worker

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config)
        worker = Worker(database, config.worker_shutdown_seconds) if config.embed_worker else None
        app.state.database = database
        app.state.worker = worker
        try:
            if worker is not None:
                await worker.start()
            yield
        finally:
            try:
                if worker is not None:
                    await worker.stop()
            finally:
                await database.close()

    app = FastAPI(title="Ovrly backend", lifespan=lifespan)

    @app.get("/healthz")
    async def health() -> JSONResponse:
        try:
            await app.state.database.ping()
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.warning("Readiness failed: database unavailable")
            return JSONResponse({"status": "unavailable", "reason": "database"}, status_code=503)
        worker = app.state.worker
        if config.embed_worker and (worker is None or not worker.running):
            logger.warning("Readiness failed: embedded worker unavailable")
            return JSONResponse({"status": "unavailable", "reason": "worker"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app
