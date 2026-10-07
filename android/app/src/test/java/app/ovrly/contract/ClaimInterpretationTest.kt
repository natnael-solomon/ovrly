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
        val factual = valid.copy(
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
        val quoted = valid.copy(
            speakerCommitment = SpeakerCommitment.UNCOMMITTED,
            eligibilityReason = EligibilityReason.QUOTED_NOT_ENDORSED,
            uncertaintyFlags = listOf(ClaimUncertainty.UNKNOWN, ClaimUncertainty.UNKNOWN)
        )
        assertEquals(EligibilityReason.QUOTED_NOT_ENDORSED, quoted.eligibilityReason)
        val premise = valid.copy(eligibilityReason = EligibilityReason.FACTUAL_PREMISE)
        assertEquals(ClaimTaxonomy.EMPIRICAL, premise.taxonomy)
    }

    @Test
    fun directlyBuiltInvalidInterpretationsAreRejected() {
        listOf(
            { ClaimSourceRef(" ", 0, 1) },
            { ClaimSourceRef("speech-1", -1, 1) },
            { ClaimSourceRef("speech-1", 2, 2) },
            { valid.copy(sourceRefs = emptyList()) },
            { valid.copy(attributedTo = " ") },
            {
                valid.copy(
                    uncertaintyFlags = listOf(
                        ClaimUncertainty.MISSING_CONTEXT,
                        ClaimUncertainty.MISSING_CONTEXT
                    )
                )
            },
            { valid.copy(taxonomy = ClaimTaxonomy.NORMATIVE) },
            { valid.copy(assertionMode = AssertionMode.QUESTIONED) },
            {
                valid.copy(
                    eligibilityReason = EligibilityReason.FACTUAL_PREMISE,
                    assertionMode = AssertionMode.HYPOTHETICAL
                )
            },
            { valid.copy(eligibilityReason = EligibilityReason.QUOTED_NOT_ENDORSED) }
        ).forEach { build ->
            assertThrows(IllegalArgumentException::class.java) { build() }
        }
    }

    private val valid = ClaimInterpretation(
        taxonomy = ClaimTaxonomy.EMPIRICAL,
        sourceRefs = listOf(reference),
        contextRefs = listOf(reference),
        assertionMode = AssertionMode.ASSERTED,
        speakerCommitment = SpeakerCommitment.ENDORSED,
        attributedTo = null,
        eligibilityReason = EligibilityReason.FACTUAL_CLAIM,
        uncertaintyFlags = emptyList()
    )
}
