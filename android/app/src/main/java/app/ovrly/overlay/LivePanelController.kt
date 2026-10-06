package app.ovrly.overlay

import android.content.Context
import android.content.Intent
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureService
import app.ovrly.capture.CaptureState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * The user's answer to Stop. `true` is "Continue research in queue", `false` is "Keep only
 * available results"; both stop recording. Capture closes the session with
 * `continue_research` set to this value.
 */
internal fun interface StopChoiceHandler {
    fun onStopChoice(continueResearch: Boolean)
}

/**
 * Production [StopChoiceHandler]: sends [CaptureService.STOP] with
 * [CaptureService.EXTRA_CONTINUE_RESEARCH].
 * This is the only path from the overlay to capture; the overlay never closes a session itself.
 */
internal class CaptureServiceStopChoice(private val context: Context) : StopChoiceHandler {
    override fun onStopChoice(continueResearch: Boolean) {
        context.startService(
            Intent(context, CaptureService::class.java)
                .setAction(CaptureService.STOP)
                .putExtra(CaptureService.EXTRA_CONTINUE_RESEARCH, continueResearch)
        )
    }
}

internal data class LivePanelState(
    /** Hiding the results panel keeps capture running; only Stop ends it. */
    val panelVisible: Boolean = true,
    val stopPrompt: Boolean = false,
    /** The Stop choice already sent, so the panel can say what happens next. */
    val stopChoice: Boolean? = null,
    /** Claims with an unread "Assessment updated" notice, oldest first. */
    val notices: List<String> = emptyList(),
    val detailClaimId: String? = null
)

/**
 * Panel interaction state for the compact overlay. It never stops capture except through
 * [StopChoiceHandler], and only after the user picked one of the two Stop choices.
 */
internal class LivePanelController(private val stopChoice: StopChoiceHandler) {
    private val mutable = MutableStateFlow(LivePanelState())
    val state: StateFlow<LivePanelState> = mutable.asStateFlow()
    private var last: LiveResults? = null
    private var captureRunning = false

    /** Forgets notices, choices and the last results, for example when the source changes. */
    fun reset() {
        last = null
        mutable.value = LivePanelState()
    }

    fun onResults(results: LiveResults) {
        val fresh = newAssessmentUpdates(last, results).map { it.id }
        last = results
        if (fresh.isNotEmpty()) {
            mutable.update { it.copy(notices = (it.notices - fresh.toSet()) + fresh) }
        }
    }

    /** Hiding closes any claim detail; neither direction touches capture. */
    fun setPanelVisible(visible: Boolean) = mutable.update {
        it.copy(panelVisible = visible, detailClaimId = if (visible) it.detailClaimId else null)
    }

    fun requestStop() = mutable.update {
        if (it.stopChoice == null) it.copy(stopPrompt = true) else it
    }

    fun cancelStop() = mutable.update { it.copy(stopPrompt = false) }

    fun chooseStop(continueResearch: Boolean) {
        val before = mutable.value
        if (!before.stopPrompt || before.stopChoice != null) return
        mutable.value = before.copy(stopPrompt = false, stopChoice = continueResearch)
        stopChoice.onStopChoice(continueResearch)
    }

    /**
     * Capture ended elsewhere (notification Stop, time limit, revoked projection): the prompt
     * no longer applies. Only a new capture (not-running to running) clears the previous
     * choice; repeated running updates while Stop is in flight keep it, so Stop cannot be
     * sent twice.
     */
    fun onCaptureRunning(running: Boolean) {
        val started = running && !captureRunning
        captureRunning = running
        mutable.update {
            when {
                !running -> it.copy(stopPrompt = false)
                started -> it.copy(stopChoice = null)
                else -> it
            }
        }
    }

    fun openClaim(claimId: String) = mutable.update {
        it.copy(panelVisible = true, detailClaimId = claimId, notices = it.notices - claimId)
    }

    fun closeClaim() = mutable.update { it.copy(detailClaimId = null) }

    fun dismissNotice(claimId: String) = mutable.update { it.copy(notices = it.notices - claimId) }
}

/** What the overlay window renders. The demo carries no live data by construction. */
internal sealed interface OverlayContent {
    data object Demo : OverlayContent
    data class Live(val results: LiveResults, val sourceLabel: String?) : OverlayContent
}

internal fun overlayContent(
    demo: Boolean,
    results: LiveResults,
    sourceLabel: String?
): OverlayContent = if (demo) OverlayContent.Demo else OverlayContent.Live(results, sourceLabel)

/** The results panel (and so the list that blocks window dragging) is on screen. */
internal fun livePanelShown(demo: Boolean, results: LiveResults, panel: LivePanelState): Boolean =
    !demo && results.phase != LiveSessionPhase.NOT_CONNECTED && panel.panelVisible

/**
 * The recording pill is hidden for the fixture (which has no real capture) and once a
 * connected session's capture has finished, where the pill's "Research offline" would be
 * wrong; the panel then carries the state.
 */
internal fun showRecordingPill(
    fixture: Boolean,
    results: LiveResults,
    capture: CaptureState
): Boolean {
    val connected = results.phase != LiveSessionPhase.NOT_CONNECTED
    val finished = !capture.busy && capture.phase != CapturePhase.IDLE
    return !fixture && !(connected && finished)
}
