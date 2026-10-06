package app.ovrly.overlay

import app.ovrly.contract.CaptureClaimState
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ProcessingStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class LivePollingTest {
    private val investigation: Investigation = InvestigationCodec.parseInvestigation(
        ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == "partial" }
            .investigationPayload()
    )
    private val open = LiveResultsFixture.timeline[2].status

    private fun closed(continueResearch: Boolean) =
        LiveResultsFixture.closed(LiveResultsFixture.timeline[2], continueResearch).status

    /** Plays [responses] in order; a null entry throws like a failed request. */
    private class FakeFetcher(
        private val investigation: Investigation,
        responses: List<CaptureStatus?>,
        private val onCall: (Int) -> Unit = {}
    ) : LiveResultsFetcher {
        private val queue = ArrayDeque(responses)
        var calls = 0

        override suspend fun captureStatus(sessionId: String): CaptureStatus {
            onCall(calls)
            calls += 1
            return queue.removeFirstOrNull() ?: throw java.io.IOException("offline")
        }

        override suspend fun investigation(investigationId: String) = investigation
    }

    @Test fun backoffDoublesAfterFailuresAndIsCapped() {
        val policy = LivePollPolicy()
        assertEquals(
            listOf(2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L),
            (0..5).map(policy::delayAfter)
        )
        assertEquals(30_000L, policy.delayAfter(Int.MAX_VALUE))
    }

    @Test fun pollsWithBackoffThenStopsWhenTheSessionCloses() = runBlocking {
        val delays = mutableListOf<Long>()
        lateinit var source: PollingLiveResultsSource
        val seen = mutableListOf<LiveConnection>()
        val fetcher = FakeFetcher(investigation, listOf(open, null, null, closed(false))) {
            if (it > 0) seen += source.results.value.connection
        }
        source = PollingLiveResultsSource(fetcher, sleep = { delays += it })
        source.poll(LiveResultsFixture.session.id)

        assertEquals(listOf(2_000L, 4_000L, 8_000L), delays)
        assertEquals(
            listOf(LiveConnection.OK, LiveConnection.RETRYING, LiveConnection.RETRYING),
            seen
        )
        val results = source.results.value
        assertEquals(LiveSessionPhase.KEEPING_AVAILABLE, results.phase)
        assertEquals(LiveConnection.OK, results.connection)
        assertEquals(
            listOf(LiveResultsFixture.SPEECH_CLAIM, LiveResultsFixture.TEXT_CLAIM),
            results.claims.map { it.id }
        )
    }

    @Test fun repeatedFailuresKeepLastResultsAndReportLost() = runBlocking {
        val delays = mutableListOf<Long>()
        val policy = LivePollPolicy(maxFailures = 3)
        val fetcher = FakeFetcher(investigation, listOf(open))
        val source = PollingLiveResultsSource(fetcher, policy, sleep = { delays += it })
        source.poll(LiveResultsFixture.session.id)

        assertEquals(4, fetcher.calls)
        assertEquals(listOf(2_000L, 4_000L, 8_000L), delays)
        assertEquals(LiveConnection.LOST, source.results.value.connection)
        assertEquals(LiveSessionPhase.CAPTURING, source.results.value.phase)
        assertEquals(2, source.results.value.claims.size)
    }

    @Test fun continuingResearchPollsUntilExtractionCompletesAndEveryClaimSettles() {
        val start = reduceLiveResults(closed(true), investigation.report, null)
        assertTrue(keepPolling(closed(true), start))
        assertFalse(keepPolling(closed(false), start))
        val cancelled = start.claims.map { it.copy(state = LiveClaimState.CANCELLED) }
        val settled = start.copy(claims = cancelled)
        val extracted = closed(true).copy(claimExtractionStatus = CoverageStatus.COMPLETE)
        assertFalse(keepPolling(extracted, settled))
        assertTrue(keepPolling(extracted, start))
        assertTrue(keepPolling(open, start))
        for (state in listOf(CaptureSessionState.ABANDONED, CaptureSessionState.UNKNOWN)) {
            val ended = open.copy(session = open.session.copy(state = state))
            assertFalse(keepPolling(ended, start))
        }
    }

    @Test fun continuingResearchWithNoClaimsYetKeepsPolling() {
        val empty = closed(true).copy(
            claims = emptyList(),
            claimExtractionStatus = CoverageStatus.NOT_STARTED
        )
        val results = reduceLiveResults(empty, null, null)
        assertTrue(results.claims.isEmpty())
        assertTrue(keepPolling(empty, results))
        val unknown = empty.copy(claimExtractionStatus = CoverageStatus.UNKNOWN)
        assertTrue(keepPolling(unknown, reduceLiveResults(unknown, null, null)))
    }

    @Test fun partialExtractionKeepsPollingAfterCurrentClaimsSettle() {
        val status = closed(true)
        assertEquals(CoverageStatus.PARTIAL, status.claimExtractionStatus)
        val results = reduceLiveResults(status, investigation.report, null)
        val settled = results.copy(
            claims = results.claims.map { it.copy(state = LiveClaimState.FAILED) }
        )
        assertTrue(keepPolling(status, settled))
    }

    @Test fun unsettledUnknownClaimStopsAtTheClosedSessionBound() = runBlocking {
        val delays = mutableListOf<Long>()
        val unknown = closed(true).copy(
            claims = listOf(
                CaptureClaimState(LiveResultsFixture.SPEECH_CLAIM, ProcessingStatus.UNKNOWN, null)
            ),
            claimExtractionStatus = CoverageStatus.COMPLETE
        )
        var calls = 0
        val fetcher = object : LiveResultsFetcher {
            override suspend fun captureStatus(sessionId: String): CaptureStatus {
                calls += 1
                return unknown
            }

            override suspend fun investigation(investigationId: String) = investigation
        }
        val policy = LivePollPolicy(maxClosedPolls = 3)
        val source = PollingLiveResultsSource(fetcher, policy, sleep = { delays += it })
        source.poll(LiveResultsFixture.session.id)

        assertEquals(3, calls)
        assertEquals(listOf(5_000L, 5_000L), delays)
        val results = source.results.value
        assertEquals(LiveConnection.ENDED, results.connection)
        assertEquals(LiveClaimState.UNKNOWN, results.claims.first().state)
        assertEquals(120, LivePollPolicy().maxClosedPolls)
    }

    @Test fun connectionSwapsTheOverlaySourceAndBack() {
        val scope = CoroutineScope(Job())
        val fetcher = object : LiveResultsFetcher {
            override suspend fun captureStatus(sessionId: String): CaptureStatus =
                awaitCancellation()
            override suspend fun investigation(investigationId: String) = investigation
        }
        LiveResultsConnection.start(scope, fetcher, LiveResultsFixture.session.id)
        assertTrue(OverlayStore.liveSource.value is PollingLiveResultsSource)
        assertEquals(null, OverlayStore.liveSource.value.label)
        LiveResultsConnection.stop()
        assertSame(NotConnectedLiveResultsSource, OverlayStore.liveSource.value)
        scope.coroutineContext[Job]?.cancel()
    }
}
