package app.ovrly.contract

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ReconciliationTest {
    private val summary = """
        {"status":"complete","coverage_limited":true,"reassessment_claim_ids":["clm_synthetic_0001"]}
    """.trimIndent()

    @Test
    fun interpretationCompletionDoesNotCompleteAnAssessment() {
        val fixture = ContractFixtures.load(ContractFixtures.RESULTS).single {
            it.name == "partial"
        }
        val original = checkNotNull(fixture.investigation).jsonObject
        val report = original.getValue("report").jsonObject
        val reconciliation = Json.parseToJsonElement(summary)
        val updatedReport = JsonObject(report + ("reconciliation" to reconciliation))
        val updated = JsonObject(original + ("report" to updatedReport))
        val value = InvestigationCodec.parseInvestigation(updated.toString())
        assertTrue(checkNotNull(value.report).provisional)
        assertFalse(value.isComplete)
        assertEquals(
            listOf("clm_synthetic_0001"),
            checkNotNull(value.report.reconciliation).reassessmentClaimIds
        )
        assertNull(value.reconciliationProgress)
    }

    @Test
    fun unknownFinalityNeverBecomesComplete() {
        val value = Json.decodeFromString<ReconciliationSummary>(
            summary.replace("complete", "future")
        )
        assertEquals(ReconciliationStatus.UNKNOWN, value.status)
        assertThrows(SerializationException::class.java) { Json.encodeToString(value) }
    }

    @Test
    fun failurePreservesTheSeparateSafeError() {
        val value = Json.decodeFromString<ReconciliationProgress>(
            """
                {"status":"failed","error":{
                  "code":"EXTRACTION_INVALID","message":"Invalid output","retryable":false
                }}
            """.trimIndent()
        )
        assertEquals(ReconciliationStatus.FAILED, value.status)
        assertEquals("EXTRACTION_INVALID", value.error?.code)
        assertEquals(
            ReconciliationStatus.UNKNOWN,
            Json.decodeFromString<ReconciliationProgress>(
                """{"status":"future","error":null}"""
            ).status
        )
    }

    @Test
    fun correctionLinksAndOriginalWordsRoundTripTogether() {
        val fixture = ContractFixtures.load(ContractFixtures.RESULTS).single {
            it.name == "complete"
        }
        val report = checkNotNull(
            InvestigationCodec.parseInvestigation(
                checkNotNull(fixture.investigation).toString()
            ).report
        )
        val first = report.claims[0]
        val later = report.claims[1]
        val linked = first.copy(supersededByOccurrenceId = later.occurrenceId)
        val correction = later.copy(correctsOccurrenceId = first.occurrenceId)
        assertEquals(linked, Json.decodeFromString<Claim>(Json.encodeToString(linked)))
        assertEquals(correction, Json.decodeFromString<Claim>(Json.encodeToString(correction)))
        assertEquals(first.originalText, linked.originalText)
        assertEquals(later.originalText, correction.originalText)
    }

    @Test
    fun malformedReassessmentMetadataIsRejected() {
        listOf(
            summary.replace("true", "\"true\""),
            summary.replace("complete", "waiting"),
            summary.replace(
                "\"clm_synthetic_0001\"",
                "\"clm_synthetic_0001\",\"clm_synthetic_0001\""
            ),
            summary.replace("clm_synthetic_0001", "")
        ).forEach { invalid ->
            assertThrows(IllegalArgumentException::class.java) {
                Json.decodeFromString<ReconciliationSummary>(invalid)
            }
        }
    }
}
