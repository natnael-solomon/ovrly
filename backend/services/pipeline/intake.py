"""The ``intake`` stage: the first job every recorded investigation is handed to.

The stage key is derived from the investigation id, so one investigation can have at most
one intake job however often the creating request is replayed. The handler does no media
processing yet (BE-07, #20): it checks that the investigation still exists and belongs to
the owner recorded in the payload, confirms the ``queued`` state at stage ``intake`` with the
coverage placeholder, and publishes a result row under the stage key.
"""

import hashlib
import uuid
from typing import Any, Final

from sqlalchemy import func, select, update

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
                select(investigations.c.id)
                .where(
                    investigations.c.id == investigation_id,
                    investigations.c.owner_id == owner_id,
                )
                .with_for_update()
            )
        ).first()
        if owned is None:
            raise NonRetriableInput("investigation is missing or not owned by the job owner")
        await connection.execute(
            update(investigations)
            .where(investigations.c.id == investigation_id)
            .values(state=INITIAL_STATE, stage=INTAKE_STAGE, updated_at=func.now())
        )
    return {
        "investigation_id": str(investigation_id),
        "stage": INTAKE_STAGE,
        "coverage": dict(COVERAGE_PLACEHOLDER),
    }
