"""Backend-owned request/response models for BE-05. #15 will export them to packages/contracts."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Request bodies reject unknown fields, so a client-supplied `user_id` is an error."""

    model_config = ConfigDict(extra="forbid")


class GuestPrincipalRequest(StrictModel):
    pass


class Credential(BaseModel):
    token: str
    token_type: Literal["bearer"] = Field(default="bearer")


class GuestPrincipalResponse(BaseModel):
    principal_id: uuid.UUID
    kind: Literal["guest"]
    credential: Credential


class UploadCreateRequest(StrictModel):
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    content_type: str = Field(default="application/octet-stream", min_length=3, max_length=128)


UploadState = Literal["pending", "completed"]


class UploadResponse(BaseModel):
    id: uuid.UUID
    state: UploadState
    target: str
    max_bytes: int
    declared_size_bytes: int
    declared_sha256: str
    content_type: str
    expires_at: datetime
    created_at: datetime
    completed_at: datetime | None


class UrlSource(StrictModel):
    kind: Literal["url"]
    url: AnyHttpUrl
    duration_ms: int | None = Field(default=None, gt=0)


class UploadSource(StrictModel):
    kind: Literal["upload"]
    upload_id: uuid.UUID
    duration_ms: int | None = Field(default=None, gt=0)


InvestigationSource = Annotated[UrlSource | UploadSource, Field(discriminator="kind")]


class InvestigationCreateRequest(StrictModel):
    source: InvestigationSource


InvestigationState = Literal["queued", "running", "completed", "failed", "cancelled"]


class SafeError(BaseModel):
    code: str
    message: str
    retryable: bool


class InvestigationResponse(BaseModel):
    id: uuid.UUID
    state: InvestigationState
    stage: str
    coverage: dict[str, Any]
    version: int
    error: SafeError | None
    source: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class InvestigationListResponse(BaseModel):
    items: list[InvestigationResponse]


# ---- Account link (BC-D07, BE-05 part 2) ---------------------------------------------
# Kept at the end of the file so the #15 read-model additions above merge cleanly.


class AccountLinkRequest(StrictModel):
    provider: Literal["google"]
    id_token: str = Field(min_length=1, max_length=4096)


class AccountLinkResponse(BaseModel):
    principal_id: uuid.UUID
    kind: Literal["account"]
    linked: Literal[True]
    merged_saved_reports: int
    # Only present when the device continues as an existing account (second device).
    credential: Credential | None


# ---- End account link ------------------------------------------------------------------
