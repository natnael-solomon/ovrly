package app.ovrly.voice

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** One action outcome as the user sees it; [message] is also what the assistant is told. */
internal data class VoiceResult(
    val label: String,
    val message: String,
    val success: Boolean,
    val typed: Boolean = false,
    val code: String? = null,
    /** Sent and not answered yet; not a failure. */
    val pending: Boolean = false
)

/**
 * What the voice panel shows: the last text recognized from the user's speech and the last
 * action result. Held in memory for the screen only; never logged, stored or uploaded.
 */
internal data class VoiceActivity(val heard: String? = null, val result: VoiceResult? = null)

internal class VoiceActivityLog {
    private val mutable = MutableStateFlow(VoiceActivity())
    val state: StateFlow<VoiceActivity> = mutable.asStateFlow()

    // The provider sends a user turn in pieces; main thread only.
    private var pending = ""

    fun userText(piece: String, turnComplete: Boolean) {
        pending = (pending + piece).takeLast(MAX_HEARD)
        val heard = pending.trim()
        if (heard.isNotEmpty()) mutable.update { it.copy(heard = heard) }
        if (turnComplete) pending = ""
    }

    fun turnComplete() {
        pending = ""
    }

    /** Thread-safe; typed commands report from a background thread. */
    fun result(result: VoiceResult) = mutable.update { it.copy(result = result) }

    fun clear() {
        pending = ""
        mutable.value = VoiceActivity()
    }

    private companion object {
        const val MAX_HEARD = 280
    }
}
