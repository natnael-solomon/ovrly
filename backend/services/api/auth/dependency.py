"""Bearer authentication dependency and guest principal creation."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.auth.credentials import hash_token, mint_token
from services.api.errors import ApiError
from services.models import credentials, principals

_CLIENT_IDENTITY_PARAMS = {"user_id", "owner_id", "principal_id"}
_CLIENT_IDENTITY_HEADERS = {"x-user-id", "x-owner-id", "x-principal-id"}
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    kind: str


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.database.engine
    return engine


async def create_guest_principal(connection: AsyncConnection) -> tuple[Principal, str]:
    """Insert a guest principal and one credential; return the principal and raw token."""
    now = datetime.now(UTC)
    principal = Principal(id=uuid.uuid4(), kind="guest")
    token = mint_token()
    await connection.execute(
        insert(principals).values(id=principal.id, kind=principal.kind, created_at=now)
    )
    await connection.execute(
        insert(credentials).values(
            id=uuid.uuid4(),
            principal_id=principal.id,
            token_hash=hash_token(token),
            created_at=now,
            revoked_at=None,
        )
    )
    return principal, token


def reject_client_identity(request: Request) -> None:
    """Identity comes only from the credential; a client-supplied identity is an error."""
    if _CLIENT_IDENTITY_PARAMS & {key.lower() for key in request.query_params} or (
        _CLIENT_IDENTITY_HEADERS & {key.lower() for key in request.headers}
    ):
        raise ApiError(
            400,
            "CLIENT_IDENTITY_REJECTED",
            "Identity is taken from the bearer credential",
            action="fix_request",
        )


def _invalid_credential() -> ApiError:
    return ApiError(
        401,
        "INVALID_CREDENTIAL",
        "The bearer credential is not valid",
        action="authenticate",
        headers=_CHALLENGE,
    )


async def current_principal(request: Request) -> Principal:
    reject_client_identity(request)
    header = request.headers.get("Authorization", "")
    if not header:
        raise ApiError(
            401,
            "AUTHENTICATION_REQUIRED",
            "A bearer credential is required",
            action="authenticate",
            headers=_CHALLENGE,
        )
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise _invalid_credential()
    query = (
        select(principals.c.id, principals.c.kind)
        .select_from(credentials.join(principals, credentials.c.principal_id == principals.c.id))
        .where(
            credentials.c.token_hash == hash_token(token),
            credentials.c.revoked_at.is_(None),
        )
    )
    async with _engine(request).connect() as connection:
        row = (await connection.execute(query)).first()
    if row is None:
        raise _invalid_credential()
    return Principal(id=row.id, kind=row.kind)


async def lock_active_principal(connection: AsyncConnection, principal: Principal) -> None:
    """Share-lock the caller's principal row for the rest of the transaction.

    ``current_principal`` authenticates on its own connection, so a second-device link
    (BC-D07) can merge the caller after authentication. The share lock waits for a link in
    progress; a principal merged into an account is refused exactly like a revoked
    credential, so nothing is written under it after its saved reports moved.
    """
    row = (
        await connection.execute(
            select(principals.c.merged_into)
            .where(principals.c.id == principal.id)
            .with_for_update(read=True)
        )
    ).first()
    if row is None or row.merged_into is not None:
        raise _invalid_credential()


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
