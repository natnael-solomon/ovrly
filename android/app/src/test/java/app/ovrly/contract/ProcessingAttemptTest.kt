package app.ovrly.contract

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ProcessingAttemptTest {
    private val input = """
        {
          "provider":"scholarxiv","model":"synthetic-free","decision_id":null,
          "task":"claim_extraction","outcome":"invalid","prompt_tokens":null,
          "completion_tokens":null,"total_tokens":100,"thinking_leaked":true,
          "fenced":false,"repair":false,"feedback":"feedback_unknown"
        }
    """.trimIndent()

    @Test
    fun provenancePreservesUnknownFeedbackAndNullableIdentity() {
        val value = Json.decodeFromString<ProcessingAttempt>(input)
        assertEquals("invalid", value.outcome)
        assertEquals("feedback_unknown", value.feedback)
        assertNull(value.decisionId)
        assertEquals(100, value.totalTokens)
        assertEquals(value, Json.decodeFromString<ProcessingAttempt>(Json.encodeToString(value)))
    }

    @Test
    fun invalidTokenCountersAreRejected() {
        listOf("-1", "2147483648", "1.5", "true", "\"100\"").forEach { invalid ->
            assertThrows(IllegalArgumentException::class.java) {
                Json.decodeFromString<ProcessingAttempt>(
                    input.replace("\"total_tokens\":100", "\"total_tokens\":$invalid")
                )
            }
        }
    }

    @Test
    fun directlyBuiltProvenanceKeepsEveryFieldAndRejectsInvalidValues() {
        val value = attempt()
        assertEquals("scholarxiv", value.provider)
        assertEquals("synthetic-free", value.model)
        assertEquals("decision-1", value.decisionId)
        assertEquals("claim_extraction", value.task)
        assertEquals("valid", value.outcome)
        assertEquals(10, value.promptTokens)
        assertEquals(5, value.completionTokens)
        assertEquals(15, value.totalTokens)
        assertFalse(value.thinkingLeaked)
        assertTrue(value.fenced)
        assertTrue(value.repair)
        assertNull(value.feedback)
        assertEquals(value, Json.decodeFromString<ProcessingAttempt>(Json.encodeToString(value)))
        listOf(
            { attempt(provider = " ") },
            { attempt(task = "") },
            { attempt(outcome = " ") },
            { attempt(promptTokens = -1) },
            { attempt(completionTokens = -1) },
            { attempt(totalTokens = -1) }
        ).forEach { build ->
            assertThrows(IllegalArgumentException::class.java) { build() }
        }
    }

    private fun attempt(
        provider: String = "scholarxiv",
        task: String = "claim_extraction",
        outcome: String = "valid",
        promptTokens: Int? = 10,
        completionTokens: Int? = 5,
        totalTokens: Int? = 15
    ) = ProcessingAttempt(
        provider = provider,
        model = "synthetic-free",
        decisionId = "decision-1",
        task = task,
        outcome = outcome,
        promptTokens = promptTokens,
        completionTokens = completionTokens,
        totalTokens = totalTokens,
        thinkingLeaked = false,
        fenced = true,
        repair = true,
        feedback = null
    )
}
