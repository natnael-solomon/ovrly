"""Request and read models mirrored by the JSON Schemas in packages/contracts (BE-03, #15)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

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
    speech: "SpeechResult | None" = None
    analysis: "AnalysisRead | None" = None


# Read models for the BE-03 (#15) contract: report versions, claims, evidence, assessments
# and the job summary nested in an investigation read, emitted by the investigation and report
# routes (BE-10, #33); packages/contracts/roundtrip.py serialises them with the production
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
MediaSpeechStatus = Literal["pending", "unavailable"]
MediaTextStatus = Literal["pending"]
SpeechUnavailableReason = Literal["no_audio_track"]
SpeechStatus = Literal["pending", "running", "completed", "unavailable"]
SpeechReason = Literal[
    "disabled",
    "no_audio_track",
    "quota_exhausted",
    "unknown_outcome",
    "provider_unavailable",
    "cancelled",
]
ASRProvider = Literal["groq"]
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


class MediaCoverage(BaseModel):
    has_audio: bool
    has_video: bool
    speech_status: MediaSpeechStatus
    text_status: MediaTextStatus
    speech_unavailable_reason: SpeechUnavailableReason | None


class Coverage(BaseModel):
    """What share of the eligible media has been checked; never a truth verdict."""

    status: CoverageStatus
    covered_ms: int | None = None
    total_ms: int | None = None
    media: MediaCoverage | None = None


class Interval(BaseModel):
    """Half-open [start_ms, end_ms) on the named timebase."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    timebase: Timebase


class SpeechSegment(BaseModel):
    text: str
    interval: Interval


class SpeechResult(BaseModel):
    status: SpeechStatus
    reason: SpeechReason | None = None
    provider: ASRProvider | None = None
    model: str | None = None
    processing_version: int | None = None
    source_sha256: str | None = None
    audio_sha256: str | None = None
    settings_sha256: str | None = None
    segments: list[SpeechSegment] = Field(default_factory=list)


AnalysisStatus = Literal["pending", "partial", "no_usable", "complete"]
AnalysisModality = Literal["speech", "text"]


class AnalysisGap(BaseModel):
    modality: AnalysisModality
    reason: Literal[
        "NO_AUDIO_TRACK",
        "ASR_QUOTA_EXHAUSTED",
        "ASR_OUTCOME_UNKNOWN",
        "ASR_DISABLED",
        "ASR_UNAVAILABLE",
        "DEVICE_TEXT_MISSING",
        "DEVICE_TEXT_INCOMPLETE",
        "DEVICE_TEXT_FRAME_FAILED",
        "CAPTURE_CHUNK_MISSING",
        "CAPTURE_CHUNK_INVALID",
    ]
    interval: Interval | None


class AnalysisRead(BaseModel):
    """Extraction only. Sampled frames never assert coverage between those frames."""

    status: AnalysisStatus
    text_deadline: datetime | None
    text_expired: bool
    analyzed_modalities: list[AnalysisModality]
    pending_modalities: list[AnalysisModality]
    unavailable_modalities: list[AnalysisModality]
    gaps: list[AnalysisGap]
    text: dict[str, Any] | None
    captions: list[SpeechSegment]


class SpeechRetryRequest(StrictModel):
    protocol_version: Literal[1]


SpeechRetryOutcome = Literal[
    "accepted", "quota_exhausted", "not_eligible", "already_complete", "in_progress"
]


class SpeechRetryResponse(BaseModel):
    protocol_version: Literal[1] = 1
    investigation_id: uuid.UUID
    outcome: SpeechRetryOutcome


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
    # True only for a development stub built from the contract fixtures (OVRLY_STUB_REPORTS);
    # such a version is never a check of the media and clients label it as a fixture.
    fixture: bool = False


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
    job and report. Returned by every investigation route (BE-10, #33)."""

    processing_status: ProcessingStatus
    job: JobSummary | None
    report: ReportVersion | None


class InvestigationListResponse(BaseModel):
    items: list[InvestigationReadModel]


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


# ---- Reports, saves and voice actions (BE-10, #33) ------------------------------------


class ReportVersionSummary(BaseModel):
    """One entry of the version list: what changed, without the claims and evidence.

    ``fixture`` is true only for a development stub (``OVRLY_STUB_REPORTS``).
    """

    id: str
    version: int
    created_at: datetime
    provisional: bool
    change_summary: str
    supersedes: str | None
    fixture: bool


class ReportVersionListResponse(BaseModel):
    investigation_id: uuid.UUID
    items: list[ReportVersionSummary]


class SavedReport(BaseModel):
    """An explicit save: the caller's snapshot of one immutable report version."""

    report_id: str
    investigation_id: uuid.UUID
    version: int
    saved_at: datetime
    report: ReportVersion


class SavedReportListResponse(BaseModel):
    items: list[SavedReport]


OpaqueId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]
VoiceActionName = Literal[
    "open_check", "save_report", "queue_cancel", "queue_retry", "queue_continue"
]
VoiceTargetKind = Literal["investigation", "report", "job"]
VOICE_TARGET_KINDS: dict[str, str] = {
    "open_check": "investigation",
    "save_report": "report",
    "queue_cancel": "job",
    "queue_retry": "job",
    "queue_continue": "job",
}
VoiceErrorCode = Literal[
    "VOICE_ACTION_UNSUPPORTED",
    "VOICE_TARGET_NOT_FOUND",
    "VOICE_TARGET_NOT_OWNED",
    "VOICE_ACTION_INVALID_STATE",
]


class VoiceTarget(StrictModel):
    kind: VoiceTargetKind
    id: OpaqueId


class VoiceActionEnvelope(StrictModel):
    """The parts every voice request must have before the allowlist is consulted."""

    request_id: OpaqueId
    action: str = Field(min_length=1, max_length=64)
    target: Any = None


class VoiceActionRequest(StrictModel):
    request_id: OpaqueId
    action: VoiceActionName
    target: VoiceTarget

    @model_validator(mode="after")
    def target_matches_action(self) -> Self:
        if self.target.kind != VOICE_TARGET_KINDS[self.action]:
            raise ValueError(f"{self.action} requires a {VOICE_TARGET_KINDS[self.action]} target")
        return self


class VoiceError(BaseModel):
    code: VoiceErrorCode
    message: str
    retryable: bool
    action: Literal["none", "fix_request"]
    request_id: str


class VoiceActionResponse(BaseModel):
    """Serialised with ``exclude_none``: ``target`` and ``error`` are absent, never null."""

    request_id: str
    result: Literal["accepted", "denied"]
    action: str
    target: VoiceTarget | None = None
    message: str
    error: VoiceError | None = None


VersionNumber = Annotated[int, Field(ge=1, le=2_147_483_647)]
ReanalysisReason = Literal["correction", "expansion", "deeper"]


class CorrectionReanalysis(StrictModel):
    """Correct one claim's normalized meaning; its original wording is kept."""

    reason: Literal["correction"]
    base_version: VersionNumber
    claim_id: OpaqueId
    proposition: str = Field(min_length=1, max_length=2000)


class ExpansionReanalysis(StrictModel):
    """Check the full video the captured clip was matched to; the user must confirm the match.

    ``source_investigation_id`` names the caller's investigation of that full video. It is
    optional here only so the contract addition stays non-breaking; the server requires it
    whenever ``match_confirmed`` is true.
    """

    reason: Literal["expansion"]
    base_version: VersionNumber
    match_confirmed: bool
    source_investigation_id: uuid.UUID | None = None


class DeeperReanalysis(StrictModel):
    """Search further for evidence on the same claims."""

    reason: Literal["deeper"]
    base_version: VersionNumber


# Routes add the discriminator through ``Body(discriminator="reason")``; FastAPI drops a
# ``Field`` discriminator when the parameter is also annotated with ``Body()``.
ReanalysisRequest = CorrectionReanalysis | ExpansionReanalysis | DeeperReanalysis


class ReanalysisResponse(BaseModel):
    """Receipt of an accepted reanalysis; replayed unchanged for the same Idempotency-Key."""

    id: uuid.UUID
    investigation_id: uuid.UUID
    reason: ReanalysisReason
    base_version: int
    # The version a correction published at once; null for expansion and deeper, whose
    # new version is published by the reanalysis job.
    published_version: int | None
    # The confirmed full-video investigation of an expansion; null otherwise.
    source_investigation_id: uuid.UUID | None
    job: JobSummary
    created_at: datetime


class ExportAssessment(BaseModel):
    overall: OverallAssessment
    summary: str
    provisional: bool


class ExportClaim(BaseModel):
    """A claim as exported: its normalized meaning only, never the transcript wording."""

    id: str
    proposition: str
    interval: Interval
    corrected: bool
    assessment: ExportAssessment | None


class ExportSource(BaseModel):
    evidence_id: str
    claim_id: str
    title: str
    publisher: str
    url: str | None
    published_at: datetime | None
    retrieved_at: datetime
    source_type: SourceType
    inspection_level: SourceInspectionLevel
    retraction_status: RetractionStatus
    relation: Relation | None


class ReportExport(BaseModel):
    """Allowlisted, shareable view of one report version (decision 0003 export contents).

    No media, transcript wording, evidence excerpts, assessment notes or identity is included.
    """

    format: Literal["ovrly.report-export"] = "ovrly.report-export"
    format_version: Literal[1] = 1
    investigation_id: uuid.UUID
    report_id: str
    version: int
    supersedes: str | None
    provisional: bool
    fixture: bool
    report_created_at: datetime
    retrieved_at: datetime
    change_summary: str
    claims: list[ExportClaim]
    sources: list[ExportSource]
    limitations: list[str]


# ---- End reports, saves and voice actions ------------------------------------------------
