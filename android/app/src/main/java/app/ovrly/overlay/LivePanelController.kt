package app.ovrly.overlay

import android.content.Context
import android.content.Intent
import app.ovrly.capture.CaptureService
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
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
    /** The large panel is open; otherwise the pill (examining) or the bubble (after Stop). */
    val expanded: Boolean = false,
    val stopPrompt: Boolean = false,
    /** The Stop choice already sent, so the panel can say what happens next. */
    val stopChoice: Boolean? = null,
    /** Claims with an unread "Assessment updated" notice, oldest first. */
    val notices: List<String> = emptyList(),
    val detailClaimId: String? = null,
    /** This capture's one automatic expand has happened or is no longer wanted. */
    val autoExpandSpent: Boolean = false,
    /**
     * The expanded panel collapses on its own after [AUTO_COLLAPSE_MS] untouched. Off while
     * collapsed and while the Stop choice is open.
     */
    val autoCollapsePending: Boolean = false,
    /** Counts touches and new results on the expanded panel; each restarts its collapse timer. */
    val activity: Int = 0,
    /** Something changed while collapsed: the update dot on the pill or bubble. */
    val unseen: Boolean = false
)

/** What the live overlay shows. */
internal enum class LiveOverlayForm {
    /** While examining: mark, timer, claim count, update dot and Stop. */
    PILL,

    /** The large panel with the claims. */
    EXPANDED,

    /** After Stop with research continuing: a small round mark with the claim count. */
    BUBBLE,

    /** After Stop keeping only available results: "Saved to Inbox" before the overlay closes. */
    SAVED
}

/**
 * The form for the current state, or null when there is nothing live to show (no capture and
 * no connected session), where the host keeps its idle controls. [examining] is true while a
 * capture runs (or the fixture plays).
 */
internal fun liveOverlayForm(
    panel: LivePanelState,
    results: LiveResults,
    examining: Boolean
): LiveOverlayForm? {
    val connected = results.phase != LiveSessionPhase.NOT_CONNECTED
    return when {
        panel.stopChoice == false -> LiveOverlayForm.SAVED
        panel.expanded && connected -> LiveOverlayForm.EXPANDED
        examining -> LiveOverlayForm.PILL
        connected -> LiveOverlayForm.BUBBLE
        else -> null
    }
}

/**
 * Research that continued after Stop has settled: every claim is final, failed or cancelled,
 * so the user can be told the results are ready.
 */
internal fun researchSettled(results: LiveResults): Boolean =
    results.phase == LiveSessionPhase.CONTINUING &&
        results.claims.isNotEmpty() &&
        results.claims.all {
            it.complete || it.state == LiveClaimState.FAILED || it.state == LiveClaimState.CANCELLED
        }

/**
 * Which capture session already had its automatic expand (or a user expand or collapse). It
 * outlives the overlay service, so hiding and showing the overlay mid-capture does not
 * expand the panel again.
 */
internal class AutoExpandMemory {
    @Volatile var spentFor: String? = null

    /** [state], spent if [session] already had its automatic expand. */
    fun recall(state: LivePanelState, session: String?): LivePanelState =
        if (session != null && spentFor == session) state.copy(autoExpandSpent = true) else state

    /** [state] with its automatic expand spent, remembered for [session] when there is one. */
    fun spend(state: LivePanelState, session: String?): LivePanelState {
        session?.let { spentFor = it }
        return state.copy(autoExpandSpent = true)
    }
}

/**
 * Panel interaction state for the live overlay. It never stops capture except through
 * [StopChoiceHandler], and only after the user picked one of the two Stop choices.
 */
internal class LivePanelController(
    private val memory: AutoExpandMemory = AutoExpandMemory(),
    private val stopChoice: StopChoiceHandler
) {
    private val mutable = MutableStateFlow(LivePanelState())
    val state: StateFlow<LivePanelState> = mutable.asStateFlow()
    private var last: LiveResults? = null
    private var captureRunning = false

    /** The live session the results belong to; null for the fixture. */
    private var session: String? = null

    /** Forgets notices, choices and the last results, for example when the source changes. */
    fun reset() {
        last = null
        mutable.value = LivePanelState()
    }

    /**
     * Records new assessment notices. The first claims of a capture expand the panel once,
     * unless the user already expanded or collapsed it or the Stop choice is open; anything
     * new while collapsed lights the update dot. [sessionKey] names the live session, so a
     * session that already expanded once does not expand again in a new overlay.
     */
    fun onResults(results: LiveResults, sessionKey: String? = null) {
        session = sessionKey
        mutable.update { memory.recall(it, sessionKey) }
        val before = last?.claims?.size ?: 0
        val fresh = newAssessmentUpdates(last, results).map { it.id }
        last = results
        val grew = results.claims.size > before
        val changed = grew || fresh.isNotEmpty()
        mutable.update {
            val notices = if (fresh.isEmpty()) it.notices else (it.notices - fresh.toSet()) + fresh
            val firstClaims = before == 0 && grew && results.phase == LiveSessionPhase.CAPTURING
            when {
                firstClaims && !it.autoExpandSpent && !it.stopPrompt -> memory.spend(
                    it,
                    session
                ).copy(
                    notices = notices,
                    expanded = true,
                    autoExpandSpent = true,
                    autoCollapsePending = true,
                    unseen = false
                )

                !changed -> it.copy(notices = notices)

                // A change restarts the open panel's collapse timer, or lights the dot.
                it.expanded -> it.copy(notices = notices, activity = it.activity + 1)

                else -> it.copy(notices = notices, unseen = true)
            }
        }
    }

    /**
     * The user opened or closed the panel; either way the automatic expand is no longer
     * wanted. Closing returns to the pill or bubble and never touches capture. An open panel
     * collapses again when left untouched.
     */
    fun setExpanded(expanded: Boolean) = mutable.update {
        memory.spend(it, session).copy(
            expanded = expanded,
            unseen = if (expanded) false else it.unseen,
            detailClaimId = if (expanded) it.detailClaimId else null,
            autoExpandSpent = true,
            autoCollapsePending = expanded && !it.stopPrompt,
            activity = it.activity + 1
        )
    }

    /** Any touch on the expanded panel restarts its collapse timer. */
    fun touched() = mutable.update {
        if (it.expanded) it.copy(activity = it.activity + 1) else it
    }

    /** The expanded panel's timer ran out without a touch or a new result. */
    fun autoCollapse() = mutable.update {
        if (it.autoCollapsePending && it.expanded && !it.stopPrompt) {
            it.copy(expanded = false, detailClaimId = null, autoCollapsePending = false)
        } else {
            it
        }
    }

    /** Opens the Stop choice (only while no choice was sent yet) or closes it ("Keep examining"). */
    fun setStopPrompt(open: Boolean) = mutable.update {
        when {
            !open -> it.copy(
                stopPrompt = false,
                autoCollapsePending = it.expanded,
                activity = it.activity + 1
            )

            it.stopChoice == null -> it.copy(stopPrompt = true, autoCollapsePending = false)

            else -> it
        }
    }

    /** Continuing research collapses to the bubble; keeping available results shows "Saved". */
    fun chooseStop(continueResearch: Boolean) {
        val before = mutable.value
        if (!before.stopPrompt || before.stopChoice != null) return
        mutable.value = before.copy(
            stopPrompt = false,
            stopChoice = continueResearch,
            expanded = false,
            detailClaimId = null,
            autoCollapsePending = false
        )
        stopChoice.onStopChoice(continueResearch)
    }

    /**
     * Capture ended elsewhere (notification Stop, time limit, revoked projection): the prompt
     * no longer applies. Only a new capture (not-running to running) clears the previous
     * choice and the per-capture expand state; repeated running updates while Stop is in
     * flight keep it, so Stop cannot be sent twice.
     */
    fun onCaptureRunning(running: Boolean) {
        val started = running && !captureRunning
        captureRunning = running
        mutable.update {
            when {
                !running -> it.copy(stopPrompt = false)

                started -> it.copy(
                    stopChoice = null,
                    expanded = false,
                    autoExpandSpent = session != null && memory.spentFor == session,
                    autoCollapsePending = false,
                    unseen = false
                )

                else -> it
            }
        }
    }

    /** Opens one claim's detail in the expanded panel, or closes it with null. */
    fun openClaim(claimId: String?) = mutable.update {
        if (claimId == null) {
            it.copy(detailClaimId = null, activity = it.activity + 1)
        } else {
            it.copy(
                expanded = true,
                detailClaimId = claimId,
                notices = it.notices - claimId,
                unseen = false,
                autoCollapsePending = !it.stopPrompt,
                activity = it.activity + 1
            )
        }
    }
    fun dismissNotice(claimId: String) = mutable.update { it.copy(notices = it.notices - claimId) }
}

/**
 * Runs the expanded panel's collapse timer for as long as the caller's scope lives: the panel
 * collapses to the pill (or bubble) after [delayMs] without a touch or a new result, never
 * while the Stop choice is open and never while [touchExploration] (TalkBack) is on.
 */
internal suspend fun runAutoCollapse(
    controller: LivePanelController,
    touchExploration: () -> Boolean,
    delayMs: Long = AUTO_COLLAPSE_MS
) {
    controller.state
        .map { it.autoCollapsePending to it.activity }
        .distinctUntilChanged()
        .collectLatest {
            if (it.first) {
                delay(delayMs)
                if (!touchExploration()) controller.autoCollapse()
            }
        }
}

internal const val AUTO_COLLAPSE_MS = 8_000L

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
