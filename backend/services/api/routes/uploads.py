"""Upload intake: scoped target, streamed bytes with a limit, server-side verification."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import Row, insert, update

from services.api.auth import CurrentPrincipal, load_owned
from services.api.errors import ApiError
from services.api.routes.common import engine, settings, upload_store
from services.api.schemas import UploadCreateRequest, UploadResponse
from services.models import uploads
from services.storage import UploadTooLarge

router = APIRouter(tags=["uploads"])


def _target(upload_id: uuid.UUID) -> str:
    return f"/v1/uploads/{upload_id}/content"


def upload_response(row: Row[Any]) -> UploadResponse:
    return UploadResponse(
        id=row.id,
        state=row.state,
        target=_target(row.id),
        max_bytes=row.max_bytes,
        declared_size_bytes=row.declared_size_bytes,
        declared_sha256=row.declared_sha256,
        content_type=row.content_type,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _too_large(limit: int) -> ApiError:
    return ApiError(
        413,
        "UPLOAD_TOO_LARGE",
        f"Uploads are limited to {limit} bytes",
        action="fix_request",
    )


def _require_open(row: Row[Any], now: datetime) -> None:
    if row.state == "completed":
        raise ApiError(409, "UPLOAD_ALREADY_COMPLETED", "The upload is already complete")
    if row.expires_at <= now:
        raise ApiError(
            410, "UPLOAD_EXPIRED", "The upload target has expired", action="upload_again"
        )


@router.post("/uploads", status_code=201, response_model=UploadResponse)
async def create_upload(
    request: Request, body: UploadCreateRequest, principal: CurrentPrincipal
) -> UploadResponse:
    config = settings(request)
    if body.size_bytes > config.upload_max_bytes:
        raise _too_large(config.upload_max_bytes)
    now = datetime.now(UTC)
    values = {
        "id": uuid.uuid4(),
        "owner_id": principal.id,
        "state": "pending",
        "declared_size_bytes": body.size_bytes,
        "declared_sha256": body.sha256,
        "content_type": body.content_type,
        "max_bytes": config.upload_max_bytes,
        "storage_key": uuid.uuid4().hex,
        "expires_at": now + timedelta(seconds=config.upload_target_seconds),
        "created_at": now,
        "completed_at": None,
    }
    async with engine(request).begin() as connection:
        row = (await connection.execute(insert(uploads).values(values).returning(uploads))).one()
    return upload_response(row)


@router.put("/uploads/{upload_id}/content", status_code=204, response_model=None)
async def put_content(request: Request, upload_id: uuid.UUID, principal: CurrentPrincipal) -> None:
    now = datetime.now(UTC)
    # The row lock is held while bytes stream so a concurrent complete cannot verify
    # one body while another replaces it.
    async with engine(request).begin() as connection:
        row = await load_owned(connection, uploads, upload_id, principal, for_update=True)
        _require_open(row, now)
        limit = min(row.max_bytes, row.declared_size_bytes)
        declared_length = request.headers.get("content-length")
        if declared_length is not None and declared_length.isdigit():
            if int(declared_length) > limit:
                raise _too_large(limit)
        try:
            await upload_store(request).write(row.storage_key, request.stream(), limit)
        except UploadTooLarge:
            raise _too_large(limit) from None


@router.post("/uploads/{upload_id}/complete", response_model=UploadResponse)
async def complete_upload(
    request: Request, upload_id: uuid.UUID, principal: CurrentPrincipal
) -> UploadResponse:
    now = datetime.now(UTC)
    async with engine(request).begin() as connection:
        row = await load_owned(connection, uploads, upload_id, principal, for_update=True)
        if row.state == "completed":
            return upload_response(row)
        _require_open(row, now)
        store = upload_store(request)
        stored = await store.digest(row.storage_key)
        if stored is None:
            raise ApiError(
                409,
                "UPLOAD_CONTENT_MISSING",
                "No bytes have been uploaded to the target",
                action="upload_again",
            )
        size, sha256 = stored
        if size != row.declared_size_bytes or sha256 != row.declared_sha256:
            await store.delete(row.storage_key)
            raise ApiError(
                409,
                "UPLOAD_MISMATCH",
                "The uploaded bytes do not match the declared size and SHA-256",
                action="upload_again",
            )
        completed = (
            await connection.execute(
                update(uploads)
                .where(uploads.c.id == row.id)
                .values(state="completed", completed_at=now)
                .returning(uploads)
            )
        ).one()
    return upload_response(completed)
