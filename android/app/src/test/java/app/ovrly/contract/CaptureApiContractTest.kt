package app.ovrly.contract

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureApiContractTest {
    @Test
    fun pendingCaptureIsNotACompletedOrNoClaimsFinding() {
        val fixture = ContractFixtures.load(ContractFixtures.INTAKE)
            .single { it.name == "capture-status-waiting" }
        val status = CaptureApiCodec.parseStatus(fixture.responsePayload())
        assertFalse(status.session.isOpen)
        assertEquals("not_started", status.claimExtractionStatus)
        assertTrue(status.claims.isEmpty())
        assertEquals(ProcessingStatus.WAITING, status.work.single().processingStatus)
        assertEquals(20000, status.manifest.durationMs)
        assertEquals(
            listOf(Interval(10000, 20000, Timebase.CAPTURE)),
            status.manifest.missingIntervals
        )
        assertEquals(
            status.manifest.declaredCoverage.speech,
            status.manifest.declaredCoverage.text
        )
    }

    @Test
    fun createAndCloseRequestsHaveStrictTypesAndBounds() {
        assertEquals(CaptureCreateRequest(), CaptureApiCodec.parseCreateRequest("{}"))
        assertEquals(
            CaptureCreateRequest(5000),
            CaptureApiCodec.parseCreateRequest(
                CaptureApiCodec.encodeCreateRequest(CaptureCreateRequest(5000))
            )
        )
        assertEquals(
            CaptureCloseRequest(false),
            CaptureApiCodec.parseCloseRequest("""{"continue_research":false}""")
        )
        listOf(
            """{"chunk_duration_ms":999}""",
            """{"chunk_duration_ms":30001}""",
            """{"chunk_duration_ms":"10000"}""",
            """{"owner_id":"not-allowed"}"""
        ).forEach { payload ->
            assertThrows(ContractParseException::class.java) {
                CaptureApiCodec.parseCreateRequest(payload)
            }
        }
        listOf(
            """{}""",
            """{"continue_research":"false"}""",
            """{"continue_research":true,"duration_ms":180001}"""
        ).forEach { payload ->
            assertThrows(ContractParseException::class.java) {
                CaptureApiCodec.parseCloseRequest(payload)
            }
        }
    }

    @Test
    fun captureSourceCannotBeSentToTheSharedMediaCreateEndpoint() {
        val source = InvestigationSource.Capture("00000000-0000-4000-8000-000000000501")
        assertEquals(SourceKind.CAPTURE, source.kind)
        assertThrows(IllegalArgumentException::class.java) {
            InvestigationCreateRequest(source)
        }
    }
}
