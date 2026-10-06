"""The production stage table both worker entry points build (embedded and standalone)."""

from collections.abc import Mapping

from services.database import Database
from services.evidence.stages import EvidenceStages
from services.jobs.handlers import JobHandler
from services.pipeline.stub_reports import enable_stub_reports
from services.settings import Settings


def worker_handlers(
    database: Database,
    settings: Settings,
    base: Mapping[str, JobHandler],
    *,
    evidence: bool = True,
) -> dict[str, JobHandler]:
    """``base`` plus the evidence stages and the development stub, as configured.

    The evidence stages (BE-09, #27) join only when ``evidence`` is set and the server-side
    Scholarxiv key is configured. The development stub (``OVRLY_STUB_REPORTS``) is applied
    last, so it wins for the stages it covers.
    """
    handlers = dict(base)
    if evidence and settings.scholarxiv_api_key is not None:
        handlers.update(EvidenceStages(database, settings).handlers())
    if settings.stub_reports:
        enable_stub_reports(handlers)
    return handlers
