"""Route registration for the versioned `/v1` API."""

from fastapi import FastAPI

from services.api import errors
from services.api.auth.google import GoogleIdTokenVerifier, IdTokenVerifier
from services.api.intake import InvestigationDispatcher
from services.api.routes import (
    captures,
    investigations,
    jobs,
    principals,
    reports,
    uploads,
    voice,
)
from services.settings import Settings
from services.storage import LocalFilesystemStore


def register(
    app: FastAPI,
    settings: Settings,
    dispatcher: InvestigationDispatcher | None = None,
    id_token_verifier: IdTokenVerifier | None = None,
) -> None:
    """Install routes and shared state.

    ``dispatcher`` ``None`` means the lifespan installs the production ``QueueDispatcher``
    once the database exists. ``id_token_verifier`` ``None`` selects Google verification
    against ``OVRLY_GOOGLE_CLIENT_ID``, or leaves account linking unavailable when that
    setting is empty.
    """
    app.state.settings = settings
    app.state.upload_store = LocalFilesystemStore(settings.storage_dir)
    app.state.dispatcher = dispatcher
    if id_token_verifier is None and settings.google_client_id:
        id_token_verifier = GoogleIdTokenVerifier(settings.google_client_id)
    app.state.id_token_verifier = id_token_verifier
    errors.install(app)
    app.include_router(principals.router, prefix="/v1")
    app.include_router(uploads.router, prefix="/v1")
    app.include_router(investigations.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(captures.router, prefix="/v1")
    app.include_router(reports.router, prefix="/v1")
    app.include_router(voice.router, prefix="/v1")
