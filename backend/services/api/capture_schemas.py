"""Live capture wire models; existing session/chunk shapes remain unchanged."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from services.api.schemas import (
    CoverageStatus,
    ExtractionProgress,
    Modality,
    ProcessingStatus,
    ReconciliationProgress,
    SafeError,
    StrictModel,
)

MAX_CAPTURE_MS = 180_000


class CaptureCreateRequest(StrictModel):
    chunk_duration_ms: int = Field(default=10_000, ge=1000, le=30_000, strict=True)


class CaptureCloseRequest(StrictModel):
    continue_research: bool = Field(strict=True)
    duration_ms: int | None = Field(default=None, ge=0, le=MAX_CAPTURE_MS, strict=True)


class CaptureInterval(StrictModel):
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, le=MAX_CAPTURE_MS, strict=True)
    timebase: Literal["capture"]

    @model_validator(mode="after")
    def ordered(self) -> "CaptureInterval":
        if self.end_ms <= self.start_ms:
            raise ValueError("Capture intervals must be nonempty and ordered")
        return self


class CaptureChunkRequest(StrictModel):
    session_id: uuid.UUID
    seq: int = Field(ge=0, strict=True)
    interval: CaptureInterval
    size_bytes: int = Field(gt=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}\z")
    content_type: str = Field(min_length=3, max_length=128)


class CaptureMetadata(StrictModel):
    chunk: CaptureChunkRequest
    modality: Modality


class SeqRange(BaseModel):
    from_seq: int
    to_seq: int


class CaptureSession(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    state: Literal["open", "closed", "abandoned"]
    timebase: Literal["capture"] = "capture"
    started_at: datetime
    closed_at: datetime | None
    max_duration_ms: int = MAX_CAPTURE_MS
    chunk_duration_ms: int
    chunks_received: int
    highest_seq: int | None
    received_ms: int
    gaps: list[SeqRange]
    duplicate_handling: Literal["replay_acknowledgement"] = "replay_acknowledgement"
    out_of_order_handling: Literal["accept_and_record_gaps"] = "accept_and_record_gaps"


class CaptureChunk(BaseModel):
    session_id: uuid.UUID
    seq: int
    interval: CaptureInterval
    size_bytes: int
    sha256: str
    disposition: Literal["stored", "duplicate", "out_of_order"]
    received_at: datetime
    gaps: list[SeqRange]


class ModalityCoverage(BaseModel):
    speech: list[CaptureInterval]
    text: list[CaptureInterval]


class CaptureManifest(BaseModel):
    duration_ms: int
    missing_intervals: list[CaptureInterval]
    declared_coverage: ModalityCoverage


class CaptureClaimState(StrictModel):
    claim_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\z")
    processing_status: ProcessingStatus
    error: SafeError | None


class CaptureWork(BaseModel):
    seq: int
    job_id: uuid.UUID | None
    processing_status: ProcessingStatus
    error: SafeError | None


class CaptureStatus(BaseModel):
    session: CaptureSession
    continue_research: bool | None
    expires_at: datetime
    manifest: CaptureManifest
    work: list[CaptureWork]
    claims: list[CaptureClaimState]
    claim_extraction_status: CoverageStatus = "not_started"
    extraction_progress: ExtractionProgress | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reconciliation_progress: "ReconciliationProgress | None" = Field(
        default=None, exclude_if=lambda value: value is None
    )
