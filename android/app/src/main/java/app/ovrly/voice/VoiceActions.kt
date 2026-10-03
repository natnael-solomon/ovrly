package app.ovrly.voice

/** Runs each provider tool call once and replays the recorded result for retried ids. */
internal class VoiceActions(
    private val navigator: VoiceNavigator,
    private val diagnostics: VoiceDiagnostics
) {
    private val results = mutableMapOf<String, Pair<VoiceEvent.Tool, String>>()

    /** Returns the tool result to send, or null once the per-session action limit is reached. */
    fun respond(tool: VoiceEvent.Tool, finishing: Boolean): String? {
        val previous = results[tool.id]
        if (previous != null && previous.first != tool) throw VoiceProtocolException()
        return when {
            previous != null -> previous.second
            results.size >= MAX_ACTIONS -> null
            else -> execute(tool, finishing).also { results[tool.id] = tool to it }
        }
    }

    private fun execute(tool: VoiceEvent.Tool, finishing: Boolean): String = when {
        finishing ->
            VoiceProtocol.toolResult(
                tool,
                false,
                "The voice session time limit was reached. No new actions run."
            )

        tool.name != VoiceProtocol.ACTION ->
            VoiceCommandContract.rejectForCurrentBuild(tool)

        else -> VoiceTab.from(tool.arguments)?.let { openTab(tool, it) }
            ?: VoiceProtocol.toolResult(
                tool,
                false,
                "Only these tabs exist: space (Your space) and explore (Explore)."
            )
    }

    private fun openTab(tool: VoiceEvent.Tool, tab: VoiceTab): String = try {
        if (navigator.openTab(tab)) {
            VoiceProtocol.toolResult(tool, true, "Switched to ${tab.label}.")
        } else {
            VoiceProtocol.toolResult(
                tool,
                true,
                "The user is already on ${tab.label}. Nothing changed; tell them they are already there."
            )
        }
    } catch (cause: IllegalStateException) {
        diagnostics.warning("Tab navigation unavailable in the current activity state", cause)
        VoiceProtocol.toolResult(tool, false, "The app cannot switch tabs right now.")
    }

    private companion object {
        const val MAX_ACTIONS = 32
    }
}
