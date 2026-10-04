@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.contract

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject

/*
 * Typed models for `investigation.schema.json`, `job.schema.json` and
 * `investigation-create-request.schema.json` (contract 0.1.0-draft). Progress, stored state,
 * the queue job, the error and the findings are separate fields and the constructor enforces
 * the schema's three `oneOf` branches, so a failure can never be read as a finding. Parse and
 * encode with [InvestigationCodec].
 */

/** `investigation.schema.json#/$defs/coverage`: how much media was checked. Never a verdict. */
@Serializable
internal data class Coverage(
    val status: CoverageStatus,
    @SerialName("covered_ms")
    val coveredMs: Long? = null,
    @SerialName("total_ms")
    val totalMs: Long? = null
)

/** `investigation.schema.json#/$defs/safe_error`: the stored subset of the shared error shape. */
@Serializable
internal data class InvestigationError(
    val code: String,
    val message: String,
    @Serializable(with = RetryableSerializer::class)
    val retryable: Boolean
) {
    init {
        ContractSyntax.errorCode("error.code", code)
        ContractSyntax.errorMessage("error.message", message)
    }
}

/**
 * The source union of a create request and of the read model, discriminated on `kind`. A
 * [Url] names a complete accessible video; an [Upload] names a completed upload. [Unknown]
 * is a kind this version does not define: a read model keeps it, a request rejects it and
 * it cannot be encoded.
 */
@Serializable(with = InvestigationSourceSerializer::class)
internal sealed class InvestigationSource {
    abstract val kind: SourceKind

    /** Declared media duration in milliseconds, or null when it was not declared. */
    abstract val durationMs: Long?

    @Serializable
    data class Url(
        val url: String,
        @SerialName("duration_ms")
        override val durationMs: Long? = null
    ) : InvestigationSource() {
        override val kind: SourceKind
            get() = SourceKind.URL

        init {
            ContractSyntax.httpUrl("source.url", url)
        }
    }

    @Serializable
    data class Upload(
        @SerialName("upload_id")
        val uploadId: String,
        @SerialName("duration_ms")
        override val durationMs: Long? = null
    ) : InvestigationSource() {
        override val kind: SourceKind
            get() = SourceKind.UPLOAD

        init {
            ContractSyntax.uuid("source.upload_id", uploadId)
        }
    }

    data class Unknown(val wireKind: String) : InvestigationSource() {
        override val kind: SourceKind
            get() = SourceKind.UNKNOWN

        override val durationMs: Long?
            get() = null
    }
}

/**
 * Reads `kind` first and decodes the branch it names with the calling `Json` configuration
 * (strict for requests, tolerant for read models), so the two structurally different branches
 * never blur into one object with optional fields.
 */
internal object InvestigationSourceSerializer : KSerializer<InvestigationSource> {
    private const val KIND = "kind"

    override val descriptor: SerialDescriptor =
        buildClassSerialDescriptor("app.ovrly.contract.InvestigationSource")

    override fun deserialize(decoder: Decoder): InvestigationSource {
        val json = requireNotNull(decoder as? JsonDecoder) { "source needs a JSON decoder" }
        val element = requireNotNull(json.decodeJsonElement() as? JsonObject) {
            "source must be an object"
        }
        val kind = requireNotNull(element[KIND]) { "source.kind is required" }
        val name = requireNotNull((kind as? JsonPrimitive)?.takeIf { it.isString }?.content) {
            "source.kind must be a string"
        }
        val fields = JsonObject(element.filterKeys { it != KIND })
        return when (SourceKind.fromWire(name)) {
            SourceKind.URL ->
                json.json.decodeFromJsonElement(InvestigationSource.Url.serializer(), fields)

            SourceKind.UPLOAD ->
                json.json.decodeFromJsonElement(InvestigationSource.Upload.serializer(), fields)

            SourceKind.UNKNOWN -> InvestigationSource.Unknown(name)
        }
    }

    override fun serialize(encoder: Encoder, value: InvestigationSource) {
        val json = requireNotNull(encoder as? JsonEncoder) { "source needs a JSON encoder" }
        val fields = when (value) {
            is InvestigationSource.Url ->
                json.json.encodeToJsonElement(InvestigationSource.Url.serializer(), value)

            is InvestigationSource.Upload ->
                json.json.encodeToJsonElement(InvestigationSource.Upload.serializer(), value)

            is InvestigationSource.Unknown ->
                throw IllegalArgumentException("source.kind UNKNOWN has no wire value")
        }.jsonObject
        val kind = JsonPrimitive(value.kind.wireName)
        json.encodeJsonElement(JsonObject(mapOf(KIND to kind) + fields))
    }
}

/** Request body for `POST /v1/investigations`: exactly one known source. */
@Serializable
internal data class InvestigationCreateRequest(val source: InvestigationSource) {
    init {
        require(source !is InvestigationSource.Unknown) { "source.kind must be url or upload" }
        source.durationMs?.let { ContractSyntax.positive("source.duration_ms", it) }
    }
}

/** Summary of one durable queue job; the target of the queue voice actions. */
@Serializable
internal data class Job(
    val id: String,
    val state: JobState,
    val stage: Stage,
    @SerialName("cancel_requested")
    val cancelRequested: Boolean,
    val attempts: Int,
    @SerialName("retry_class")
    val retryClass: RetryClass?,
    @SerialName("available_at")
    val availableAt: String,
    @SerialName("updated_at")
    val updatedAt: String
) {
    /** True only for `published`; a job in an undefined state is not published. */
    val isPublished: Boolean
        get() = state == JobState.PUBLISHED

    init {
        ContractSyntax.uuid("job.id", id)
        ContractSyntax.timestamp("job.available_at", availableAt)
        ContractSyntax.timestamp("job.updated_at", updatedAt)
    }
}

/**
 * Read model of one investigation. The constructor enforces the schema's `oneOf` branches,
 * tolerating [ProcessingStatus.UNKNOWN] and [InvestigationState.UNKNOWN] in each so a newer
 * server's value keeps the payload readable without ever making it complete:
 *
 * - nothing published, nothing failed: `report` and `error` are both null;
 * - a report version is published: `error` is null and the report echoes `id` and `version`;
 * - failed: `error` is present and `report` is null.
 *
 * A complete investigation has a non-provisional report that assesses every claim.
 */
@Serializable
internal data class Investigation(
    val id: String,
    val state: InvestigationState,
    val stage: Stage,
    val coverage: Coverage,
    val version: Int,
    val error: InvestigationError?,
    val source: InvestigationSource,
    @SerialName("created_at")
    val createdAt: String,
    @SerialName("updated_at")
    val updatedAt: String,
    @SerialName("processing_status")
    val processingStatus: ProcessingStatus,
    val job: Job?,
    val report: ReportVersion?
) {
    /** True only for `complete`; [ProcessingStatus.UNKNOWN] is never complete. */
    val isComplete: Boolean
        get() = processingStatus == ProcessingStatus.COMPLETE

    /** True only for `failed`, the one status that carries an error and never a finding. */
    val isFailed: Boolean
        get() = processingStatus == ProcessingStatus.FAILED

    init {
        ContractSyntax.uuid("id", id)
        ContractSyntax.timestamp("created_at", createdAt)
        ContractSyntax.timestamp("updated_at", updatedAt)
        requireOneBranch()
        if (processingStatus == ProcessingStatus.COMPLETE) {
            require(report != null && !report.provisional) {
                "a complete investigation has a report that is not provisional"
            }
            require(report.assessesEveryClaim) { "a complete report assesses every claim" }
        }
    }

    private fun requireOneBranch() {
        when {
            error != null -> {
                require(report == null) { "a failed investigation cannot carry a report" }
                require(processingStatus in FAILED_STATUSES && state in FAILED_STATES) {
                    "error is only present when processing_status and state are failed"
                }
            }

            report != null -> {
                require(processingStatus in PUBLISHED_STATUSES && state in PUBLISHED_STATES) {
                    "report is only present for partial, complete or cancelled"
                }
                require(report.investigationId == id) { "report.investigation_id must echo id" }
                require(report.version == version) { "report.version must equal version" }
            }

            else -> require(processingStatus in PENDING_STATUSES && state in PENDING_STATES) {
                "waiting, checking and cancelled carry neither report nor error"
            }
        }
    }

    companion object {
        private val FAILED_STATUSES = setOf(ProcessingStatus.FAILED, ProcessingStatus.UNKNOWN)
        private val FAILED_STATES = setOf(InvestigationState.FAILED, InvestigationState.UNKNOWN)
        private val PUBLISHED_STATUSES = setOf(
            ProcessingStatus.PARTIAL,
            ProcessingStatus.COMPLETE,
            ProcessingStatus.CANCELLED,
            ProcessingStatus.UNKNOWN
        )
        private val PUBLISHED_STATES = setOf(
            InvestigationState.RUNNING,
            InvestigationState.COMPLETED,
            InvestigationState.CANCELLED,
            InvestigationState.UNKNOWN
        )
        private val PENDING_STATUSES = setOf(
            ProcessingStatus.WAITING,
            ProcessingStatus.CHECKING,
            ProcessingStatus.CANCELLED,
            ProcessingStatus.UNKNOWN
        )
        private val PENDING_STATES = setOf(
            InvestigationState.QUEUED,
            InvestigationState.RUNNING,
            InvestigationState.CANCELLED,
            InvestigationState.UNKNOWN
        )
    }
}
