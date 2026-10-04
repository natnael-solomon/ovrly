"""Hook for handing a durable investigation record to the job engine (#16, PR 2 of BE-05)."""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection


class InvestigationDispatcher(Protocol):
    async def dispatch(self, connection: AsyncConnection, investigation_id: uuid.UUID) -> None:
        """Enqueue work inside the same transaction that wrote the investigation row."""


class RecordOnlyDispatcher:
    """Writes nothing beyond the investigation row; the job engine replaces it."""

    async def dispatch(self, connection: AsyncConnection, investigation_id: uuid.UUID) -> None:
        return None
