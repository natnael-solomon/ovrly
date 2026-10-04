package app.ovrly.voice

/** PCM16 staging buffer. The audio owner serializes offer/write/clear with its lock. */
internal class VoicePlaybackBuffer(val capacity: Int = CAPACITY) {
    init {
        require(capacity in 2..CAPACITY && capacity % 2 == 0)
    }

    private val bytes = ByteArray(capacity)
    private var head = 0
    var size: Int = 0
        private set

    fun offer(pcm: ByteArray): Boolean {
        if (pcm.isEmpty() || pcm.size % 2 != 0 || pcm.size > capacity - size) return false
        val tail = (head + size) % capacity
        val first = minOf(pcm.size, capacity - tail)
        pcm.copyInto(bytes, tail, 0, first)
        pcm.copyInto(bytes, 0, first, pcm.size)
        size += pcm.size
        return true
    }

    fun write(maxBytes: Int, writer: (ByteArray, Int, Int) -> Int): Int {
        require(maxBytes > 0 && maxBytes % 2 == 0)
        if (size == 0) return 0
        val length = minOf(maxBytes, size, capacity - head)
        val written = writer(bytes, head, length)
        check(written in 0..length && written % 2 == 0)
        bytes.fill(0, head, head + written)
        head = (head + written) % capacity
        size -= written
        return written
    }

    fun clear() {
        bytes.fill(0)
        head = 0
        size = 0
    }

    companion object {
        // About 3.5 minutes of 24 kHz PCM16 speech.
        const val CAPACITY = 10_000_000
    }
}
