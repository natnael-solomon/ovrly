package app.ovrly.contract

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ExtractionProgressTest {
    private val input = """
        {
          "closed":true,"requests_used":1,"tokens_reserved":5954,
          "max_requests":2,"max_tokens":200000,
          "reconciliation_requests":1,"reconciliation_tokens":20000,
          "observations":[{
            "observation_id":"synthetic-speech-1","start_ms":0,"end_ms":1000,
            "timebase":"capture","status":"skipped","reason":"budget_exhausted"
          }]
        }
    """.trimIndent()

    @Test
    fun skippedCoverageRemainsDistinctFromProcessedCoverage() {
        val value = Json.decodeFromString<ExtractionProgress>(input)
        assertEquals(ExtractionCoverageStatus.SKIPPED, value.observations.single().status)
        assertEquals("budget_exhausted", value.observations.single().reason)
        assertEquals(5954, value.tokensReserved)
        assertEquals(value, Json.decodeFromString<ExtractionProgress>(Json.encodeToString(value)))
    }

    @Test
    fun futureCoverageDoesNotBecomeAProcessedFinding() {
        val value = Json.decodeFromString<ExtractionProgress>(input.replace("skipped", "future"))
        assertEquals(ExtractionCoverageStatus.UNKNOWN, value.observations.single().status)
    }

    @Test
    fun countersAndIntervalsUseStrictNumbers() {
        listOf("-1", "2147483648", "1.5", "true", "\"100\"").forEach { invalid ->
            assertThrows(IllegalArgumentException::class.java) {
                Json.decodeFromString<ExtractionProgress>(
                    input.replace("\"tokens_reserved\":5954", "\"tokens_reserved\":$invalid")
                )
            }
        }
        assertThrows(IllegalArgumentException::class.java) {
            Json.decodeFromString<ExtractionProgress>(
                input.replace("\"end_ms\":1000", "\"end_ms\":0")
            )
        }
    }
}
