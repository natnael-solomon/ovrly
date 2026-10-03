package app.ovrly.voice

/**
 * In-process protocol fixture: no sockets, credentials, microphone or speech recognition.
 * It completes the handshake and one empty turn, then listens until the silence stop; it never
 * calls an app action.
 */
internal class MockVoiceTransport : VoiceTransport {
    private var closed = false

    override fun start(listener: VoiceTransport.Listener) {
        check(!closed)
        listener.message("""{"type":"ready","sessionId":"offline-fixture"}""")
        if (!closed) listener.message("""{"type":"turn_complete"}""")
    }

    override fun send(text: String): Boolean = !closed

    override fun close() {
        closed = true
    }
}

internal class MockVoiceAudio : VoiceAudio {
    override fun start(
        input: (ByteArray) -> Unit,
        drained: () -> Unit,
        failed: (String) -> Unit,
        output: (Float) -> Unit
    ) = Unit

    override fun enqueue(pcm: ByteArray): Boolean = true

    override fun interrupt() = Unit

    override fun stopInput() = Unit

    override fun close() = Unit
}
