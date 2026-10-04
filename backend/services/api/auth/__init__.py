"""Guest principals, opaque bearer credentials, account linking and owner-scoped object access."""

from services.api.auth.credentials import hash_token, mint_token
from services.api.auth.dependency import (
    CurrentPrincipal,
    Principal,
    create_guest_principal,
    current_principal,
    reject_client_identity,
)
from services.api.auth.google import (
    GoogleIdTokenVerifier,
    IdTokenVerifier,
    InvalidIdToken,
    VerifiedIdentity,
)
from services.api.auth.linking import LinkOutcome, link_account, transfer_saved_reports
from services.api.auth.ownership import load_owned, owned_rows

__all__ = [
    "CurrentPrincipal",
    "GoogleIdTokenVerifier",
    "IdTokenVerifier",
    "InvalidIdToken",
    "LinkOutcome",
    "Principal",
    "VerifiedIdentity",
    "create_guest_principal",
    "current_principal",
    "hash_token",
    "link_account",
    "load_owned",
    "mint_token",
    "owned_rows",
    "reject_client_identity",
    "transfer_saved_reports",
]
