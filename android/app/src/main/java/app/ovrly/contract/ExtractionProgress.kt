@file:UseSerializers(StrictIntSerializer::class, StrictLongSerializer::class)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable(with = ExtractionCoverageStatusSerializer::class)
internal enum class ExtractionCoverageStatus(val wireName: String) {
    PENDING("pending"),
    PROCESSED("processed"),
    SKIPPED("skipped"),
    FAILED("failed"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ExtractionCoverageStatus =
            entries.firstOrNull { it != UNKNOWN && it.wireName == name } ?: UNKNOWN
    }
}

internal object ExtractionCoverageStatusSerializer : WireEnumSerializer<ExtractionCoverageStatus>(
    "app.ovrly.contract.ExtractionCoverageStatus",
    { ExtractionCoverageStatus.fromWire(it) },
    ExtractionCoverageStatus::wireName
)

@Serializable
internal data class ObservationProgress(
    @SerialName("observation_id") val observationId: String,
    @SerialName("start_ms") val startMs: Long,
    @SerialName("end_ms") val endMs: Long,
    val timebase: Timebase,
    val status: ExtractionCoverageStatus,
    val reason: String?
) {
    init {
        require(startMs >= 0 && endMs > startMs) { "invalid observation interval" }
    }
}

@Serializable
internal data class ExtractionProgress(
    val closed: Boolean,
    @SerialName("requests_used") val requestsUsed: Int,
    @SerialName("tokens_reserved") val tokensReserved: Int,
    @SerialName("max_requests") val maxRequests: Int,
    @SerialName("max_tokens") val maxTokens: Int,
    @SerialName("reconciliation_requests") val reconciliationRequests: Int,
    @SerialName("reconciliation_tokens") val reconciliationTokens: Int,
    val observations: List<ObservationProgress>
) {
    init {
        require(requestsUsed >= 0 && tokensReserved >= 0) { "negative extraction reservation" }
        require(
            listOf(maxRequests, maxTokens, reconciliationRequests, reconciliationTokens).all {
                it > 0
            }
        ) { "invalid extraction budget" }
    }
}
