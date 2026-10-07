"""Shared error shape `{code, message, retryable, action, request_id}` for every API error."""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from services.logging import request_log_id

logger = logging.getLogger(__name__)

Action = Literal["none", "retry", "authenticate", "fix_request", "upload_again"]
REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_IDENTITY_FIELDS = {"user_id", "owner_id", "principal_id"}
# error.schema.json caps the message at 240 characters.
_MESSAGE_LIMIT = 240


def extraction_failure_code(failure: str | None) -> str:
    return {
        "ExtractionInvalid": "EXTRACTION_INVALID",
        "ExtractionUnavailable": "EXTRACTION_UNAVAILABLE",
        "ExtractionDenied": "EXTRACTION_DENIED",
        "InvalidCooldown": "EXTRACTION_UNAVAILABLE",
        "ExtractionBudgetExceeded": "EXTRACTION_BUDGET_EXHAUSTED",
        "UnknownOutcome": "EXTRACTION_OUTCOME_UNKNOWN",
    }.get(failure or "", "PROCESSING_FAILED")


def evidence_failure_code(failure: str | None) -> str:
    """Safe code of a terminally failed ``retrieval`` or ``assessment`` job (#127)."""
    return "EVIDENCE_UNAVAILABLE" if failure == "EvidenceUnavailable" else "EVIDENCE_FAILED"


class ApiError(Exception):
    """Typed, client-safe error. Messages must never contain internals or secrets."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        action: Action = "none",
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.action = action
        self.headers = headers


def request_id_of(request: Request) -> str:
    stored = getattr(request.state, "request_id", None)
    if isinstance(stored, str):
        return stored
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    generated = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
    request.state.request_id = generated
    return generated


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    action: Action = "none",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    body = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "action": action,
        "request_id": request_id,
    }
    return JSONResponse(
        body, status_code=status_code, headers={**(headers or {}), REQUEST_ID_HEADER: request_id}
    )


_STATUS_CODES = {
    404: ("NOT_FOUND", "The requested resource was not found"),
    405: ("METHOD_NOT_ALLOWED", "The method is not allowed for this resource"),
}


def _validation_message(exc: RequestValidationError) -> tuple[str, str]:
    paths = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if not isinstance(part, int)]
        if location and location[-1] in _IDENTITY_FIELDS:
            return "CLIENT_IDENTITY_REJECTED", "Identity is taken from the bearer credential"
        if error.get("type") == "extra_forbidden":
            # The last element is a key the client chose; never echo it (#28).
            location = location[:-1]
        paths.append(".".join(location) or "body")
    unique = ", ".join(dict.fromkeys(paths)) or "body"
    message = f"The request is invalid at: {unique}"
    if len(message) > _MESSAGE_LIMIT:
        suffix = ", ..."
        head = message[: _MESSAGE_LIMIT - len(suffix)]
        # Prefer ending on a whole path; a single over-long path is cut mid-path instead.
        trimmed = head.rsplit(",", 1)[0] if "," in head else head.rstrip(".")
        message = (trimmed + suffix)[:_MESSAGE_LIMIT]
    return "VALIDATION_FAILED", message


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def tag_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request_id_of(request)
        response = await call_next(request)
        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
        logger.info("Request %s completed (%d)", request_log_id(request_id), response.status_code)
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            request,
            exc.status_code,
            exc.code,
            exc.message,
            retryable=exc.retryable,
            action=exc.action,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        code, message = _validation_message(exc)
        return error_response(request, 422, code, message, action="fix_request")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _STATUS_CODES.get(exc.status_code, ("REQUEST_FAILED", "Request failed"))
        headers = dict(exc.headers) if exc.headers else None
        return error_response(request, exc.status_code, code, message, headers=headers)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(TimeoutError)
    async def database_error(request: Request, exc: Exception) -> JSONResponse:
        logger.warning(
            "Request %s failed: database unavailable", request_log_id(request_id_of(request))
        )
        return error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "The service is temporarily unavailable",
            retryable=True,
            action="retry",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Request %s failed: %s", request_log_id(request_id_of(request)), "INTERNAL_ERROR"
        )
        return error_response(request, 500, "INTERNAL_ERROR", "An internal error occurred")


def safe_error(error_code: str | None) -> dict[str, Any] | None:
    """Translate a stored error code into the client-safe error object, or ``None``."""
    if error_code is None:
        return None
    media_errors = {
        "ASR_QUOTA_EXHAUSTED": (
            "Speech quota is exhausted; automatic attempts have stopped",
            False,
        ),
        "ASR_UNAVAILABLE": ("Speech transcription is unavailable", False),
        "INVALID_MEDIA": ("The uploaded media could not be processed", False),
        "MEDIA_SIZE_LIMIT_EXCEEDED": ("The media exceeds the permitted size", False),
        "DURATION_LIMIT_EXCEEDED": ("The media exceeds the permitted duration", False),
        "MEDIA_PROCESSING_TIMEOUT": ("Media processing exceeded its time limit", True),
        "MEDIA_PROCESSING_UNAVAILABLE": ("Media processing is temporarily unavailable", True),
        "EVIDENCE_UNAVAILABLE": ("Evidence checking is unavailable", False),
        "EVIDENCE_FAILED": ("Evidence checking failed", False),
        "CLAIM_UNASSESSED": ("This claim was not assessed; the report says why", False),
    }
    if error_code in media_errors:
        message, retryable = media_errors[error_code]
        return {"code": error_code, "message": message, "retryable": retryable}
    return {"code": error_code, "message": "Processing failed", "retryable": False}
