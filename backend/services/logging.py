"""Content-free logging for both entry points, including third-party log records."""

import hashlib
import logging
import math
from uuid import UUID

# Literal events only. Unknown messages (including third-party access/SQL logs) are
# replaced, not pattern-redacted: a transcript need not resemble a credential.
_MESSAGES = frozenset(
    {
        "Invalid backend environment configuration",
        "Worker ready (%s, %d stage handlers)",
        "Job %s cancelled before it started",
        "Job %s lease lost before it started",
        "Job %s cancelled during stage %s",
        "Job %s lease lost during stage %s",
        "Job %s infrastructure error in stage %s",
        "Job %s failed in stage %s (%s)",
        "Job %s not published: %s",
        "Job %s failed in stage %s (%s, retries exhausted after %d)",
        "Job %s retry %d (%s) scheduled in %.2fs",
        "Job %s lease lost before retry %s",
        "Job %s lease released %s",
        "Job %s lease could not be released; it will expire",
        "Worker failed; readiness is unavailable",
        "Worker stopped unexpectedly; readiness is unavailable",
        "Worker shutdown timed out; cancelling the owned task",
        "Worker could not run; check database and configuration",
        "Published job %s with fencing token %s",
        "Readiness failed: database unavailable",
        "Readiness failed: embedded worker unavailable",
        "Request %s failed: database unavailable",
        "Request %s failed: %s",
        "Request %s completed (%d)",
        "Retention policy disabled; no automatic deletion",
        "Retention completed (%d principals, %d uploads, %d jobs, %d purged)",
        "Stub reports enabled; fixture reports will be published",
        "Voice action %s %s",
    }
)
_CODES = frozenset(
    {
        "open_check",
        "save_report",
        "queue_cancel",
        "queue_retry",
        "queue_continue",
        "unsupported",
        "accepted",
        "denied",
        "transient",
        "rate_limited",
        "schema_repair",
        "invalid_model_schema",
        "unknown_outcome",
        "non_retriable_input",
        "INTERNAL_ERROR",
        "after an error",
        "during shutdown",
        "the job no longer exists",
        "the job was deleted (generation changed)",
        "the job was cancelled (generation changed)",
        "the lease is stale (fencing token changed)",
        "the lease is stale (owner changed)",
        "cancellation was requested",
        "a result for this stage key already exists",
        "the job is queued",
        "the job is leased",
        "the job is running",
        "the job is published",
        "the job is cancelled",
        "the job is deleted",
        "the job is failed",
    }
)


def request_log_id(request_id: str) -> UUID:
    """Correlate even client-chosen IDs without logging arbitrary header content."""
    return UUID(bytes=hashlib.sha256(request_id.encode()).digest()[:16])


def _argument(value: object) -> object:
    if isinstance(value, UUID):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, str) and value in _CODES:
        return value
    return "[redacted]"


def _record(
    name: str,
    level: int,
    pathname: str,
    lineno: int,
    msg: object,
    args: object,
    exc_info: object,
    func: str | None = None,
    sinfo: str | None = None,
    **kwargs: object,
) -> logging.LogRecord:
    trusted = isinstance(msg, str) and msg in _MESSAGES
    safe_args = tuple(_argument(value) for value in args) if isinstance(args, tuple) else ()
    message = "UNSTRUCTURED_LOG_REDACTED"
    if trusted:
        try:
            message = str(msg) % safe_args
        except (TypeError, ValueError):
            message = "INVALID_LOG_EVENT"
    return logging.LogRecord(
        "ovrly",
        level,
        "",
        0,
        message,
        (),
        None,
    )


_FIELDS = frozenset(logging.LogRecord("ovrly", logging.INFO, "", 0, "", (), None).__dict__)


class _RemoveExtras(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Logging adds ``extra`` *after* the factory; strip it before any sink sees it.
        for key in tuple(record.__dict__):
            if key not in _FIELDS:
                del record.__dict__[key]
        return True


_FILTER = _RemoveExtras()


def configure_logging() -> None:
    logging.setLogRecordFactory(_record)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    loggers = [logging.getLogger()]
    loggers.extend(
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    for logger in loggers:
        for handler in logger.handlers:
            handler.addFilter(_FILTER)
            # Access formatters unpack raw request arguments, which we deliberately erase.
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
