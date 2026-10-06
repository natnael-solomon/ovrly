"""The ``intake`` stage: the first job every recorded investigation is handed to.

The stage key is derived from the investigation id, so one investigation can have at most
one intake job however often the creating request is replayed. The handler checks the
owned investigation and defers upload-backed media validation until fenced publication.
It never resets progress on the investigation; URL references remain intake-only.
"""

import hashlib
import uuid
from typing import Any, Final

from sqlalchemy import select

from services.jobs.handlers import JobContext
from services.jobs.queue import ClaimedJob, StageKey
from services.jobs.retries import NonRetriableInput
from services.models import investigations

INTAKE_STAGE: Final = "intake"
INTAKE_VERSION: Final = 1
INITIAL_STATE: Final = "queued"
COVERAGE_PLACEHOLDER: Final[dict[str, Any]] = {"status": "not_started"}


def intake_stage_key(investigation_id: uuid.UUID) -> StageKey:
    digest = hashlib.sha256(f"investigation:{investigation_id}".encode()).hexdigest()
    return StageKey(INTAKE_VERSION, INTAKE_STAGE, digest)


def intake_payload(investigation_id: uuid.UUID, owner_id: uuid.UUID) -> dict[str, Any]:
    return {"investigation_id": str(investigation_id), "owner_id": str(owner_id)}


def _identifier(payload: dict[str, Any], field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[field]))
    except (KeyError, ValueError, AttributeError):
        raise NonRetriableInput(f"intake payload has no valid {field}") from None


async def intake_stage(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
    investigation_id = _identifier(job.payload, "investigation_id")
    owner_id = _identifier(job.payload, "owner_id")
    async with context.queue.database.engine.begin() as connection:
        owned = (
            await connection.execute(
                select(investigations.c.id, investigations.c.source_kind)
                .where(
                    investigations.c.id == investigation_id,
                    investigations.c.owner_id == owner_id,
                )
                .with_for_update()
            )
        ).first()
        if owned is None:
            raise NonRetriableInput("investigation is missing or not owned by the job owner")
        if owned.source_kind == "upload":
            from services.pipeline.media_validation import media_stage_key

            context.enqueue_after_publish(
                media_stage_key(investigation_id), intake_payload(investigation_id, owner_id)
            )
    return {
        "investigation_id": str(investigation_id),
        "stage": INTAKE_STAGE,
        "coverage": dict(COVERAGE_PLACEHOLDER),
    }
