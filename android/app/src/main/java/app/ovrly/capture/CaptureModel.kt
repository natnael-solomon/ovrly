package app.ovrly.capture

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

object CaptureLimits {
    const val LIVE_MS = 180_000L
    const val SHARED_MS = 600_000L
    const val MAX_BYTES = 32L * 1024 * 1024
    const val SAMPLE_RATE = 16_000
    const val FRAME_INTERVAL_MS = 5_000L
    const val FRAME_LONG_EDGE = 720

    fun elapsedSeconds(startMs: Long, nowMs: Long): Int =
        ((nowMs - startMs).coerceIn(0, LIVE_MS) / 1_000).toInt()

    fun expired(startMs: Long, nowMs: Long): Boolean = nowMs - startMs >= LIVE_MS
    fun acceptsSharedDuration(durationMs: Long): Boolean = durationMs in 1..SHARED_MS
    fun fitsStorage(current: Long, incoming: Long): Boolean =
        current >= 0 && incoming >= 0 && current <= MAX_BYTES && incoming <= MAX_BYTES - current
}

enum class CapturePhase { IDLE, STARTING, RECORDING, FINISHED, ERROR }

data class CaptureState(
    val phase: CapturePhase = CapturePhase.IDLE,
    val seconds: Int = 0,
    val message: String = "No capture yet. Research is not connected.",
    val bytes: Long = 0,
    val frames: Int = 0,
    val playbackSignal: Boolean = false,
    val hasLocalCapture: Boolean = false,
) {
    val busy: Boolean get() = phase == CapturePhase.STARTING || phase == CapturePhase.RECORDING
}

object CaptureStore {
    private val mutable = MutableStateFlow(CaptureState())
    val state = mutable.asStateFlow()
    fun set(state: CaptureState) { mutable.value = state }
    fun update(transform: (CaptureState) -> CaptureState) = mutable.update(transform)
    fun message(message: String) = update { it.copy(message = message) }
}

enum class PermissionStep { AUDIO, PROJECTION, ALREADY_RUNNING }

fun nextCapturePermission(audioGranted: Boolean, busy: Boolean): PermissionStep = when {
    busy -> PermissionStep.ALREADY_RUNNING
    !audioGranted -> PermissionStep.AUDIO
    else -> PermissionStep.PROJECTION
}

class CaptureLifecycle {
    enum class Stage { NEW, STARTING, RECORDING, STOPPED }
    var stage = Stage.NEW
        private set

    fun begin(): Boolean {
        if (stage != Stage.NEW) return false
        stage = Stage.STARTING
        return true
    }

    fun recording(): Boolean {
        if (stage != Stage.STARTING) return false
        stage = Stage.RECORDING
        return true
    }

    fun stop(): Boolean {
        if (stage == Stage.STOPPED) return false
        stage = Stage.STOPPED
        return true
    }
}
