"""Hand-off of a durable investigation record to the job engine (#16, BE-05 part 2).

The dispatcher runs inside the transaction that writes the investigation row, so the
business record and the queue row commit or roll back together. The stage key is derived
from the investigation id, so a replayed request can never enqueue a second job.
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from services.jobs.queue import JobQueue
from services.pipeline.intake import intake_payload, intake_stage_key


class InvestigationDispatcher(Protocol):
    async def dispatch(
        self, connection: AsyncConnection, investigation_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """Enqueue work inside the same transaction that wrote the investigation row."""


class RecordOnlyDispatcher:
    """Writes nothing beyond the investigation row; tests use it to isolate the API."""

    async def dispatch(
        self, connection: AsyncConnection, investigation_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        return None


class QueueDispatcher:
    """Production dispatcher: one ``intake`` job per investigation, in the caller's transaction."""

    def __init__(self, queue: JobQueue):
        self.queue = queue

    async def dispatch(
        self, connection: AsyncConnection, investigation_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        await self.queue.enqueue(
            connection,
            intake_stage_key(investigation_id),
            intake_payload(investigation_id, owner_id),
        )
