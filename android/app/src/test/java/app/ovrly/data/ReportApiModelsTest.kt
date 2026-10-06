package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.ContractParseException
import app.ovrly.contract.InvestigationCodec
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** The BE-10 inline bodies (#34): built in code they enforce the same rules the parser does. */
class ReportApiModelsTest {
    private val investigationId = "00000000-0000-4000-8000-000000000001"

    private fun job() = InvestigationCodec.parseInvestigation(
        ApiTestServer.result("partial").investigationPayload()
    ).job!!

    private fun summary(
        id: String = "rpt_synthetic_0001_v2",
        version: Int = 2,
        createdAt: String = "2026-10-04T12:11:00Z",
        supersedes: String? = "rpt_synthetic_0001_v1"
    ) = ReportVersionSummary(
        id = id,
        version = version,
        createdAt = createdAt,
        provisional = false,
        changeSummary = "Correction.",
        supersedes = supersedes,
        fixture = false
    )

    @Test
    fun aVersionSummaryAndListValidateWhatTheyHold() {
        val list = ReportVersionList(investigationId, listOf(summary(), summary(version = 1)))
        assertEquals(2, list.items.size)
        assertNull(summary(supersedes = null).supersedes)
        assertThrows(IllegalArgumentException::class.java) { summary(version = 0) }
        assertThrows(IllegalArgumentException::class.java) { summary(id = "not valid!") }
        assertThrows(IllegalArgumentException::class.java) { summary(createdAt = "yesterday") }
        assertThrows(IllegalArgumentException::class.java) { summary(supersedes = "bad id!") }
        assertThrows(IllegalArgumentException::class.java) {
            ReportVersionList("not-a-uuid", emptyList())
        }
    }

    @Test
    fun aReceiptValidatesItsIdsAndTheConfirmedFullVideo() {
        val receipt = ReanalysisReceipt(
            id = "00000000-0000-4000-8000-000000000901",
            investigationId = investigationId,
            reason = "expansion",
            baseVersion = 2,
            publishedVersion = null,
            job = job(),
            createdAt = "2026-10-04T12:20:00Z",
            sourceInvestigationId = "00000000-0000-4000-8000-000000000401"
        )
        assertEquals("00000000-0000-4000-8000-000000000401", receipt.sourceInvestigationId)
        assertNull(receipt.copy(sourceInvestigationId = null).sourceInvestigationId)
        assertThrows(IllegalArgumentException::class.java) { receipt.copy(id = "receipt-1") }
        assertThrows(IllegalArgumentException::class.java) {
            receipt.copy(sourceInvestigationId = "video-1")
        }
        assertThrows(IllegalArgumentException::class.java) { receipt.copy(createdAt = "now") }
    }

    @Test
    fun onlyAnEffectiveCancellationIsEffective() {
        val job = "00000000-0000-4000-8000-000000000102"
        assertTrue(CancelReceipt(job, "effective").effective)
        assertFalse(CancelReceipt(job, "requested").effective)
        val other = ReportApiCodec.parseCancel("""{"job_id":"$job","cancellation":"x"}""")
        assertFalse(other.effective)
    }

    @Test
    fun requestsRefuseWhatTheServerWouldReject() {
        assertThrows(IllegalArgumentException::class.java) {
            ReanalysisRequest.Correction(2, "clm_synthetic_0001", "   ")
        }
        assertThrows(IllegalArgumentException::class.java) {
            ReanalysisRequest.Correction(2, "claim one", "A meaning.")
        }
        assertThrows(IllegalArgumentException::class.java) {
            ReanalysisRequest.Correction(
                2,
                "clm_synthetic_0001",
                "x".repeat(ReanalysisRequest.MAX_PROPOSITION_LENGTH + 1)
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ReanalysisRequest.Expansion(2, "full-video")
        }
        assertThrows(IllegalArgumentException::class.java) { ReanalysisRequest.Deeper(0).encode() }
        val correction = ReanalysisRequest.Correction(2, "clm_synthetic_0001", "A meaning.")
        assertEquals("correction", correction.reason)
        val body = ContractFixtures.element(correction.encode()).jsonObject
        assertEquals("\"clm_synthetic_0001\"", body.getValue("claim_id").toString())
        assertEquals("deeper", ReanalysisRequest.Deeper(1).reason)
    }

    @Test
    fun aListWithoutItemsOrWithAnUnreadableItemFails() {
        assertThrows(ContractParseException::class.java) {
            ReportApiCodec.parseInvestigationList("""{"investigations":[]}""")
        }
        assertThrows(ContractParseException::class.java) {
            ReportApiCodec.parseInvestigationList("not json")
        }
        assertTrue(ReportApiCodec.parseInvestigationList("""{"items":[]}""").isEmpty())
        val complete = ApiTestServer.result("complete").investigationPayload()
        val listed = ReportApiCodec.parseInvestigationList("""{"items":[$complete]}""")
        assertEquals(investigationId, listed.single().id)
        assertTrue(ContractFixtures.element(complete) is JsonObject)
    }

    @Test
    fun malformedBodiesAreParseFailuresNeverResults() {
        listOf<(String) -> Any>(
            ReportApiCodec::parseVersions,
            ReportApiCodec::parseReceipt,
            ReportApiCodec::parseCancel
        ).forEach { parse ->
            assertThrows(ContractParseException::class.java) { parse("""{"unexpected":true}""") }
        }
    }
}
