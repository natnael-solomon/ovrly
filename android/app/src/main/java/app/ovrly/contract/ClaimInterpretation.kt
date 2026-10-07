@file:UseSerializers(StrictIntSerializer::class)

package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.UseSerializers

@Serializable
internal data class ClaimSourceRef(
    @SerialName("observation_id") val observationId: String,
    @SerialName("start_char") val startChar: Int,
    @SerialName("end_char") val endChar: Int
) {
    init {
        require(observationId.isNotBlank()) { "observation_id must contain text" }
        require(startChar >= 0 && startChar < endChar) { "source span must be ordered" }
    }
}

@Serializable
internal data class ClaimInterpretation(
    val taxonomy: ClaimTaxonomy,
    @SerialName("source_refs") val sourceRefs: List<ClaimSourceRef>,
    @SerialName("context_refs") val contextRefs: List<ClaimSourceRef>,
    @SerialName("assertion_mode") val assertionMode: AssertionMode,
    @SerialName("speaker_commitment") val speakerCommitment: SpeakerCommitment,
    @SerialName("attributed_to") val attributedTo: String?,
    @SerialName("eligibility_reason") val eligibilityReason: EligibilityReason,
    @SerialName("uncertainty_flags") val uncertaintyFlags: List<ClaimUncertainty>
) {
    init {
        require(sourceRefs.isNotEmpty()) { "an interpretation needs source references" }
        require(attributedTo == null || attributedTo.isNotBlank()) { "attribution is empty" }
        val knownFlags = uncertaintyFlags.filter { it != ClaimUncertainty.UNKNOWN }
        require(knownFlags.distinct().size == knownFlags.size) { "duplicate uncertainty flags" }
        val factual = eligibilityReason == EligibilityReason.FACTUAL_CLAIM ||
            eligibilityReason == EligibilityReason.FACTUAL_PREMISE
        require(!factual || taxonomy != ClaimTaxonomy.NORMATIVE) {
            "normative content cannot be empirically eligible"
        }
        require(
            !factual ||
                assertionMode !in setOf(AssertionMode.QUESTIONED, AssertionMode.HYPOTHETICAL)
        ) {
            "questions and hypothetical scenarios cannot be empirically eligible"
        }
        require(
            eligibilityReason != EligibilityReason.QUOTED_NOT_ENDORSED ||
                speakerCommitment != SpeakerCommitment.ENDORSED
        ) { "an endorsed statement cannot be excluded as not endorsed" }
    }
}
