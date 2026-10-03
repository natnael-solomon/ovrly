package app.ovrly.ui.voice

import kotlin.math.max
import kotlin.math.sin
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private const val BURST_GAP_S = .55f
private const val BURST_LEVEL = .55f
private const val EVENT_BUFFER = 8
private const val FAKE_CONNECT_MS = 1100L
private const val FAKE_REPLY_MS = 3200L
private const val FAKE_SILENCE_MS = 15_000L
private const val FAKE_SPEECH_DELAY_MS = 900L
private const val FAKE_SPEECH_MS = 2400L
private const val FAKE_THINK_MS = 1500L
private const val LEVEL_STEP_MS = 50L
private const val LEVEL_STEP_S = .05f
private const val MIC_BASE = 0.35f
private const val MIC_FREQ_A = 3.1f
private const val MIC_FREQ_B = 7.7f
private const val MIC_WAVE_A = 0.3f
private const val MIC_WAVE_B = 0.2f
private const val REPLY_FREQ_A = 2.1f
private const val REPLY_FREQ_B = 5.3f
private const val REPLY_FREQ_C = 9.7f
private const val REPLY_WAVE_A = 0.5f
private const val REPLY_WAVE_B = 0.3f
private const val REPLY_WAVE_C = 0.2f

/**
 * Scripted stand-in for the voice controller: same API, no microphone, no network. Used by the
 * design gallery previews and as the UI-session fake until the real controller is wired. Plays the
 * canonical turn (connecting → listening → thinking → speaking → listening) with synthetic levels.
 */
class FakeVoiceOrbSource(
    private val scope: CoroutineScope,
    override val configured: Boolean = true
) : VoiceOrbSource {
    private val mutableState =
        MutableStateFlow(VoiceOrbState("Ready", "Tap to talk", false, VoiceInteractionPhase.IDLE))
    private val mutableLevel = MutableStateFlow(0f)
    private val mutableEvents =
        MutableSharedFlow<VoiceOrbEvent>(0, EVENT_BUFFER, BufferOverflow.DROP_OLDEST)
    override val state: StateFlow<VoiceOrbState> = mutableState.asStateFlow()
    override val level: StateFlow<Float> = mutableLevel.asStateFlow()
    override val events: SharedFlow<VoiceOrbEvent> = mutableEvents.asSharedFlow()
    override val requiresMicrophone = false
    private var turn: Job? = null
    private var holding = false

    private fun set(phase: VoiceInteractionPhase, status: String, message: String = "") {
        mutableState.value =
            VoiceOrbState(
                status,
                message,
                phase != VoiceInteractionPhase.IDLE && phase != VoiceInteractionPhase.ERROR,
                phase
            )
    }

    override fun start() {
        if (mutableState.value.active) return
        turn = scope.launch {
            set(VoiceInteractionPhase.CONNECTING, "Connecting")
            delay(FAKE_CONNECT_MS)
            mutableEvents.tryEmit(VoiceOrbEvent.Snap)
            set(VoiceInteractionPhase.LISTENING, "Listening")
            // one scripted exchange, then stay listening until the 15 s silence stop
            delay(FAKE_SPEECH_DELAY_MS)
            mutableEvents.tryEmit(VoiceOrbEvent.Onset)
            levelFor(FAKE_SPEECH_MS) {
                MIC_BASE + MIC_WAVE_A * sin(it * MIC_FREQ_A) +
                    MIC_WAVE_B * sin(it * MIC_FREQ_B)
            }
            mutableEvents.tryEmit(VoiceOrbEvent.Snap)
            set(VoiceInteractionPhase.THINKING, "Thinking")
            delay(FAKE_THINK_MS)
            set(VoiceInteractionPhase.SPEAKING, "Speaking")
            var lastBurst = 0f
            levelFor(FAKE_REPLY_MS) { t ->
                val env =
                    max(
                        0f,
                        REPLY_WAVE_A * sin(
                            t * REPLY_FREQ_A
                        ) + REPLY_WAVE_B * sin(t * REPLY_FREQ_B) +
                            REPLY_WAVE_C * sin(t * REPLY_FREQ_C)
                    )
                if (env > BURST_LEVEL &&
                    t - lastBurst > BURST_GAP_S
                ) {
                    mutableEvents.tryEmit(VoiceOrbEvent.Burst(env))
                    lastBurst =
                        t
                }
                env
            }
            mutableEvents.tryEmit(VoiceOrbEvent.Snap)
            set(VoiceInteractionPhase.LISTENING, "Listening")
            delay(FAKE_SILENCE_MS)
            set(VoiceInteractionPhase.IDLE, "Session ended", "Voice ended after silence.")
        }
    }

    private suspend fun levelFor(ms: Long, f: (Float) -> Float) {
        val steps = ms / LEVEL_STEP_MS
        for (i in 0 until steps) {
            mutableLevel.value = f(i * LEVEL_STEP_S).coerceIn(0f, 1f)
            delay(LEVEL_STEP_MS)
        }
        mutableLevel.value = 0f
    }

    override fun stop() {
        turn?.cancel()
        turn = null
        holding = false
        mutableLevel.value = 0f
        set(VoiceInteractionPhase.IDLE, "Stopped", "Voice stopped.")
    }

    override fun holdStart() {
        if (!mutableState.value.active) {
            start()
            holding = true
            return
        }
        if (mutableState.value.phase == VoiceInteractionPhase.SPEAKING) return
        holding = true
    }

    override fun holdEnd() {
        if (holding &&
            mutableState.value.phase == VoiceInteractionPhase.CONNECTING
        ) {
            stop()
            set(VoiceInteractionPhase.IDLE, "Voice start cancelled.", "")
        }
        holding = false
    }

    override fun interrupt() {
        if (mutableState.value.phase != VoiceInteractionPhase.SPEAKING) return
        mutableEvents.tryEmit(VoiceOrbEvent.Interrupt)
        mutableLevel.value = 0f
        set(VoiceInteractionPhase.LISTENING, "Listening", "Assistant interrupted.")
    }

    fun fail(message: String = "Connection timed out") {
        turn?.cancel()
        turn = null
        mutableLevel.value = 0f
        set(VoiceInteractionPhase.ERROR, message, "Voxide did not become ready.")
        mutableEvents.tryEmit(VoiceOrbEvent.Shake)
    }
}
