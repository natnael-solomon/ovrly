@file:UseSerializers(ContractBooleanSerializer::class)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable(with = ReconciliationStatusSerializer::class)
internal enum class ReconciliationStatus(val wireName: String) {
    WAITING("waiting"),
    CHECKING("checking"),
    COMPLETE("complete"),
    FAILED("failed"),
    CANCELLED("cancelled"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ReconciliationStatus =
            entries.firstOrNull { it != UNKNOWN && it.wireName == name } ?: UNKNOWN
    }
}

internal object ReconciliationStatusSerializer : WireEnumSerializer<ReconciliationStatus>(
    "app.ovrly.contract.ReconciliationStatus",
    { ReconciliationStatus.fromWire(it) },
    ReconciliationStatus::wireName
)

@Serializable
internal data class ReconciliationSummary(
    val status: ReconciliationStatus,
    @SerialName("coverage_limited") val coverageLimited: Boolean,
    @SerialName("reassessment_claim_ids") val reassessmentClaimIds: List<String>
) {
    init {
        require(status == ReconciliationStatus.COMPLETE || status == ReconciliationStatus.UNKNOWN)
        require(reassessmentClaimIds.distinct().size == reassessmentClaimIds.size)
        reassessmentClaimIds.forEach { ContractSyntax.opaqueId("reassessment_claim_id", it) }
    }
}

@Serializable
internal data class ReconciliationProgress(
    val status: ReconciliationStatus,
    val error: InvestigationError?
)
