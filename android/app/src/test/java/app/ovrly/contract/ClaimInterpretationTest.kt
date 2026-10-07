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
}
