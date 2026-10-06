package app.ovrly.overlay

import app.ovrly.contract.CaptureApiCodec
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.InvestigationCodec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The development fixture stays derived from the committed `packages/contracts` fixtures. */
class LiveResultsFixtureTest {
    private fun fixture(directory: String, name: String) =
        ContractFixtures.load(directory).single { it.name == name }

    @Test fun reportVersionOneIsThePartialResultFixture() {
        val partial = InvestigationCodec.parseInvestigation(
            fixture(ContractFixtures.RESULTS, "partial").investigationPayload()
        )
        assertEquals(LiveResultsFixture.INVESTIGATION_ID, partial.id)
        assertEquals(partial.report, LiveResultsFixture.provisionalReport)
    }

    @Test fun sessionIsTheWaitingStatusFixtureReopenedForThePartialInvestigation() {
        val waiting = CaptureApiCodec.parseStatus(
            fixture(ContractFixtures.INTAKE, "capture-status-waiting").responsePayload()
        )
        assertEquals(
            waiting.session.copy(
                investigationId = LiveResultsFixture.INVESTIGATION_ID,
                state = CaptureSessionState.OPEN,
                closedAt = null
            ),
            LiveResultsFixture.session
        )
        assertEquals(waiting.expiresAt, LiveResultsFixture.timeline.first().status.expiresAt)
    }

    @Test fun timelineWalksWaitingCheckingProvisionalUpdated() {
        val source = FixtureLiveResultsSource()
        val seen = mutableListOf(source.results.value)
        while (source.advance()) seen += source.results.value
        assertEquals(4, seen.size)
        assertTrue(seen.all { it.phase == LiveSessionPhase.CAPTURING })
        assertTrue(seen[0].claims.isEmpty())
        val speech = seen.map { results ->
            results.claims.firstOrNull { it.id == LiveResultsFixture.SPEECH_CLAIM }?.state
        }
        assertEquals(
            listOf(
                null,
                LiveClaimState.CHECKING_EVIDENCE,
                LiveClaimState.PROVISIONAL,
                LiveClaimState.UPDATED
            ),
            speech
        )
        val text = seen.last().claims.single { it.id == LiveResultsFixture.TEXT_CLAIM }
        assertEquals(LiveClaimState.PROVISIONAL, text.state)
        assertEquals(
            LiveClaimState.WAITING,
            seen[1].claims.single { it.id == LiveResultsFixture.TEXT_CLAIM }.state
        )
        assertNotNull(seen.last().claims.first().change)
        assertEquals(FixtureLiveResultsSource.LABEL, source.label)
    }

    @Test fun closingRecordsTheChoiceAndKeepsUnfinishedClaimsIncomplete() {
        for (continueResearch in listOf(true, false)) {
            val source = FixtureLiveResultsSource()
            repeat(2) { source.advance() }
            source.close(continueResearch)
            val results = source.results.value
            assertEquals(
                if (continueResearch) {
                    LiveSessionPhase.CONTINUING
                } else {
                    LiveSessionPhase.KEEPING_AVAILABLE
                },
                results.phase
            )
            assertTrue(results.claims.none { it.complete })
            assertFalse("closed fixture must not advance", source.advance())
        }
    }
}
