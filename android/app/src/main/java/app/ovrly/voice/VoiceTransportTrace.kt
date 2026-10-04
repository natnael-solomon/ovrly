package app.ovrly.voice

/**
 * Counts only fixed event categories. Never retains payloads, IDs, keys or transcripts.
 * Queue acceptance is local evidence, not proof of server receipt or intelligible audio.
 */
internal class VoiceTransportTrace(private val diagnostics: VoiceDiagnostics) {
    private val counts = linkedMapOf<String, Long>()
    private var finished = false

    fun received(text: String) = count("received", category(text))

    fun received(event: VoiceEvent) = count(
        "received",
        when (event) {
            VoiceEvent.Ready -> "ready"
            is VoiceEvent.Audio -> "audio"
            is VoiceEvent.Tool -> "tool_call"
            VoiceEvent.Interrupted -> "interrupted"
            VoiceEvent.TurnComplete -> "turn_complete"
            is VoiceEvent.Error -> "error"
            VoiceEvent.Text -> "text"
            VoiceEvent.Unknown -> "other"
        }
    )

    fun sent(text: String) = count("queued", category(text))

    @Synchronized
    fun finish() {
        if (!finished) {
            finished = true
            val summary = counts.entries.joinToString { "${it.key}=${it.value}" }
            diagnostics.warning("Voice traffic summary: $summary")
        }
    }

    @Synchronized
    private fun count(direction: String, category: String) {
        if (!finished) {
            val key = "$direction/$category"
            val previous = counts[key] ?: 0
            counts[key] = previous + 1
            if (previous == 0L) diagnostics.warning("Voice first $key")
        }
    }

    private fun category(text: String): String {
        val type = try {
            VoiceProtocol.objectFrom(text).opt("type") as? String
        } catch (_: VoiceProtocolException) {
            null
        }
        return when (type) {
            "ready", "audio", "audio_input", "tool_call", "tool_result",
            "text", "text_user", "turn_complete", "interrupted", "interrupt", "error" -> type

            else -> "other"
        }
    }
}
