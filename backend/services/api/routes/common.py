"""Shared route helpers."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from services.settings import Settings
from services.storage import UploadStore


def engine(request: Request) -> AsyncEngine:
    value: AsyncEngine = request.app.state.database.engine
    return value


def settings(request: Request) -> Settings:
    value: Settings = request.app.state.settings
    return value


def upload_store(request: Request) -> UploadStore:
    value: UploadStore = request.app.state.upload_store
    return value
