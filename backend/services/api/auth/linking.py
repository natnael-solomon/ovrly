"""Account linking (BC-D07): upgrade a guest in place, or continue a device as an existing account.

Every write below is restricted to the calling principal, identified only by its bearer
credential, and the whole operation runs in the caller's transaction. Objects are never
moved by client-supplied identifiers.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth.credentials import hash_token, mint_token
from services.api.auth.dependency import Principal
from services.api.auth.google import VerifiedIdentity
from services.api.errors import ApiError
from services.models import credentials, principals, saved_reports

ACCOUNT_KIND = "account"


@dataclass(frozen=True)
class LinkOutcome:
    principal_id: uuid.UUID
    merged_saved_reports: int
    # Present only when the device continues as another principal (second-device case).
    credential: str | None


def already_linked() -> ApiError:
    return ApiError(
        409,
        "ACCOUNT_ALREADY_LINKED",
        "This principal is already linked to a different account",
    )


async def transfer_saved_reports(
    connection: AsyncConnection, from_principal: uuid.UUID, to_principal: uuid.UUID
) -> int:
    """Move the explicitly saved reports of ``from_principal`` to ``to_principal``.

    Runs in the caller's link transaction and touches only rows owned by ``from_principal``.
    A report both principals saved keeps the account's earlier save and drops the guest's
    duplicate; the count is the number of saves that moved. Investigations, uploads,
    idempotency keys and temporary history are deliberately not transferred (BC-D07).
    """
    already_saved = select(saved_reports.c.report_id).where(
        saved_reports.c.owner_id == to_principal
    )
    await connection.execute(
        delete(saved_reports).where(
            saved_reports.c.owner_id == from_principal,
            saved_reports.c.report_id.in_(already_saved),
        )
    )
    moved = await connection.execute(
        update(saved_reports)
        .where(saved_reports.c.owner_id == from_principal)
        .values(owner_id=to_principal)
    )
    return moved.rowcount


async def link_account(
    connection: AsyncConnection, principal: Principal, identity: VerifiedIdentity
) -> LinkOutcome:
    # Serialise links for one provider subject so two devices cannot both become the account.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:subject))"),
        {"subject": f"{identity.provider}:{identity.subject}"},
    )
    caller = (
        await connection.execute(
            select(principals.c.id, principals.c.google_sub)
            .where(principals.c.id == principal.id)
            .with_for_update()
        )
    ).one()
    if caller.google_sub == identity.subject:
        return LinkOutcome(principal.id, 0, None)
    if caller.google_sub is not None:
        raise already_linked()
    existing = (
        await connection.execute(
            select(principals.c.id)
            .where(principals.c.google_sub == identity.subject)
            .with_for_update()
        )
    ).first()
    now = datetime.now(UTC)
    if existing is None:
        await connection.execute(
            update(principals)
            .where(principals.c.id == principal.id)
            .values(google_sub=identity.subject, kind=ACCOUNT_KIND)
        )
        return LinkOutcome(principal.id, 0, None)
    account_id: uuid.UUID = existing.id
    merged = await transfer_saved_reports(connection, principal.id, account_id)
    await connection.execute(
        update(credentials)
        .where(credentials.c.principal_id == principal.id, credentials.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await connection.execute(
        update(principals)
        .where(principals.c.id == principal.id)
        .values(merged_into=account_id, merged_at=now)
    )
    token = mint_token()
    await connection.execute(
        insert(credentials).values(
            id=uuid.uuid4(),
            principal_id=account_id,
            token_hash=hash_token(token),
            created_at=now,
            revoked_at=None,
        )
    )
    return LinkOutcome(account_id, merged, token)
