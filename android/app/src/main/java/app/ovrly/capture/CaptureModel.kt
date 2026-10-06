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

    /**
     * Length of one chunk on the capture timeline. Chunk `seq` covers
     * `[seq * CHUNK_MS, min((seq + 1) * CHUNK_MS, LIVE_MS))`; the contract allows 1000..30000
     * and RES-02 may tune it.
     */
    const val CHUNK_MS = 10_000L

    /** Playback audio missing for at least this long is recorded as an interruption gap. */
    const val AUDIO_GAP_MS = 1_000L

    fun elapsedSeconds(startMs: Long, nowMs: Long): Int =
        ((nowMs - startMs).coerceIn(0, LIVE_MS) / 1_000).toInt()

    fun expired(startMs: Long, nowMs: Long): Boolean = nowMs - startMs >= LIVE_MS
    fun acceptsSharedDuration(durationMs: Long): Boolean = durationMs in 1..SHARED_MS
    fun fitsStorage(current: Long, incoming: Long): Boolean = current >= 0 &&
        incoming >= 0 &&
        current <= MAX_BYTES &&
        incoming <= MAX_BYTES - current
}

enum class CapturePhase { IDLE, STARTING, RECORDING, FINISHED, ERROR }

/** Where the chunks of the current capture are. */
enum class UploadStatus {
    /** No chunk has been sealed yet. */
    NONE,

    /** Sealed chunks are waiting on the device: offline, backing off or not yet scheduled. */
    PENDING,

    /** An upload attempt is running. */
    SENDING,

    /** Every sealed chunk is sent; the session is still open. */
    SENT,

    /** The session was closed with the user's continuation choice. */
    CLOSED,

    /** Chunks stay on the device and will not be sent; [UploadProgress.detail] says why. */
    NOT_SENT
}

data class UploadProgress(
    val chunks: Int = 0,
    val sent: Int = 0,
    val status: UploadStatus = UploadStatus.NONE,
    val continueResearch: Boolean? = null,
    val detail: String? = null,
    val testServer: Boolean = false,
    val wifiOnly: Boolean = false
) {
    val unsent: Int get() = chunks - sent

    fun summary(): String? {
        val server = if (testServer) " to the in-memory test server" else ""
        return when (status) {
            UploadStatus.NONE -> null

            UploadStatus.PENDING ->
                "Saved on device, not yet sent: $unsent of $chunks chunks." +
                    if (wifiOnly) " Waiting for Wi-Fi." else ""

            UploadStatus.SENDING -> "Sending$server: $sent of $chunks chunks sent."

            UploadStatus.SENT -> "All $chunks chunks sent$server."

            UploadStatus.CLOSED -> if (continueResearch == true) {
                "All $chunks chunks sent$server; research may continue."
            } else {
                "Upload closed; research will not continue. $sent of $chunks chunks were sent."
            }

            UploadStatus.NOT_SENT ->
                "Saved on device, not yet sent: $unsent of $chunks chunks. ${detail.orEmpty()}"
                    .trim()
        }
    }

    companion object {
        internal fun of(
            manifest: LocalManifest,
            ledger: CaptureLedger,
            testServer: Boolean,
            sending: Boolean = false,
            wifiOnly: Boolean = false
        ): UploadProgress {
            val chunks = manifest.chunks.size
            val sent = manifest.chunks.count { ledger.isSent(it.seq) }
            val choice = ledger.choice
            val failure = ledger.failure
            val status = when {
                failure != null -> UploadStatus.NOT_SENT
                ledger.closed || choice == false -> UploadStatus.CLOSED
                chunks == 0 -> UploadStatus.NONE
                sending -> UploadStatus.SENDING
                sent == chunks -> UploadStatus.SENT
                else -> UploadStatus.PENDING
            }
            return UploadProgress(chunks, sent, status, choice, failure, testServer, wifiOnly)
        }
    }
}

data class CaptureState(
    val phase: CapturePhase = CapturePhase.IDLE,
    val seconds: Int = 0,
    val message: String = "No capture yet. Research is not connected.",
    val bytes: Long = 0,
    val frames: Int = 0,
    val playbackSignal: Boolean = false,
    val hasLocalCapture: Boolean = false,
    val upload: UploadProgress = UploadProgress()
) {
    val busy: Boolean get() = phase == CapturePhase.STARTING || phase == CapturePhase.RECORDING

    /** The capture has stopped and the overlay should ask whether research continues. */
    val needsContinuationChoice: Boolean
        get() = !busy &&
            hasLocalCapture &&
            upload.continueResearch == null &&
            upload.status != UploadStatus.NONE &&
            upload.status != UploadStatus.NOT_SENT

    /** [message] followed by the upload summary, for status text. */
    val displayMessage: String
        get() = upload.summary()?.let { "$message $it" } ?: message
}

object CaptureStore {
    private val mutable = MutableStateFlow(CaptureState())
    val state = mutable.asStateFlow()
    fun set(state: CaptureState) {
        mutable.value = state
    }

    fun update(transform: (CaptureState) -> CaptureState) = mutable.update(transform)
    fun message(message: String) = update { it.copy(message = message) }
    fun upload(progress: UploadProgress) = update {
        if (it.busy || it.hasLocalCapture) it.copy(upload = progress) else it
    }
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
