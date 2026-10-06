package app.ovrly.overlay

import app.ovrly.contract.Assessment
import app.ovrly.contract.CaptureApiCodec
import app.ovrly.contract.CaptureClaimState
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.EvidenceRelation
import app.ovrly.contract.OverallAssessment
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.Relation
import app.ovrly.contract.ReportVersion
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveResultsTest {
    private val provisional = LiveResultsFixture.provisionalReport
    private val updated = LiveResultsFixture.updatedReport
    private val speech = LiveResultsFixture.SPEECH_CLAIM
    private val text = LiveResultsFixture.TEXT_CLAIM

    private fun assessment(overall: OverallAssessment, provisional: Boolean) = Assessment(
        id = "asm_test",
        claimId = speech,
        version = 1,
        relations = listOf(EvidenceRelation("evd_test", Relation.SUPPORT, null)),
        overall = overall,
        provisional = provisional,
        summary = "Synthetic."
    )

    private fun status(vararg claims: Pair<String, ProcessingStatus>): CaptureStatus =
        LiveResultsFixture.timeline.last().status.copy(
            claims = claims.map { (id, state) -> CaptureClaimState(id, state, null) }
        )

    private fun reduce(
        claimId: String,
        state: ProcessingStatus,
        report: ReportVersion?,
        previous: LiveResults?
    ) = reduceLiveResults(status(claimId to state), report, previous)

    private fun claim(results: LiveResults, id: String) = results.claims.single { it.id == id }

    @Test fun processingStatusesMapToTheFiveLiveStates() {
        val final = assessment(OverallAssessment.INSUFFICIENT_EVIDENCE, provisional = false)
        val pending = assessment(OverallAssessment.INSUFFICIENT_EVIDENCE, provisional = true)
        assertEquals(LiveClaimState.WAITING, liveClaimState(ProcessingStatus.WAITING, null))
        assertEquals(
            LiveClaimState.CHECKING_EVIDENCE,
            liveClaimState(ProcessingStatus.CHECKING, null)
        )
        assertEquals(
            LiveClaimState.CHECKING_EVIDENCE,
            liveClaimState(ProcessingStatus.PARTIAL, null)
        )
        assertEquals(LiveClaimState.PROVISIONAL, liveClaimState(ProcessingStatus.PARTIAL, pending))
        assertEquals(LiveClaimState.PROVISIONAL, liveClaimState(ProcessingStatus.COMPLETE, pending))
        assertEquals(LiveClaimState.ASSESSED, liveClaimState(ProcessingStatus.COMPLETE, final))
        assertEquals(LiveClaimState.FAILED, liveClaimState(ProcessingStatus.FAILED, final))
        assertEquals(LiveClaimState.CANCELLED, liveClaimState(ProcessingStatus.CANCELLED, final))
    }

    @Test fun unknownNeverBecomesAssessed() {
        val final = assessment(OverallAssessment.INSUFFICIENT_EVIDENCE, provisional = false)
        val unknownFinal = assessment(OverallAssessment.UNKNOWN, provisional = false)
        val cases = listOf(
            ProcessingStatus.UNKNOWN to final,
            ProcessingStatus.UNKNOWN to null,
            null to final,
            ProcessingStatus.COMPLETE to unknownFinal,
            ProcessingStatus.COMPLETE to null,
            ProcessingStatus.PARTIAL to unknownFinal
        )
        for ((status, assessment) in cases) {
            assertEquals(
                "$status / $assessment",
                LiveClaimState.UNKNOWN,
                liveClaimState(status, assessment)
            )
        }
        for (status in ProcessingStatus.entries) {
            for (overall in OverallAssessment.entries) {
                val state = liveClaimState(status, assessment(overall, provisional = false))
                if (status == ProcessingStatus.UNKNOWN || overall == OverallAssessment.UNKNOWN) {
                    assertNotEquals("$status / $overall", LiveClaimState.ASSESSED, state)
                }
            }
        }
    }

    @Test fun unknownClaimStateFromTheWireIsRenderedUnknownAndIncomplete() {
        val fixture = ContractFixtures.load(ContractFixtures.INTAKE)
            .single { it.name == "capture-status-waiting" }
        val claims = ContractFixtures.element(
            """[{"claim_id":"$speech","processing_status":"__future_value__","error":null}]"""
        )
        val payload = ContractFixtures.replace(
            ContractFixtures.element(fixture.responsePayload()),
            listOf("claims"),
            claims
        )
        val parsed = CaptureApiCodec.parseStatus(payload.toString())
        val results = reduceLiveResults(parsed, null, null)
        assertEquals(ProcessingStatus.UNKNOWN, parsed.claims.single().processingStatus)
        assertEquals(LiveClaimState.UNKNOWN, claim(results, speech).state)
        assertFalse(claim(results, speech).complete)
        assertNull(claim(results, speech).assessment)
    }

    @Test fun unknownSessionStateIsNotCapturingOrClosed() {
        val fixture = ContractFixtures.load(ContractFixtures.INTAKE)
            .single { it.name == "capture-status-waiting" }
        val payload = ContractFixtures.replace(
            ContractFixtures.element(fixture.responsePayload()),
            listOf("session", "state"),
            JsonPrimitive("__future_value__")
        )
        val parsed = CaptureApiCodec.parseStatus(payload.toString())
        assertEquals(CaptureSessionState.UNKNOWN, parsed.session.state)
        assertEquals(LiveSessionPhase.UNKNOWN, reduceLiveResults(parsed, null, null).phase)
    }

    @Test fun sessionPhaseFollowsStateAndStopChoice() {
        assertEquals(LiveSessionPhase.CAPTURING, liveSessionPhase(CaptureSessionState.OPEN, null))
        assertEquals(
            LiveSessionPhase.CONTINUING,
            liveSessionPhase(CaptureSessionState.CLOSED, true)
        )
        assertEquals(
            LiveSessionPhase.KEEPING_AVAILABLE,
            liveSessionPhase(CaptureSessionState.CLOSED, false)
        )
        assertEquals(LiveSessionPhase.CLOSED, liveSessionPhase(CaptureSessionState.CLOSED, null))
        assertEquals(
            LiveSessionPhase.ABANDONED,
            liveSessionPhase(CaptureSessionState.ABANDONED, null)
        )
        assertEquals(LiveSessionPhase.UNKNOWN, liveSessionPhase(CaptureSessionState.UNKNOWN, true))
    }

    @Test fun committedWaitingStatusReportsCapturedSegmentAndMissingTail() {
        val fixture = ContractFixtures.load(ContractFixtures.INTAKE)
            .single { it.name == "capture-status-waiting" }
        val results = reduceLiveResults(
            CaptureApiCodec.parseStatus(fixture.responsePayload()),
            null,
            null
        )
        assertEquals(LiveSessionPhase.CONTINUING, results.phase)
        assertEquals(LiveCoverage(capturedMs = 20_000, missingMs = 10_000), results.coverage)
        assertTrue(results.claims.isEmpty())
    }

    @Test fun claimsFollowSpokenOrderNotStatusOrder() {
        val results = reduceLiveResults(
            status(text to ProcessingStatus.WAITING, speech to ProcessingStatus.CHECKING),
            provisional,
            null
        )
        assertEquals(listOf(speech, text), results.claims.map { it.id })
        assertEquals(listOf(4200L, 31000L), results.claims.map { it.startMs })
    }

    @Test fun claimsWithoutWordingKeepStatusOrderAfterPlacedClaims() {
        val results = reduceLiveResults(
            status(
                "clm_later_b" to ProcessingStatus.WAITING,
                text to ProcessingStatus.CHECKING,
                "clm_later_a" to ProcessingStatus.WAITING,
                speech to ProcessingStatus.PARTIAL
            ),
            provisional,
            null
        )
        assertEquals(
            listOf(speech, text, "clm_later_b", "clm_later_a"),
            results.claims.map { it.id }
        )
        assertNull(claim(results, "clm_later_b").text)
    }

    @Test fun reportClaimMissingFromStatusIsUnknown() {
        val results = reduce(speech, ProcessingStatus.PARTIAL, provisional, null)
        assertEquals(LiveClaimState.PROVISIONAL, claim(results, speech).state)
        assertEquals(LiveClaimState.UNKNOWN, claim(results, text).state)
    }

    @Test fun reportOfAnotherInvestigationIsIgnored() {
        val other = provisional.copy(investigationId = "00000000-0000-4000-8000-000000000999")
        val results = reduce(speech, ProcessingStatus.PARTIAL, other, null)
        assertNull(claim(results, speech).text)
        assertEquals(LiveClaimState.CHECKING_EVIDENCE, claim(results, speech).state)
    }

    @Test fun replacedProvisionalAssessmentBecomesUpdatedAndStays() {
        val first = reduceLiveResults(
            status(speech to ProcessingStatus.PARTIAL, text to ProcessingStatus.CHECKING),
            provisional,
            null
        )
        val second = reduceLiveResults(
            status(speech to ProcessingStatus.COMPLETE, text to ProcessingStatus.PARTIAL),
            updated,
            first
        )
        val change = checkNotNull(claim(second, speech).change)
        assertEquals(LiveClaimState.UPDATED, claim(second, speech).state)
        assertTrue(change.before.provisional)
        assertFalse(change.after.provisional)
        assertEquals(updated.changeSummary, change.reason)
        assertTrue(claim(second, speech).complete)
        assertEquals(LiveClaimState.PROVISIONAL, claim(second, text).state)
        assertEquals(listOf(speech), newAssessmentUpdates(first, second).map { it.id })

        val third = reduceLiveResults(
            status(speech to ProcessingStatus.COMPLETE, text to ProcessingStatus.PARTIAL),
            updated,
            second
        )
        assertEquals(LiveClaimState.UPDATED, claim(third, speech).state)
        assertEquals(change, claim(third, speech).change)
        assertTrue(newAssessmentUpdates(second, third).isEmpty())
    }

    @Test fun finalAssessmentNeverShownProvisionallyIsAssessedWithoutNotice() {
        val first = reduce(speech, ProcessingStatus.CHECKING, null, null)
        val second = reduce(speech, ProcessingStatus.COMPLETE, updated, first)
        assertEquals(LiveClaimState.ASSESSED, claim(second, speech).state)
        assertNull(claim(second, speech).change)
        assertTrue(newAssessmentUpdates(first, second).isEmpty())
    }

    @Test fun provisionalClaimThatFailsIsIncompleteNotUpdated() {
        val first = reduce(speech, ProcessingStatus.PARTIAL, provisional, null)
        val second = reduce(speech, ProcessingStatus.FAILED, provisional, first)
        assertEquals(LiveClaimState.FAILED, claim(second, speech).state)
        assertFalse(claim(second, speech).complete)
        assertTrue(newAssessmentUpdates(first, second).isEmpty())
    }

    @Test fun unchangedProvisionalAssessmentIsNotAnUpdate() {
        val first = reduce(speech, ProcessingStatus.PARTIAL, provisional, null)
        val second = reduce(speech, ProcessingStatus.PARTIAL, provisional, first)
        assertEquals(LiveClaimState.PROVISIONAL, claim(second, speech).state)
        assertTrue(newAssessmentUpdates(first, second).isEmpty())
    }

    @Test fun notConnectedSourceNeverEmitsClaims() {
        val results = NotConnectedLiveResultsSource.results.value
        assertEquals(LiveSessionPhase.NOT_CONNECTED, results.phase)
        assertTrue(results.claims.isEmpty())
        assertNull(NotConnectedLiveResultsSource.label)
    }
}
