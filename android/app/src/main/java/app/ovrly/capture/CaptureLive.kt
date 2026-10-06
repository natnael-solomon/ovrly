package app.ovrly.capture

import android.content.Context
import app.ovrly.AppNotifications
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.Investigation
import app.ovrly.data.ApiServices
import app.ovrly.data.InvestigationRepository
import app.ovrly.overlay.LiveConnection
import app.ovrly.overlay.LiveResultsConnection
import app.ovrly.overlay.LiveResultsFetcher
import app.ovrly.overlay.OverlayStore
import app.ovrly.overlay.researchSettled
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * The live panel's reads: the capture status through the capture API and the investigation
 * through [InvestigationRepository.refresh], which also stores the capture's investigation and
 * its report versions in the Room store like any other server read.
 */
internal class CaptureLiveResultsFetcher(
    private val capture: CaptureSessionApi,
    private val investigations: InvestigationRepository
) : LiveResultsFetcher {
    override suspend fun captureStatus(sessionId: String): CaptureStatus = capture.status(sessionId)

    override suspend fun investigation(investigationId: String): Investigation =
        investigations.refresh(investigationId).orThrow()
}

/**
 * Connects the overlay's live results to the server capture session. Polling starts when the
 * uploader learns the server session id and keeps running after Stop until the session
 * settles (the poll policy bounds it); it is stopped when a new capture starts. The labelled
 * in-memory capture server has no investigations to read, so it never connects.
 */
internal object CaptureLive {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var connected: String? = null
    private var watcher: Job? = null

    /** Starts polling [sessionId] unless it is already polling or ended normally. */
    fun connect(context: Context, sessionId: String) {
        val capture = CaptureApis.current(context)
        val investigations = ApiServices.get(context)?.investigations
        if (capture.isTestServer || investigations == null) return
        synchronized(this) {
            val lost = OverlayStore.liveSource.value.results.value.connection == LiveConnection.LOST
            if (connected == sessionId && !lost) return
            connected = sessionId
            val source = LiveResultsConnection.start(
                scope,
                CaptureLiveResultsFetcher(capture, investigations),
                sessionId
            )
            watcher?.cancel()
            val app = context.applicationContext
            // Independent of the overlay: dismissing it does not stop research or this notice.
            watcher = scope.launch {
                val settled = source.results.first { researchSettled(it) }
                AppNotifications.resultsReady(app, settled.claims.size, source.investigationId)
            }
        }
    }

    /** Returns the overlay to "not connected"; called when a new capture replaces the old one. */
    fun disconnect() = synchronized(this) {
        watcher?.cancel()
        watcher = null
        if (connected != null) {
            connected = null
            LiveResultsConnection.stop()
        }
    }
}
