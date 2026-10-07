package app.ovrly.contract

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
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

    @Test
    fun directlyBuiltProgressKeepsEveryCounterAndCoverageState() {
        val value = progress()
        assertTrue(value.closed)
        assertEquals(1, value.requestsUsed)
        assertEquals(5954, value.tokensReserved)
        assertEquals(2, value.maxRequests)
        assertEquals(200000, value.maxTokens)
        assertEquals(1, value.reconciliationRequests)
        assertEquals(20000, value.reconciliationTokens)
        val item = value.observations.single()
        assertEquals("synthetic-speech-1", item.observationId)
        assertEquals(0L, item.startMs)
        assertEquals(1000L, item.endMs)
        assertEquals(Timebase.CAPTURE, item.timebase)
        assertEquals(ExtractionCoverageStatus.PROCESSED, item.status)
        assertNull(item.reason)
        assertEquals(value, Json.decodeFromString<ExtractionProgress>(Json.encodeToString(value)))
        ExtractionCoverageStatus.entries.filter { it != ExtractionCoverageStatus.UNKNOWN }.forEach {
            assertEquals(it, ExtractionCoverageStatus.fromWire(it.wireName))
        }
    }

    @Test
    fun directlyBuiltInvalidProgressIsRejected() {
        listOf(
            { observation(startMs = -1) },
            { observation(endMs = 0) },
            { progress(requestsUsed = -1) },
            { progress(tokensReserved = -1) },
            { progress(maxRequests = 0) },
            { progress(maxTokens = 0) },
            { progress(reconciliationRequests = 0) },
            { progress(reconciliationTokens = 0) }
        ).forEach { build ->
            assertThrows(IllegalArgumentException::class.java) { build() }
        }
    }

    private fun observation(startMs: Long = 0, endMs: Long = 1000) = ObservationProgress(
        observationId = "synthetic-speech-1",
        startMs = startMs,
        endMs = endMs,
        timebase = Timebase.CAPTURE,
        status = ExtractionCoverageStatus.PROCESSED,
        reason = null
    )

    private fun progress(
        requestsUsed: Int = 1,
        tokensReserved: Int = 5954,
        maxRequests: Int = 2,
        maxTokens: Int = 200000,
        reconciliationRequests: Int = 1,
        reconciliationTokens: Int = 20000
    ) = ExtractionProgress(
        closed = true,
        requestsUsed = requestsUsed,
        tokensReserved = tokensReserved,
        maxRequests = maxRequests,
        maxTokens = maxTokens,
        reconciliationRequests = reconciliationRequests,
        reconciliationTokens = reconciliationTokens,
        observations = listOf(observation())
    )
}
