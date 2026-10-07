"""The production stage table both worker entry points build (embedded and standalone)."""

import logging
from collections.abc import Mapping

from services.database import Database
from services.evidence.stages import EvidenceStages
from services.jobs.handlers import JobHandler
from services.pipeline.stub_reports import enable_stub_reports
from services.settings import Settings

logger = logging.getLogger(__name__)


def worker_handlers(
    database: Database,
    settings: Settings,
    base: Mapping[str, JobHandler],
    *,
    evidence: bool = True,
) -> dict[str, JobHandler]:
    """``base`` plus the evidence stages and the development stub, as configured.

    The evidence stages (BE-09, #27) join whenever ``evidence`` is set. Without the
    server-side Scholarxiv key their jobs fail with the typed ``EvidenceUnavailable``
    reason instead of waiting unclaimed (#127). The development stub
    (``OVRLY_STUB_REPORTS``) is applied last, so it wins for the stages it covers.
    """
    handlers = dict(base)
    if evidence:
        if settings.scholarxiv_api_key is None:
            logger.warning("Scholarxiv key missing; evidence jobs will fail as unavailable")
        handlers.update(EvidenceStages(database, settings).handlers())
    if settings.stub_reports:
        enable_stub_reports(handlers)
    return handlers
