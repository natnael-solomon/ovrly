package app.ovrly.ui.voice

import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * The orb's view of the voice engine. Mirrors the public surface of
 * `app.ovrly.voice.VoiceController` as agreed with the voice session; the real controller is
 * adapted to this interface so the UI never depends on engine internals. See docs in
 * `VoiceOrbDock` for the interaction contract.
 */
interface VoiceOrbSource {
    val state: StateFlow<VoiceOrbState>

    /** 0..1, coalesced ≤20 Hz. Mic RMS while LISTENING, playback RMS while SPEAKING, 0 otherwise. */
    val level: StateFlow<Float>

    /** Lossy presentation cues (replay 0, buffer 8, drop oldest). Never command or
     * confirmation delivery. */
    val events: SharedFlow<VoiceOrbEvent>

    val configured: Boolean
    val requiresMicrophone: Boolean

    fun start()
    fun stop()

    /**
     * Push-to-talk. While idle this starts the session; ending before ready cancels.
     * After ready, ending resumes continuous listening. Ignored while SPEAKING.
     */
    fun holdStart()
    fun holdEnd()

    /** User barge-in while SPEAKING. */
    fun interrupt()
}

/** Engine interaction phase. Muted is UI-derived (not configured, or microphone
 * permission missing). */
enum class VoiceInteractionPhase {
    IDLE,
    CONNECTING,
    LISTENING,
    THINKING,
    SPEAKING,
    FINISHING,
    ERROR
}

data class VoiceOrbState(
    val status: String,
    val message: String,
    val active: Boolean,
    val phase: VoiceInteractionPhase
)

sealed interface VoiceOrbEvent {
    /** Ready, turn complete, tool result sent. */
    data object Snap : VoiceOrbEvent

    /** Speech start detected. */
    data object Onset : VoiceOrbEvent

    /** Assistant audio chunk, ≥500 ms apart. */
    data class Burst(val strength: Float) : VoiceOrbEvent

    data object Interrupt : VoiceOrbEvent

    /** Session ended in error. Normal ends do not shake. */
    data object Shake : VoiceOrbEvent
}
