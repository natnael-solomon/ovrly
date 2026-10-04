package app.ovrly.voice

/** Parses on the serialized transport callback; posts bounded batches, never one task per frame. */
internal class VoiceInbox(
    private val dispatch: (() -> Unit) -> Unit,
    private val receive: (VoiceEvent) -> Unit,
    private val fail: (String, String) -> Unit,
    private val diagnostics: VoiceDiagnostics,
    private val capacity: Int = CAPACITY
) : AutoCloseable {
    private data class Pending(val event: VoiceEvent, val charge: Int)

    private val lock = Any()
    private val queue = ArrayDeque<Pending>()
    private val trace = VoiceTransportTrace(diagnostics)
    private var bytes = 0
    private var peakBytes = 0
    private var peakMessages = 0
    private var scheduled = false
    private var closed = false
    private var failure: Pair<String, String>? = null

    fun message(text: String) {
        val post = synchronized(lock) {
            if (closed || failure != null) return
            try {
                val event = VoiceProtocol.parse(text)
                val charge =
                    text.length * 2 + (if (event is VoiceEvent.Audio) event.pcm.size else 0) +
                        EVENT_OVERHEAD
                if (charge > capacity - bytes || queue.size >= MAX_MESSAGES) {
                    diagnostics.warning("Voice inbox full: bytes=$bytes, messages=${queue.size}")
                    failure =
                        "Message limit reached" to "Incoming voice data exceeded its memory limit."
                    queue.clear()
                    bytes = 0
                } else {
                    queue.addLast(Pending(event, charge))
                    bytes += charge
                    peakBytes = maxOf(peakBytes, bytes)
                    peakMessages = maxOf(peakMessages, queue.size)
                    trace.received(event)
                }
            } catch (cause: VoiceProtocolException) {
                diagnostics.warning("Rejected incoming voice protocol message", cause)
                failure =
                    "Protocol error" to
                    "Voxide sent an invalid or oversized message. Voice stopped safely."
                queue.clear()
                bytes = 0
            }
            if (scheduled) {
                false
            } else {
                scheduled = true
                true
            }
        }
        if (post) dispatch(::drain)
    }

    private fun drain() {
        repeat(BATCH_SIZE) {
            val next = synchronized(lock) {
                if (closed) return
                failure?.let {
                    failure = null
                    close()
                    return fail(it.first, it.second)
                }
                queue.removeFirstOrNull()
            }
            if (next != null) {
                try {
                    receive(next.event)
                } finally {
                    synchronized(lock) { if (!closed && failure == null) bytes -= next.charge }
                }
            }
        }
        val again = synchronized(lock) {
            if (closed) {
                false
            } else if (queue.isEmpty() && failure == null) {
                scheduled = false
                false
            } else {
                true
            }
        }
        if (again) dispatch(::drain)
    }

    override fun close() = synchronized(lock) {
        if (!closed) {
            closed = true
            queue.clear()
            bytes = 0
            failure = null
            trace.finish()
            diagnostics.warning("Voice inbox peak: bytes=$peakBytes, messages=$peakMessages")
        }
    }

    companion object {
        const val CAPACITY = 16_000_000
        private const val MAX_MESSAGES = 8192
        private const val BATCH_SIZE = 4
        private const val EVENT_OVERHEAD = 512
    }
}
