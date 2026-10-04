"""Guest principals, opaque bearer credentials and owner-scoped object access."""

from services.api.auth.credentials import hash_token, mint_token
from services.api.auth.dependency import (
    CurrentPrincipal,
    Principal,
    create_guest_principal,
    current_principal,
    reject_client_identity,
)
from services.api.auth.ownership import load_owned, owned_rows

__all__ = [
    "CurrentPrincipal",
    "Principal",
    "create_guest_principal",
    "current_principal",
    "hash_token",
    "load_owned",
    "mint_token",
    "owned_rows",
    "reject_client_identity",
]
