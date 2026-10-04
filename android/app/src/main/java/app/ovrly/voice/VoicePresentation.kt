package app.ovrly.voice

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/** Rate-limited orb level and lossy cues. Main-thread only; state never depends on it. */
internal class VoicePresentation(private val clockMillis: () -> Long) {
    private val mutableLevel = MutableStateFlow(0f)
    val level: StateFlow<Float> = mutableLevel.asStateFlow()
    private val mutableEvents = MutableSharedFlow<VoiceOrbEvent>(
        extraBufferCapacity = EVENT_BUFFER,
        onBufferOverflow = BufferOverflow.DROP_OLDEST
    )
    val events: SharedFlow<VoiceOrbEvent> = mutableEvents.asSharedFlow()
    private var lastLevelAt: Long? = null
    private var lastBurstAt: Long? = null

    fun level(rms: Float) {
        val now = clockMillis()
        if (lastLevelAt?.let { now - it < LEVEL_INTERVAL_MILLIS } == true) return
        lastLevelAt = now
        mutableLevel.value = VoiceLevel.display(rms)
    }

    fun audio(pcm: ByteArray) {
        val now = clockMillis()
        if (lastBurstAt?.let { now - it < BURST_INTERVAL_MILLIS } == true) return
        lastBurstAt = now
        emit(VoiceOrbEvent.Burst(VoiceLevel.display(VoiceLevel.rms(pcm))))
    }

    fun emit(event: VoiceOrbEvent) {
        mutableEvents.tryEmit(event)
    }

    fun quiet() {
        lastLevelAt = null
        mutableLevel.value = 0f
    }

    fun reset() {
        quiet()
        lastBurstAt = null
    }

    private companion object {
        const val LEVEL_INTERVAL_MILLIS = 50L
        const val BURST_INTERVAL_MILLIS = 500L
        const val EVENT_BUFFER = 8
    }
}
