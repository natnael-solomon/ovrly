"""Google ID token verification behind a small protocol so tests never contact Google."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class InvalidIdToken(Exception):
    """The token's signature, audience, issuer or expiry did not verify. No details kept."""


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    subject: str


class IdTokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedIdentity:
        """Return the stable provider subject or raise :class:`InvalidIdToken`."""


class GoogleIdTokenVerifier:
    """Verifies a Google ID token against the configured Web client ID audience."""

    def __init__(self, client_id: str):
        if not client_id:
            raise ValueError("A Google client ID is required")
        self.client_id = client_id

    def _verify(self, token: str) -> dict[str, Any]:
        # google-auth ships no type information; the call is isolated here and its result
        # is treated as an untyped claims mapping.
        verify: Callable[..., Any] = id_token.verify_oauth2_token
        claims = verify(token, google_requests.Request(), self.client_id)
        if not isinstance(claims, dict):
            raise InvalidIdToken
        return claims

    async def verify(self, token: str) -> VerifiedIdentity:
        try:
            claims = await asyncio.to_thread(self._verify, token)
        except (ValueError, GoogleAuthError):
            raise InvalidIdToken from None
        subject = claims.get("sub")
        if claims.get("iss") not in GOOGLE_ISSUERS or not isinstance(subject, str) or not subject:
            raise InvalidIdToken
        return VerifiedIdentity(provider="google", subject=subject)
