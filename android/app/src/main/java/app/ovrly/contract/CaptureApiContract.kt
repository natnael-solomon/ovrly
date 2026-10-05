@file:UseSerializers(
    StrictLongSerializer::class,
    StrictIntSerializer::class,
    ContractBooleanSerializer::class
)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable
internal data class CaptureCreateRequest(
    @SerialName("chunk_duration_ms")
    val chunkDurationMs: Int = 10000
) {
    init {
        require(chunkDurationMs in 1000..30000) { "chunk_duration_ms must be 1000..30000" }
    }
}

@Serializable
internal data class CaptureCloseRequest(
    @SerialName("continue_research")
    val continueResearch: Boolean,
    @SerialName("duration_ms")
    val durationMs: Int? = null
) {
    init {
        require(durationMs == null || durationMs in 0..180000) {
            "duration_ms must be 0..180000"
        }
    }
}

@Serializable
internal data class CaptureMetadata(val chunk: CaptureChunkRequest, val modality: Modality) {
    init {
        require(modality != Modality.UNKNOWN) { "chunk modality must be known" }
    }
}

@Serializable
internal data class CaptureModalityCoverage(
    val speech: List<Interval>,
    val text: List<Interval>
) {
    init {
        (speech + text).forEach { require(it.timebase == Timebase.CAPTURE) }
    }
}

@Serializable
internal data class CaptureManifest(
    @SerialName("duration_ms")
    val durationMs: Int,
    @SerialName("missing_intervals")
    val missingIntervals: List<Interval>,
    @SerialName("declared_coverage")
    val declaredCoverage: CaptureModalityCoverage
) {
    init {
        require(durationMs in 0..180000)
        missingIntervals.forEach { require(it.timebase == Timebase.CAPTURE) }
    }
}

@Serializable
internal data class CaptureWork(
    val seq: Int,
    @SerialName("job_id")
    val jobId: String?,
    @SerialName("processing_status")
    val processingStatus: ProcessingStatus,
    val error: InvestigationError?
) {
    init {
        require(seq >= 0)
        jobId?.let { ContractSyntax.uuid("job_id", it) }
    }
}

@Serializable
internal data class CaptureClaimState(
    @SerialName("claim_id")
    val claimId: String,
    @SerialName("processing_status")
    val processingStatus: ProcessingStatus,
    val error: InvestigationError?
) {
    init {
        ContractSyntax.opaqueId("claim_id", claimId)
    }
}

@Serializable
internal data class CaptureStatus(
    val session: CaptureSession,
    @SerialName("continue_research")
    val continueResearch: Boolean?,
    @SerialName("expires_at")
    val expiresAt: String,
    val manifest: CaptureManifest,
    val work: List<CaptureWork>,
    val claims: List<CaptureClaimState>,
    @SerialName("claim_extraction_status")
    val claimExtractionStatus: String
) {
    init {
        ContractSyntax.timestamp("expires_at", expiresAt)
        require(claimExtractionStatus == "not_started")
    }
}
