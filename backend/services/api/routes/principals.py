"""`POST /v1/principals/guest`: mint a guest principal and its opaque bearer credential."""

from fastapi import APIRouter, Depends, Request

from services.api.auth import create_guest_principal, reject_client_identity
from services.api.routes.common import engine
from services.api.schemas import Credential, GuestPrincipalRequest, GuestPrincipalResponse

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
