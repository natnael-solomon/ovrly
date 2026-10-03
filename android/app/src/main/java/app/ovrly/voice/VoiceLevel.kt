package app.ovrly.voice

import kotlin.math.sqrt

/** Little-endian PCM16 loudness. The speech threshold is an untuned starting point. */
internal object VoiceLevel {
    const val SPEECH_RMS = 0.02f
    private const val FULL_SCALE = 32_768.0
    private const val BYTE_MASK = 0xff
    private const val BITS_PER_BYTE = 8

    // Speech RMS rarely exceeds this, so it maps to a full orb level.
    private const val DISPLAY_CEILING = 0.25f

    fun rms(pcm: ByteArray, offset: Int = 0, length: Int = pcm.size): Float {
        val samples = length / 2
        if (samples == 0) return 0f
        var sum = 0.0
        for (index in 0 until samples) {
            val low = pcm[offset + index * 2].toInt() and BYTE_MASK
            val sample = (pcm[offset + index * 2 + 1].toInt() shl BITS_PER_BYTE) or low
            val normalized = sample / FULL_SCALE
            sum += normalized * normalized
        }
        return sqrt(sum / samples).toFloat()
    }

    /** Perceptual 0..1 display level. */
    fun display(rms: Float): Float = sqrt((rms / DISPLAY_CEILING).coerceIn(0f, 1f))
}
