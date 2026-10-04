"""Principals: mint a guest credential, or link the calling guest to a Google account (BC-D07)."""

from fastapi import APIRouter, Depends, Request

from services.api.auth import CurrentPrincipal, create_guest_principal, reject_client_identity
from services.api.auth.google import IdTokenVerifier, InvalidIdToken
from services.api.auth.linking import link_account
from services.api.errors import ApiError
from services.api.routes.common import engine
from services.api.schemas import (
    AccountLinkRequest,
    AccountLinkResponse,
    Credential,
    GuestPrincipalRequest,
    GuestPrincipalResponse,
)

router = APIRouter(tags=["principals"], dependencies=[Depends(reject_client_identity)])


@router.post("/principals/guest", status_code=201, response_model=GuestPrincipalResponse)
async def create_guest(
    request: Request, body: GuestPrincipalRequest | None = None
) -> GuestPrincipalResponse:
    async with engine(request).begin() as connection:
        principal, token = await create_guest_principal(connection)
    return GuestPrincipalResponse(
        principal_id=principal.id, kind="guest", credential=Credential(token=token)
    )


@router.post("/principals/link", response_model=AccountLinkResponse)
async def link_principal(
    request: Request, body: AccountLinkRequest, principal: CurrentPrincipal
) -> AccountLinkResponse:
    verifier: IdTokenVerifier | None = request.app.state.id_token_verifier
    if verifier is None:
        raise ApiError(
            503,
            "ACCOUNT_LINK_UNAVAILABLE",
            "Account linking is not configured on this server",
        )
    try:
        identity = await verifier.verify(body.id_token)
    except InvalidIdToken:
        raise ApiError(
            401,
            "INVALID_ID_TOKEN",
            "The identity token could not be verified",
            action="authenticate",
        ) from None
    async with engine(request).begin() as connection:
        outcome = await link_account(connection, principal, identity)
    return AccountLinkResponse(
        principal_id=outcome.principal_id,
        kind="account",
        linked=True,
        merged_saved_reports=outcome.merged_saved_reports,
        credential=Credential(token=outcome.credential) if outcome.credential else None,
    )
