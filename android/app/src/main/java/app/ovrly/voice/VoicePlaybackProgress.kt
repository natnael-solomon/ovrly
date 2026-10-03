package app.ovrly.voice

internal data class VoicePlaybackSample(
    val queuedMillis: Long,
    val noProgressMillis: Long,
    val stalled: Boolean
)

/** Monotonic playback-head measurements, independent of message arrival or write acceptance. */
internal class VoicePlaybackProgress {
    private var lastPlayed = 0L
    private var lastProgressAt: Long? = null

    fun sample(
        queuedBytes: Int,
        writtenFrames: Long,
        playedFrames: Long,
        nowMillis: Long
    ): VoicePlaybackSample {
        val pendingFrames =
            queuedBytes / SAMPLE_BYTES + (writtenFrames - playedFrames).coerceAtLeast(0)
        if (pendingFrames == 0L) {
            lastProgressAt = null
        } else if (lastProgressAt == null || playedFrames != lastPlayed) {
            lastProgressAt = nowMillis
        }
        lastPlayed = playedFrames
        val age = lastProgressAt?.let { (nowMillis - it).coerceAtLeast(0) } ?: 0
        return VoicePlaybackSample(
            pendingFrames * MILLIS_PER_SECOND / SAMPLE_RATE,
            age,
            pendingFrames > 0 && age >= STALL_MILLIS
        )
    }

    fun reset() {
        lastPlayed = 0
        lastProgressAt = null
    }

    companion object {
        const val STALL_MILLIS = 5_000L
        private const val SAMPLE_BYTES = 2
        private const val SAMPLE_RATE = 24_000
        private const val MILLIS_PER_SECOND = 1_000
    }
}
