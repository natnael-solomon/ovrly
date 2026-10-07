package app.ovrly.contract

import kotlinx.serialization.Serializable

@Serializable(with = ClaimTaxonomySerializer::class)
internal enum class ClaimTaxonomy(val wireName: String) {
    EMPIRICAL("empirical"),
    CAUSAL("causal"),
    DOCUMENTARY("documentary"),
    PREDICTIVE("predictive"),
    NORMATIVE("normative"),
    MIXED("mixed"),
    UNCLEAR("unclear"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ClaimTaxonomy = known(entries, name) { it.wireName }
    }
}

@Serializable(with = AssertionModeSerializer::class)
internal enum class AssertionMode(val wireName: String) {
    ASSERTED("asserted"),
    REPORTED("reported"),
    QUESTIONED("questioned"),
    HYPOTHETICAL("hypothetical"),
    COUNTERFACTUAL("counterfactual"),
    UNCLEAR("unclear"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): AssertionMode = known(entries, name) { it.wireName }
    }
}

@Serializable(with = SpeakerCommitmentSerializer::class)
internal enum class SpeakerCommitment(val wireName: String) {
    ENDORSED("endorsed"),
    REJECTED("rejected"),
    UNCOMMITTED("uncommitted"),
    UNCLEAR("unclear"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SpeakerCommitment = known(entries, name) { it.wireName }
    }
}

@Serializable(with = EligibilityReasonSerializer::class)
internal enum class EligibilityReason(val wireName: String) {
    FACTUAL_CLAIM("factual-claim"),
    FACTUAL_PREMISE("factual-premise"),
    OPINION("opinion"),
    QUOTED_NOT_ENDORSED("quoted-not-endorsed"),
    INSUFFICIENT_CONTEXT("insufficient-context"),
    NOT_A_CLAIM("not-a-claim"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): EligibilityReason = known(entries, name) { it.wireName }
    }
}

@Serializable(with = ClaimUncertaintySerializer::class)
internal enum class ClaimUncertainty(val wireName: String) {
    UNRESOLVED_REFERENCE("unresolved-reference"),
    MISSING_CONTEXT("missing-context"),
    AMBIGUOUS_ATTRIBUTION("ambiguous-attribution"),
    AMBIGUOUS_COMMITMENT("ambiguous-commitment"),
    AMBIGUOUS_MEANING("ambiguous-meaning"),
    SOURCE_TEXT_CONFLICT("source-text-conflict"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ClaimUncertainty = known(entries, name) { it.wireName }
    }
}

internal object ClaimTaxonomySerializer : WireEnumSerializer<ClaimTaxonomy>(
    "app.ovrly.contract.ClaimTaxonomy",
    { ClaimTaxonomy.fromWire(it) },
    ClaimTaxonomy::wireName
)

internal object AssertionModeSerializer : WireEnumSerializer<AssertionMode>(
    "app.ovrly.contract.AssertionMode",
    { AssertionMode.fromWire(it) },
    AssertionMode::wireName
)

internal object SpeakerCommitmentSerializer : WireEnumSerializer<SpeakerCommitment>(
    "app.ovrly.contract.SpeakerCommitment",
    { SpeakerCommitment.fromWire(it) },
    SpeakerCommitment::wireName
)

internal object EligibilityReasonSerializer : WireEnumSerializer<EligibilityReason>(
    "app.ovrly.contract.EligibilityReason",
    { EligibilityReason.fromWire(it) },
    EligibilityReason::wireName
)

internal object ClaimUncertaintySerializer : WireEnumSerializer<ClaimUncertainty>(
    "app.ovrly.contract.ClaimUncertainty",
    { ClaimUncertainty.fromWire(it) },
    ClaimUncertainty::wireName
)

/*
 * One Kotlin enum per `$def` in `packages/contracts/schemas/enums.schema.json` (contract
 * 0.2.0-draft). Each lists the schema's values in schema order and ends with UNKNOWN, the
 * fallback [known] returns for a value this version does not define. UNKNOWN has no wire
 * name, cannot be encoded and is never a success, stored, completed or finding state. The
 * `ContractEnumsTest` cross-check fails the build when a list and its `$def` differ.
 */

/** `job_state`: persisted queue job state (backend `JobState`). */
@Serializable(with = JobStateSerializer::class)
internal enum class JobState(val wireName: String) {
    QUEUED("queued"),
    LEASED("leased"),
    RUNNING("running"),
    PUBLISHED("published"),
    CANCELLED("cancelled"),
    DELETED("deleted"),
    FAILED("failed"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): JobState = known(entries, name) { it.wireName }
    }
}

/** `retry_class`: class of the last scheduled retry or of the terminal failure. */
@Serializable(with = RetryClassSerializer::class)
internal enum class RetryClass(val wireName: String) {
    TRANSIENT("transient"),
    RATE_LIMITED("rate_limited"),
    NON_RETRIABLE_INPUT("non_retriable_input"),
    INVALID_MODEL_SCHEMA("invalid_model_schema"),
    UNKNOWN_OUTCOME("unknown_outcome"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): RetryClass = known(entries, name) { it.wireName }
    }
}

/** `investigation_state`: coarse stored state of an investigation, distinct from [JobState]. */
@Serializable(with = InvestigationStateSerializer::class)
internal enum class InvestigationState(val wireName: String) {
    QUEUED("queued"),
    RUNNING("running"),
    COMPLETED("completed"),
    FAILED("failed"),
    CANCELLED("cancelled"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): InvestigationState = known(entries, name) { it.wireName }
    }
}

/** `stage`: pipeline stage an investigation or job is at. */
@Serializable(with = StageSerializer::class)
internal enum class Stage(val wireName: String) {
    INTAKE("intake"),
    MEDIA_VALIDATION("media_validation"),
    ASR("asr"),
    DEVICE_TEXT("device_text"),
    CLAIM_EXTRACTION("claim_extraction"),
    RETRIEVAL("retrieval"),
    ASSESSMENT("assessment"),
    RECONCILIATION("reconciliation"),
    PUBLICATION("publication"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): Stage = known(entries, name) { it.wireName }
    }
}

/** `processing_status`: user-facing progress. Never a finding; [UNKNOWN] is never complete. */
@Serializable(with = ProcessingStatusSerializer::class)
internal enum class ProcessingStatus(val wireName: String) {
    WAITING("waiting"),
    CHECKING("checking"),
    PARTIAL("partial"),
    COMPLETE("complete"),
    FAILED("failed"),
    CANCELLED("cancelled"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ProcessingStatus = known(entries, name) { it.wireName }
    }
}

/** `coverage_status`: how much of the eligible media has been checked. Never a verdict. */
@Serializable(with = CoverageStatusSerializer::class)
internal enum class CoverageStatus(val wireName: String) {
    NOT_STARTED("not_started"),
    PARTIAL("partial"),
    COMPLETE("complete"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): CoverageStatus = known(entries, name) { it.wireName }
    }
}

/** `upload_state`: upload lifecycle. [UNKNOWN] is never completed. */
@Serializable(with = UploadStateSerializer::class)
internal enum class UploadState(val wireName: String) {
    PENDING("pending"),
    COMPLETED("completed"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): UploadState = known(entries, name) { it.wireName }
    }
}

/** `source_kind`: discriminator of the investigation source union. */
@Serializable(with = SourceKindSerializer::class)
internal enum class SourceKind(val wireName: String) {
    URL("url"),
    UPLOAD("upload"),
    CAPTURE("capture"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SourceKind = known(entries, name) { it.wireName }
    }
}

/** `timebase`: what millisecond offsets count from (live capture start or media start). */
@Serializable(with = TimebaseSerializer::class)
internal enum class Timebase(val wireName: String) {
    CAPTURE("capture"),
    MEDIA("media"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): Timebase = known(entries, name) { it.wireName }
    }
}

/** `modality`: where a claim occurrence was observed. */
@Serializable(with = ModalitySerializer::class)
internal enum class Modality(val wireName: String) {
    SPEECH("speech"),
    TEXT("text"),
    BOTH("both"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): Modality = known(entries, name) { it.wireName }
    }
}

/** `relation`: how one piece of evidence relates to one claim. */
@Serializable(with = RelationSerializer::class)
internal enum class Relation(val wireName: String) {
    SUPPORT("support"),
    CHALLENGE("challenge"),
    QUALIFY("qualify"),
    MIXED("mixed"),
    INSUFFICIENT("insufficient"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): Relation = known(entries, name) { it.wireName }
    }
}

/** `overall_assessment`: per-claim aggregate over its evidence. No report-level verdict exists. */
@Serializable(with = OverallAssessmentSerializer::class)
internal enum class OverallAssessment(val wireName: String) {
    SUPPORTED("supported"),
    CHALLENGED("challenged"),
    QUALIFIED("qualified"),
    MIXED("mixed"),
    INSUFFICIENT_EVIDENCE("insufficient_evidence"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): OverallAssessment = known(entries, name) { it.wireName }
    }
}

/**
 * `source_inspection_level`: how much of a source the pipeline read. [UNDETERMINED] is the
 * contract's own `unknown` value (the level was not recorded); [UNKNOWN] is the fallback for
 * a value this version does not define.
 */
@Serializable(with = SourceInspectionLevelSerializer::class)
internal enum class SourceInspectionLevel(val wireName: String) {
    ABSTRACT_ONLY("abstract_only"),
    FULL_TEXT("full_text"),
    METADATA_ONLY("metadata_only"),
    UNDETERMINED("unknown"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SourceInspectionLevel = known(entries, name) { it.wireName }
    }
}

/** `source_type`: kind of publication the evidence comes from. */
@Serializable(with = SourceTypeSerializer::class)
internal enum class SourceType(val wireName: String) {
    PEER_REVIEWED("peer_reviewed"),
    PREPRINT("preprint"),
    GOVERNMENT("government"),
    NEWS("news"),
    REFERENCE_WORK("reference_work"),
    PRIMARY_DOCUMENT("primary_document"),
    ORGANIZATION("organization"),
    OTHER("other"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SourceType = known(entries, name) { it.wireName }
    }
}

/** `retrieval_relevance`: retrieval's relevance judgement, independent of [Relation]. */
@Serializable(with = RetrievalRelevanceSerializer::class)
internal enum class RetrievalRelevance(val wireName: String) {
    HIGH("high"),
    MEDIUM("medium"),
    LOW("low"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): RetrievalRelevance = known(entries, name) { it.wireName }
    }
}

/**
 * `retraction_status`: whether the source itself was corrected, retracted or withdrawn.
 * [UNDETERMINED] is the contract's own `unknown` value; [UNKNOWN] is the fallback.
 */
@Serializable(with = RetractionStatusSerializer::class)
internal enum class RetractionStatus(val wireName: String) {
    NONE("none"),
    CORRECTED("corrected"),
    RETRACTED("retracted"),
    WITHDRAWN("withdrawn"),
    UNDETERMINED("unknown"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): RetractionStatus = known(entries, name) { it.wireName }
    }
}

/** `correction_attribution`: who changed a claim's normalized meaning after extraction. */
@Serializable(with = CorrectionAttributionSerializer::class)
internal enum class CorrectionAttribution(val wireName: String) {
    USER("user"),
    PIPELINE("pipeline"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): CorrectionAttribution = known(entries, name) { it.wireName }
    }
}

/** `capture_session_state`: lifecycle of a live capture session. */
@Serializable(with = CaptureSessionStateSerializer::class)
internal enum class CaptureSessionState(val wireName: String) {
    OPEN("open"),
    CLOSED("closed"),
    ABANDONED("abandoned"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): CaptureSessionState = known(entries, name) { it.wireName }
    }
}

/** `chunk_disposition`: what the server did with a chunk. [UNKNOWN] is never stored. */
@Serializable(with = ChunkDispositionSerializer::class)
internal enum class ChunkDisposition(val wireName: String) {
    STORED("stored"),
    DUPLICATE("duplicate"),
    OUT_OF_ORDER("out_of_order"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ChunkDisposition = known(entries, name) { it.wireName }
    }
}

internal object JobStateSerializer : WireEnumSerializer<JobState>(
    "app.ovrly.contract.JobState",
    { JobState.fromWire(it) },
    JobState::wireName
)

internal object RetryClassSerializer : WireEnumSerializer<RetryClass>(
    "app.ovrly.contract.RetryClass",
    { RetryClass.fromWire(it) },
    RetryClass::wireName
)

internal object InvestigationStateSerializer : WireEnumSerializer<InvestigationState>(
    "app.ovrly.contract.InvestigationState",
    { InvestigationState.fromWire(it) },
    InvestigationState::wireName
)

internal object StageSerializer : WireEnumSerializer<Stage>(
    "app.ovrly.contract.Stage",
    { Stage.fromWire(it) },
    Stage::wireName
)

internal object ProcessingStatusSerializer : WireEnumSerializer<ProcessingStatus>(
    "app.ovrly.contract.ProcessingStatus",
    { ProcessingStatus.fromWire(it) },
    ProcessingStatus::wireName
)

internal object CoverageStatusSerializer : WireEnumSerializer<CoverageStatus>(
    "app.ovrly.contract.CoverageStatus",
    { CoverageStatus.fromWire(it) },
    CoverageStatus::wireName
)

internal object UploadStateSerializer : WireEnumSerializer<UploadState>(
    "app.ovrly.contract.UploadState",
    { UploadState.fromWire(it) },
    UploadState::wireName
)

internal object SourceKindSerializer : WireEnumSerializer<SourceKind>(
    "app.ovrly.contract.SourceKind",
    { SourceKind.fromWire(it) },
    SourceKind::wireName
)

internal object TimebaseSerializer : WireEnumSerializer<Timebase>(
    "app.ovrly.contract.Timebase",
    { Timebase.fromWire(it) },
    Timebase::wireName
)

internal object ModalitySerializer : WireEnumSerializer<Modality>(
    "app.ovrly.contract.Modality",
    { Modality.fromWire(it) },
    Modality::wireName
)

internal object RelationSerializer : WireEnumSerializer<Relation>(
    "app.ovrly.contract.Relation",
    { Relation.fromWire(it) },
    Relation::wireName
)

internal object OverallAssessmentSerializer : WireEnumSerializer<OverallAssessment>(
    "app.ovrly.contract.OverallAssessment",
    { OverallAssessment.fromWire(it) },
    OverallAssessment::wireName
)

internal object SourceInspectionLevelSerializer : WireEnumSerializer<SourceInspectionLevel>(
    "app.ovrly.contract.SourceInspectionLevel",
    { SourceInspectionLevel.fromWire(it) },
    SourceInspectionLevel::wireName
)

internal object SourceTypeSerializer : WireEnumSerializer<SourceType>(
    "app.ovrly.contract.SourceType",
    { SourceType.fromWire(it) },
    SourceType::wireName
)

internal object RetrievalRelevanceSerializer : WireEnumSerializer<RetrievalRelevance>(
    "app.ovrly.contract.RetrievalRelevance",
    { RetrievalRelevance.fromWire(it) },
    RetrievalRelevance::wireName
)

internal object RetractionStatusSerializer : WireEnumSerializer<RetractionStatus>(
    "app.ovrly.contract.RetractionStatus",
    { RetractionStatus.fromWire(it) },
    RetractionStatus::wireName
)

internal object CorrectionAttributionSerializer : WireEnumSerializer<CorrectionAttribution>(
    "app.ovrly.contract.CorrectionAttribution",
    { CorrectionAttribution.fromWire(it) },
    CorrectionAttribution::wireName
)

internal object CaptureSessionStateSerializer : WireEnumSerializer<CaptureSessionState>(
    "app.ovrly.contract.CaptureSessionState",
    { CaptureSessionState.fromWire(it) },
    CaptureSessionState::wireName
)

internal object ChunkDispositionSerializer : WireEnumSerializer<ChunkDisposition>(
    "app.ovrly.contract.ChunkDisposition",
    { ChunkDisposition.fromWire(it) },
    ChunkDisposition::wireName
)

@Serializable(with = SpeechStatusSerializer::class)
internal enum class SpeechStatus(val wireName: String) {
    PENDING("pending"),
    RUNNING("running"),
    COMPLETED("completed"),
    UNAVAILABLE("unavailable"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SpeechStatus = known(entries, name) { it.wireName }
    }
}

@Serializable(with = SpeechReasonSerializer::class)
internal enum class SpeechReason(val wireName: String) {
    DISABLED("disabled"),
    NO_AUDIO_TRACK("no_audio_track"),
    QUOTA_EXHAUSTED("quota_exhausted"),
    UNKNOWN_OUTCOME("unknown_outcome"),
    PROVIDER_UNAVAILABLE("provider_unavailable"),
    CANCELLED("cancelled"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SpeechReason = known(entries, name) { it.wireName }
    }
}

@Serializable(with = ASRProviderSerializer::class)
internal enum class ASRProvider(val wireName: String) {
    GROQ("groq"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ASRProvider = known(entries, name) { it.wireName }
    }
}

@Serializable(with = MediaSpeechStatusSerializer::class)
internal enum class MediaSpeechStatus(val wireName: String) {
    PENDING("pending"),
    UNAVAILABLE("unavailable"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): MediaSpeechStatus = known(entries, name) { it.wireName }
    }
}

@Serializable(with = MediaTextStatusSerializer::class)
internal enum class MediaTextStatus(val wireName: String) {
    PENDING("pending"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): MediaTextStatus = known(entries, name) { it.wireName }
    }
}

@Serializable(with = SpeechUnavailableReasonSerializer::class)
internal enum class SpeechUnavailableReason(val wireName: String) {
    NO_AUDIO_TRACK("no_audio_track"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): SpeechUnavailableReason = known(entries, name) { it.wireName }
    }
}

internal object SpeechStatusSerializer : WireEnumSerializer<SpeechStatus>(
    "app.ovrly.contract.SpeechStatus",
    { SpeechStatus.fromWire(it) },
    SpeechStatus::wireName
)

internal object SpeechReasonSerializer : WireEnumSerializer<SpeechReason>(
    "app.ovrly.contract.SpeechReason",
    { SpeechReason.fromWire(it) },
    SpeechReason::wireName
)

internal object ASRProviderSerializer : WireEnumSerializer<ASRProvider>(
    "app.ovrly.contract.ASRProvider",
    { ASRProvider.fromWire(it) },
    ASRProvider::wireName
)

internal object MediaSpeechStatusSerializer : WireEnumSerializer<MediaSpeechStatus>(
    "app.ovrly.contract.MediaSpeechStatus",
    { MediaSpeechStatus.fromWire(it) },
    MediaSpeechStatus::wireName
)

internal object MediaTextStatusSerializer : WireEnumSerializer<MediaTextStatus>(
    "app.ovrly.contract.MediaTextStatus",
    {
        MediaTextStatus.fromWire(it)
    },
    MediaTextStatus::wireName
)

internal object SpeechUnavailableReasonSerializer : WireEnumSerializer<SpeechUnavailableReason>(
    "app.ovrly.contract.SpeechUnavailableReason",
    { SpeechUnavailableReason.fromWire(it) },
    SpeechUnavailableReason::wireName
)

@Serializable(with = AnalysisStatusSerializer::class)
internal enum class AnalysisStatus(val wireName: String) {
    PENDING("pending"),
    PARTIAL("partial"),
    NO_USABLE("no_usable"),
    COMPLETE("complete"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): AnalysisStatus = known(entries, name) { it.wireName }
    }
}

internal object AnalysisStatusSerializer : WireEnumSerializer<AnalysisStatus>(
    "app.ovrly.contract.AnalysisStatus",
    { AnalysisStatus.fromWire(it) },
    AnalysisStatus::wireName
)
