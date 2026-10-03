package app.ovrly.voice

/** The input window, finishing grace, silence stop and thinking hint for one connection. */
internal class VoiceTurnTimers(
    private val schedule: (Long, () -> Unit) -> VoiceCancellation,
    private val onSilence: () -> Unit
) {
    private var window: VoiceCancellation? = null
    private var grace: VoiceCancellation? = null
    private var silence: VoiceCancellation? = null
    private var thinking: VoiceCancellation? = null

    fun inputWindow(onExpired: () -> Unit) {
        window?.cancel()
        window = schedule(VoiceConfiguration.INPUT_WINDOW_MILLIS, onExpired)
    }

    fun grace(onExpired: () -> Unit) {
        grace?.cancel()
        grace = schedule(VoiceConfiguration.FINISHING_GRACE_MILLIS, onExpired)
    }

    /** Restarts the silence countdown when [enabled]; otherwise leaves it stopped. */
    fun armSilence(enabled: Boolean) {
        cancelSilence()
        if (enabled) silence = schedule(VoiceConfiguration.SILENCE_MILLIS, onSilence)
    }

    fun cancelSilence() {
        silence?.cancel()
        silence = null
    }

    fun thinking(onQuiet: () -> Unit) {
        cancelThinking()
        thinking = schedule(THINKING_QUIET_MILLIS) {
            thinking = null
            onQuiet()
        }
    }

    fun cancelThinking() {
        thinking?.cancel()
        thinking = null
    }

    fun cancelAll() {
        listOfNotNull(window, grace, silence, thinking).forEach { it.cancel() }
        window = null
        grace = null
        silence = null
        thinking = null
    }

    private companion object {
        const val THINKING_QUIET_MILLIS = 600L
    }
}
