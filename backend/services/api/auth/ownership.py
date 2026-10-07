"""Owner-scoped loading. Objects owned by someone else are reported as missing (404)."""

import uuid
from typing import Any

from sqlalchemy import Row, Select, Table, select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth.dependency import Principal
from services.api.errors import ApiError


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "The requested resource was not found")


def owned_rows(table: Table, principal: Principal) -> Select[Any]:
    """Base query restricted to the caller's objects; every object route must build on it."""
    return select(table).where(table.c.owner_id == principal.id)


async def load_owned(
    connection: AsyncConnection,
    table: Table,
    object_id: uuid.UUID,
    principal: Principal,
    *,
    for_update: bool = False,
    for_no_key_update: bool = False,
) -> Row[Any]:
    query = owned_rows(table, principal).where(table.c.id == object_id)
    if for_update or for_no_key_update:
        query = query.with_for_update(key_share=for_no_key_update)
    result = await connection.execute(query)
    row: Row[Any] | None = result.first()
    if row is None:
        raise not_found()
    return row
