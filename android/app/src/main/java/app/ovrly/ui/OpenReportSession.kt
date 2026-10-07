package app.ovrly.ui

import app.ovrly.contract.Investigation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Which real report is open and the last loaded view of it (#34). Loading waits on network
 * calls, so every open, close and version change starts a new generation, and a load that
 * started in an earlier generation is dropped: a closed report never reopens and a slow
 * load never replaces a newer one. Call from one thread (the main thread).
 */
internal class OpenReportSession(
    private val load: suspend (Investigation, Int?, List<InboxItem>) -> OpenReport
) {
    private val mutable = MutableStateFlow<OpenReport?>(null)
    private var generation = 0L
    private var shownVersion: Int? = null

    val report: StateFlow<OpenReport?> = mutable.asStateFlow()

    /** Server id of the open investigation, or null when no report is open. */
    var openId: String? = null
        private set

    fun open(id: String) {
        openId = id
        shownVersion = null
        restart()
    }

    fun close() {
        openId = null
        shownVersion = null
        restart()
    }

    /**
     * Shows a saved copy (#36) whose check is not on this device. It has no server id to load,
     * so [refresh] leaves it alone until it is closed or another report opens.
     */
    fun showCopy(report: OpenReport) {
        openId = null
        shownVersion = null
        generation++
        mutable.value = report
    }

    fun show(version: Int) {
        shownVersion = version
        generation++
    }

    fun update(transform: (OpenReport) -> OpenReport) {
        mutable.update { it?.let(transform) }
    }

    /**
     * Loads the open report from [investigation], the latest stored read of [openId]. Nothing
     * is shown while it has not been stored yet.
     */
    suspend fun refresh(investigation: Investigation?, candidates: List<InboxItem>, busy: Boolean) {
        val started = generation
        val id = openId ?: return
        val loaded = if (investigation?.id == id) {
            load(investigation, shownVersion, candidates)
        } else {
            null
        }
        if (generation == started) {
            mutable.value = loaded?.copy(busy = busy, notice = mutable.value?.notice)
        }
    }

    private fun restart() {
        generation++
        mutable.value = null
    }
}
