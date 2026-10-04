"""Request and read models mirrored by the JSON Schemas in packages/contracts (BE-03, #15)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from services.jobs.retries import RetryClass
from services.jobs.states import JobState

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


# Read models for the BE-03 (#15) contract: report versions, claims, evidence, assessments
# and the job summary nested in an investigation read. No route emits them yet (reports are
# BE-10, #33); packages/contracts/roundtrip.py serialises them with the production
# serialiser so the committed result fixtures are exactly what this code produces. The
# enum literals mirror packages/contracts/schemas/enums.schema.json; a test diffs them.

ProcessingStatus = Literal["waiting", "checking", "partial", "complete", "failed", "cancelled"]
Stage = Literal[
    "intake",
    "media_validation",
    "asr",
    "device_text",
    "claim_extraction",
    "retrieval",
    "assessment",
    "reconciliation",
    "publication",
]
CoverageStatus = Literal["not_started", "partial", "complete"]
Timebase = Literal["capture", "media"]
Modality = Literal["speech", "text", "both"]
Relation = Literal["support", "challenge", "qualify", "mixed", "insufficient"]
OverallAssessment = Literal[
    "supported", "challenged", "qualified", "mixed", "insufficient_evidence"
]
SourceInspectionLevel = Literal["abstract_only", "full_text", "metadata_only", "unknown"]
SourceType = Literal[
    "peer_reviewed",
    "preprint",
    "government",
    "news",
    "reference_work",
    "primary_document",
    "organization",
    "other",
]
RetrievalRelevance = Literal["high", "medium", "low"]
RetractionStatus = Literal["none", "corrected", "retracted", "withdrawn", "unknown"]
CorrectionAttribution = Literal["user", "pipeline"]


class Coverage(BaseModel):
    """What share of the eligible media has been checked; never a truth verdict."""

    status: CoverageStatus
    covered_ms: int | None = None
    total_ms: int | None = None


class Interval(BaseModel):
    """Half-open [start_ms, end_ms) on the named timebase."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    timebase: Timebase


class ClaimCorrection(BaseModel):
    """A user or pipeline correction of the normalized meaning; the original wording is kept."""

    attributed_to: CorrectionAttribution
    corrected_at: datetime
    superseded_proposition: str


class Claim(BaseModel):
    id: str
    occurrence_id: str
    interval: Interval
    modality: Modality
    original_text: str
    proposition: str
    correction: ClaimCorrection | None


class EvidenceSource(BaseModel):
    id: str
    title: str
    publisher: str
    url: str | None
    published_at: datetime | None


class Evidence(BaseModel):
    """A retrieved source. Relevance says it was worth reading; it never says it supports."""

    id: str
    claim_id: str
    source: EvidenceSource
    source_type: SourceType
    inspection_level: SourceInspectionLevel
    retrieval_relevance: RetrievalRelevance
    retraction_status: RetractionStatus
    excerpt: str | None
    retrieved_at: datetime


class EvidenceRelation(BaseModel):
    evidence_id: str
    relation: Relation
    note: str | None


class Assessment(BaseModel):
    id: str
    claim_id: str
    version: int
    relations: list[EvidenceRelation]
    overall: OverallAssessment
    provisional: bool
    summary: str


class ReportVersion(BaseModel):
    """Immutable report version; corrections and expansions create a new one."""

    id: str
    investigation_id: uuid.UUID
    version: int
    created_at: datetime
    provisional: bool
    change_summary: str
    supersedes: str | None
    claims: list[Claim]
    evidence: list[Evidence]
    assessments: list[Assessment]


class JobSummary(BaseModel):
    """Client-visible subset of a `jobs` row; lease and fencing internals stay server-side."""

    id: uuid.UUID
    state: JobState
    stage: str
    cancel_requested: bool
    attempts: int
    retry_class: RetryClass | None
    available_at: datetime
    updated_at: datetime


class InvestigationReadModel(InvestigationResponse):
    """The investigation read payload of the contract: `InvestigationResponse` plus status,
    job and report. Routes still return the parent until #19 part 2 / #33 adopt this."""

    processing_status: ProcessingStatus
    job: JobSummary | None
    report: ReportVersion | None


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
