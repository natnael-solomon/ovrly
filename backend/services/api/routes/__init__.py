"""Route registration for the versioned `/v1` API."""

from fastapi import FastAPI

from services.api import errors
from services.api.intake import InvestigationDispatcher, RecordOnlyDispatcher
from services.api.routes import investigations, jobs, principals, uploads
from services.settings import Settings
from services.storage import LocalFilesystemStore


def register(
    app: FastAPI, settings: Settings, dispatcher: InvestigationDispatcher | None = None
) -> None:
    app.state.settings = settings
    app.state.upload_store = LocalFilesystemStore(settings.storage_dir)
    app.state.dispatcher = dispatcher if dispatcher is not None else RecordOnlyDispatcher()
    errors.install(app)
    app.include_router(principals.router, prefix="/v1")
    app.include_router(uploads.router, prefix="/v1")
    app.include_router(investigations.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
