package app.ovrly.overlay

import android.util.Log
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Investigation
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The two reads the live panel needs, implemented over the #18 HTTP client:
 * `GET /v1/captures/{id}` (parse with `CaptureApiCodec.parseStatus`) and
 * `GET /v1/investigations/{id}` (parse with `InvestigationCodec.parseInvestigation`). Throwing
 * any [Exception] counts as a failed poll and is retried with backoff.
 */
internal interface LiveResultsFetcher {
    suspend fun captureStatus(sessionId: String): CaptureStatus
    suspend fun investigation(investigationId: String): Investigation
}

/**
 * Poll timing: steady interval while capturing, a slower one after close, doubling backoff
 * after failures. Polling after close is bounded by [maxClosedPolls] (10 minutes by default),
 * so a claim that never settles, for example one in an unknown state, cannot poll forever.
 */
internal data class LivePollPolicy(
    val intervalMs: Long = 2_000,
    val closedIntervalMs: Long = 5_000,
    val maxClosedPolls: Int = 120,
    val maxBackoffMs: Long = 30_000,
    /** Consecutive failures after which polling stops and the panel says so. */
    val maxFailures: Int = 8
) {
    fun delayAfter(failures: Int, closed: Boolean = false): Long {
        if (failures <= 0) return if (closed) closedIntervalMs else intervalMs
        var delay = intervalMs
        repeat(failures.coerceAtMost(MAX_DOUBLINGS)) {
            delay = (delay * 2).coerceAtMost(maxBackoffMs)
        }
        return delay
    }

    private companion object {
        const val MAX_DOUBLINGS = 16
    }
}

/**
 * True while the session can still change: open, or closed with research continuing while
 * claim extraction is unfinished or some claim is unsettled.
 */
internal fun keepPolling(status: CaptureStatus, results: LiveResults): Boolean =
    when (status.session.state) {
        CaptureSessionState.OPEN -> true

        CaptureSessionState.CLOSED ->
            status.continueResearch == true && researchPending(status, results)

        CaptureSessionState.ABANDONED, CaptureSessionState.UNKNOWN -> false
    }

private fun researchPending(status: CaptureStatus, results: LiveResults): Boolean =
    status.claimExtractionStatus != CoverageStatus.COMPLETE || results.claims.any { !it.settled }

private const val TAG = "OvrlyLive"

private val LiveClaim.settled: Boolean
    get() = complete || state == LiveClaimState.FAILED || state == LiveClaimState.CANCELLED

/**
 * The live adapter: polls one capture session and its investigation, folds each poll with
 * [reduceLiveResults] and backs off on failure. Network access is entirely in [fetcher].
 */
internal class PollingLiveResultsSource(
    private val fetcher: LiveResultsFetcher,
    private val policy: LivePollPolicy = LivePollPolicy(),
    private val sleep: suspend (Long) -> Unit = { delay(it) }
) : LiveResultsSource {
    private val mutable = MutableStateFlow(LiveResults.NotConnected)
    override val results: StateFlow<LiveResults> = mutable.asStateFlow()
    override val label: String? = null
    private var job: Job? = null

    fun start(scope: CoroutineScope, sessionId: String) {
        stop()
        mutable.value = LiveResults.NotConnected
        job = scope.launch { poll(sessionId) }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    /**
     * Runs until the session settles, the closed-session bound is reached, polling fails too
     * often, or the caller cancels.
     */
    @Suppress("TooGenericExceptionCaught")
    suspend fun poll(sessionId: String) {
        var failures = 0
        var closedPolls = 0
        while (true) {
            val keep = try {
                val status = fetcher.captureStatus(sessionId)
                val report = fetcher.investigation(status.session.investigationId).report
                val next = reduceLiveResults(status, report, mutable.value)
                failures = 0
                if (!status.session.isOpen) closedPolls += 1
                val bounded = closedPolls >= policy.maxClosedPolls && keepPolling(status, next)
                mutable.value = if (bounded) next.copy(connection = LiveConnection.ENDED) else next
                keepPolling(status, next) && !bounded
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                Log.w(TAG, "Live results poll failed: ${error.javaClass.simpleName}")
                failures += 1
                mutable.value = mutable.value.copy(connection = LiveConnection.RETRYING)
                if (failures >= policy.maxFailures) {
                    mutable.value = mutable.value.copy(connection = LiveConnection.LOST)
                    false
                } else {
                    true
                }
            }
            if (!keep) return
            sleep(policy.delayAfter(failures, closed = closedPolls > 0))
        }
    }
}

/**
 * Integration seam for #18 and #26: once a capture session exists, call [start] with the
 * #18-backed [LiveResultsFetcher] and the session id; the overlay then shows live results.
 * [stop] returns the overlay to "not connected". Until then the overlay stays not connected.
 */
internal object LiveResultsConnection {
    private var active: PollingLiveResultsSource? = null

    fun start(scope: CoroutineScope, fetcher: LiveResultsFetcher, sessionId: String) {
        stop()
        val source = PollingLiveResultsSource(fetcher)
        active = source
        OverlayStore.liveSource.value = source
        source.start(scope, sessionId)
    }

    fun stop() {
        active?.stop()
        active = null
        OverlayStore.liveSource.value = NotConnectedLiveResultsSource
    }
}
