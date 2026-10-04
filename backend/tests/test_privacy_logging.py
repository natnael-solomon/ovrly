import logging
import uuid
from io import StringIO

import pytest
from uvicorn.logging import AccessFormatter

from services.logging import configure_logging, request_log_id


@pytest.fixture
def safe_logging(caplog):
    factory = logging.getLogRecordFactory()
    configure_logging()
    caplog.set_level(logging.INFO)
    try:
        yield caplog
    finally:
        logging.setLogRecordFactory(factory)


def test_content_cannot_reach_handlers(safe_logging):
    logger = logging.getLogger("third.party")
    marker = "synthetic-private-transcript-api-key"
    logger.warning(marker)
    logger.warning("request body %s", {"transcript": marker})
    logger.warning("GET /%s?credential=%s", marker, marker)
    try:
        raise ValueError(marker)
    except ValueError:
        logger.exception(marker, extra={"body": marker, "credential": marker}, stack_info=True)
    for record in safe_logging.records:
        assert marker not in repr(record.__dict__)
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.args == ()
    assert "UNSTRUCTURED_LOG_REDACTED" in safe_logging.text


def test_only_ids_counts_and_fixed_codes_survive(safe_logging):
    logger = logging.getLogger("services.worker.runtime")
    job_id = uuid.uuid4()
    logger.error("Job %s failed in stage %s (%s)", job_id, "secret-stage", "private-exception")
    logger.info("Job %s retry %d (%s) scheduled in %.2fs", job_id, 2, "transient", 0.5)
    assert str(job_id) in safe_logging.text
    assert "retry 2 (transient) scheduled in 0.50s" in safe_logging.text
    assert "secret-stage" not in safe_logging.text
    assert "private-exception" not in safe_logging.text


def test_request_ids_are_correlatable_without_copying_headers(safe_logging):
    supplied = "synthetic-private-header"
    identifier = request_log_id(supplied)
    assert identifier == request_log_id(supplied)
    assert identifier != request_log_id(supplied + "-different")
    logging.getLogger(__name__).info("Request %s completed (%d)", identifier, 200)
    assert str(identifier) in safe_logging.text
    assert supplied not in safe_logging.text


def test_bad_log_arguments_fail_closed(safe_logging):
    logger = logging.getLogger(__name__)
    logger.info("Request %s completed (%d)", "private", "not-a-count")
    logger.info("Request %s completed (%d)", "private", float("nan"))
    logger.info("Request %s completed (%d)", {"private": "content"})
    assert safe_logging.text.count("INVALID_LOG_EVENT") == 3
    assert "private" not in safe_logging.text
    assert "not-a-count" not in safe_logging.text


def test_configuration_is_idempotent(safe_logging):
    configure_logging()
    configure_logging()
    logging.getLogger(__name__).warning("Worker failed; readiness is unavailable")
    assert safe_logging.text.count("Worker failed; readiness is unavailable") == 1


def test_uvicorn_access_formatter_is_replaced_before_logging(safe_logging):
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    logger = logging.getLogger("uvicorn.access")
    logger.addHandler(handler)
    previous_level = logger.level
    try:
        configure_logging()
        logger.setLevel(logging.INFO)
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1",
            "GET",
            "/synthetic-private?token=secret",
            "1.1",
            200,
        )
        assert "UNSTRUCTURED_LOG_REDACTED" in stream.getvalue()
        assert "synthetic-private" not in stream.getvalue()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
