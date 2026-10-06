import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from services.api import routes
from services.api.auth.google import IdTokenVerifier
from services.api.intake import InvestigationDispatcher, QueueDispatcher
from services.database import Database
from services.jobs.faults import FaultInjector
from services.jobs.handlers import JobHandler, default_handlers
from services.jobs.queue import JobQueue
from services.jobs.retries import RetryPolicy
from services.logging import configure_logging
from services.pipeline.registry import worker_handlers
from services.privacy import RETENTION_STAGE, Retention
from services.settings import Settings, load_settings
from services.worker.runtime import Worker

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    handlers: Mapping[str, JobHandler] | None = None,
    faults: FaultInjector | None = None,
    dispatcher: InvestigationDispatcher | None = None,
    id_token_verifier: IdTokenVerifier | None = None,
) -> FastAPI:
    configure_logging()
    config = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config)
        retention = Retention(database, config, app.state.upload_store)
        active_handlers = worker_handlers(
            database,
            config,
            handlers if handlers is not None else default_handlers(app.state.upload_store),
            evidence=handlers is None,
        )
        if config.retention_enabled:
            active_handlers[RETENTION_STAGE] = retention.run
        else:
            logger.warning("Retention policy disabled; no automatic deletion")
        worker = (
            Worker(
                database,
                config.worker_shutdown_seconds,
                handlers=active_handlers,
                maintenance=retention.schedule if config.retention_enabled else None,
                faults=faults,
                lease_seconds=config.job_lease_seconds,
                poll_seconds=config.job_poll_seconds,
                retry_policy=RetryPolicy.from_settings(config),
            )
            if config.embed_worker
            else None
        )
        app.state.database = database
        app.state.worker = worker
        if dispatcher is None:
            app.state.dispatcher = QueueDispatcher(JobQueue(database))
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
    routes.register(app, config, dispatcher=dispatcher, id_token_verifier=id_token_verifier)

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
