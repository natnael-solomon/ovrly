package app.ovrly.contract

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
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
}
