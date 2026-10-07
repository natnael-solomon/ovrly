package app.ovrly.contract

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ClaimInterpretationTest {
    private val input = """
        {
          "taxonomy":"normative",
          "source_refs":[{"observation_id":"speech-1","start_char":0,"end_char":10}],
          "context_refs":[],
          "assertion_mode":"asserted",
          "speaker_commitment":"endorsed",
          "attributed_to":null,
          "eligibility_reason":"opinion",
          "uncertainty_flags":[]
        }
    """.trimIndent()

    private val reference = ClaimSourceRef("speech-1", 0, 10)

    @Test
    fun normativeInterpretationRemainsAnOpinion() {
        val value = Json.decodeFromString<ClaimInterpretation>(input)
        assertEquals(ClaimTaxonomy.NORMATIVE, value.taxonomy)
        assertEquals(EligibilityReason.OPINION, value.eligibilityReason)
        assertEquals("speech-1", value.sourceRefs.single().observationId)
    }

    @Test
    fun futureEligibilityIsUnknownNotFactual() {
        val value = Json.decodeFromString<ClaimInterpretation>(
            input.replace("opinion", "future-value")
        )
        assertEquals(EligibilityReason.UNKNOWN, value.eligibilityReason)
    }

    @Test
    fun normativeFactualEligibilityAndInvalidSpansAreRejected() {
        assertThrows(IllegalArgumentException::class.java) {
            Json.decodeFromString<ClaimInterpretation>(input.replace("opinion", "factual-claim"))
        }
        assertThrows(IllegalArgumentException::class.java) {
            Json.decodeFromString<ClaimInterpretation>(
                input.replace("\"end_char\":10", "\"end_char\":0")
            )
        }
    }

    @Test
    fun directlyBuiltInterpretationsRoundTripThroughTheWireShape() {
        val factual = interpretation(
            attributedTo = "Synthetic speaker",
            uncertaintyFlags = listOf(ClaimUncertainty.MISSING_CONTEXT)
        )
        assertEquals("Synthetic speaker", factual.attributedTo)
        assertEquals(0, factual.sourceRefs.single().startChar)
        assertEquals(10, factual.sourceRefs.single().endChar)
        assertEquals(listOf(reference), factual.contextRefs)
        assertEquals(AssertionMode.ASSERTED, factual.assertionMode)
        assertEquals(SpeakerCommitment.ENDORSED, factual.speakerCommitment)
        assertEquals(listOf(ClaimUncertainty.MISSING_CONTEXT), factual.uncertaintyFlags)
        assertEquals(
            factual,
            Json.decodeFromString<ClaimInterpretation>(Json.encodeToString(factual))
        )
        val quoted = interpretation(
            speakerCommitment = SpeakerCommitment.UNCOMMITTED,
            eligibilityReason = EligibilityReason.QUOTED_NOT_ENDORSED,
            uncertaintyFlags = listOf(ClaimUncertainty.UNKNOWN, ClaimUncertainty.UNKNOWN)
        )
        assertEquals(EligibilityReason.QUOTED_NOT_ENDORSED, quoted.eligibilityReason)
        val premise = interpretation(eligibilityReason = EligibilityReason.FACTUAL_PREMISE)
        assertEquals(ClaimTaxonomy.EMPIRICAL, premise.taxonomy)
    }

    @Test
    fun directlyBuiltInvalidInterpretationsAreRejected() {
        listOf(
            { ClaimSourceRef(" ", 0, 1) },
            { ClaimSourceRef("speech-1", -1, 1) },
            { ClaimSourceRef("speech-1", 2, 2) },
            { interpretation(sourceRefs = emptyList()) },
            { interpretation(attributedTo = " ") },
            {
                interpretation(
                    uncertaintyFlags = listOf(
                        ClaimUncertainty.MISSING_CONTEXT,
                        ClaimUncertainty.MISSING_CONTEXT
                    )
                )
            },
            { interpretation(taxonomy = ClaimTaxonomy.NORMATIVE) },
            { interpretation(assertionMode = AssertionMode.QUESTIONED) },
            {
                interpretation(
                    eligibilityReason = EligibilityReason.FACTUAL_PREMISE,
                    assertionMode = AssertionMode.HYPOTHETICAL
                )
            },
            { interpretation(eligibilityReason = EligibilityReason.QUOTED_NOT_ENDORSED) }
        ).forEach { build ->
            assertThrows(IllegalArgumentException::class.java) { build() }
        }
    }

    private fun interpretation(
        taxonomy: ClaimTaxonomy = ClaimTaxonomy.EMPIRICAL,
        sourceRefs: List<ClaimSourceRef> = listOf(reference),
        assertionMode: AssertionMode = AssertionMode.ASSERTED,
        speakerCommitment: SpeakerCommitment = SpeakerCommitment.ENDORSED,
        attributedTo: String? = null,
        eligibilityReason: EligibilityReason = EligibilityReason.FACTUAL_CLAIM,
        uncertaintyFlags: List<ClaimUncertainty> = emptyList()
    ) = ClaimInterpretation(
        taxonomy = taxonomy,
        sourceRefs = sourceRefs,
        contextRefs = listOf(reference),
        assertionMode = assertionMode,
        speakerCommitment = speakerCommitment,
        attributedTo = attributedTo,
        eligibilityReason = eligibilityReason,
        uncertaintyFlags = uncertaintyFlags
    )
}
