"""Opaque bearer tokens: random, stored only as a SHA-256 digest, never logged."""

import hashlib
import secrets

_OPAQUE_PREFIX = "ovk_"
_TOKEN_BYTES = 32


def mint_token() -> str:
    return _OPAQUE_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
