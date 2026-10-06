package app.ovrly.voice

import org.json.JSONObject

/** What an executed product command reports back; [message] is safe to show and to speak. */
internal data class VoiceCommandOutcome(
    val success: Boolean,
    val message: String,
    val code: String? = null
)

/**
 * Runs allowlisted product commands, normally against `POST /v1/voice/actions`. [done] is
 * called exactly once, on any thread.
 */
internal fun interface VoiceCommandExecutor {
    fun execute(command: VoiceCommand, done: (VoiceCommandOutcome) -> Unit)

    companion object {
        /** For builds without an ovrly service: every product command is refused. */
        val UNAVAILABLE = VoiceCommandExecutor { _, done ->
            val error = VoiceCommandError.VOICE_ACTION_UNAVAILABLE
            done(VoiceCommandOutcome(false, error.message, error.name))
        }
    }
}

/** How a session runs product commands; see [VoiceSession.commands]. */
internal class VoiceCommands(
    val executor: VoiceCommandExecutor = VoiceCommandExecutor.UNAVAILABLE,
    /** App state sent with every tool result, so the assistant can name real targets. */
    val state: () -> JSONObject = { JSONObject() },
    /** Called when the session ends for any reason; discards pending confirmations. */
    val ended: () -> Unit = {}
)

/**
 * Runs each provider tool call once and replays the recorded result for retried ids. Main
 * thread only: [post] brings asynchronous outcomes back to it and drops them once the session
 * is no longer current. Every outcome is also reported to the screen through [report].
 */
internal class VoiceActions(
    private val navigator: VoiceNavigator,
    private val diagnostics: VoiceDiagnostics,
    private val executor: VoiceCommandExecutor = VoiceCommandExecutor.UNAVAILABLE,
    private val state: () -> JSONObject = { JSONObject() },
    /** What the screen shows: recognized text and every outcome. */
    val activity: VoiceActivityLog = VoiceActivityLog(),
    private val post: (() -> Unit) -> Unit = { it() }
) {
    private class Call(val tool: VoiceEvent.Tool) {
        var result: String? = null
        val waiting = mutableListOf<(String) -> Unit>()
    }

    private val calls = mutableMapOf<String, Call>()

    /**
     * Sends the tool result through [reply], now or once the backend answers. Returns false,
     * without replying, once the per-session action limit is reached.
     */
    fun respond(tool: VoiceEvent.Tool, finishing: Boolean, reply: (String) -> Unit): Boolean {
        val previous = calls[tool.id]
        when {
            previous != null -> replay(previous, tool, reply)

            calls.size >= MAX_ACTIONS -> return false

            else -> {
                val call = Call(tool).also {
                    calls[tool.id] = it
                    it.waiting += reply
                }
                execute(tool, finishing) { result -> complete(call, result) }
            }
        }
        return true
    }

    private fun replay(previous: Call, tool: VoiceEvent.Tool, reply: (String) -> Unit) {
        if (previous.tool != tool) throw VoiceProtocolException()
        val result = previous.result
        if (result != null) reply(result) else previous.waiting += reply
    }

    private fun complete(call: Call, result: String) {
        call.result = result
        val waiting = call.waiting.toList()
        call.waiting.clear()
        waiting.forEach { it(result) }
    }

    private fun execute(tool: VoiceEvent.Tool, finishing: Boolean, done: (String) -> Unit) {
        when {
            finishing -> done(
                VoiceProtocol.toolResult(
                    tool,
                    false,
                    "The voice session time limit was reached. No new actions run.",
                    state = state()
                )
            )

            tool.name == VoiceProtocol.ACTION -> done(tab(tool))

            else -> when (val command = VoiceCommandContract.validate(tool.name, tool.arguments)) {
                is VoiceCommandValidation.Rejected -> {
                    val error = command.error
                    activity.result(
                        VoiceResult("Voice command", error.message, false, code = error.name)
                    )
                    done(VoiceCommandContract.reject(tool, error, state()))
                }

                is VoiceCommandValidation.Accepted -> {
                    val action = command.action
                    executor.execute(VoiceCommand(action, command.targetId)) { outcome ->
                        post {
                            activity.result(
                                VoiceResult(
                                    action.label,
                                    outcome.message,
                                    outcome.success,
                                    code = outcome.code
                                )
                            )
                            done(
                                VoiceProtocol.toolResult(
                                    tool,
                                    outcome.success,
                                    outcome.message,
                                    outcome.code,
                                    state()
                                )
                            )
                        }
                    }
                }
            }
        }
    }

    private fun tab(tool: VoiceEvent.Tool): String {
        val tab = VoiceTab.from(tool.arguments)
            ?: return VoiceProtocol.toolResult(
                tool,
                false,
                "Only these tabs exist: space (Your space) and explore (Explore).",
                state = state()
            )
        return try {
            val switched = navigator.openTab(tab)
            val shown = if (switched) "Switched to ${tab.label}." else "Already on ${tab.label}."
            val told = if (switched) {
                shown
            } else {
                "The user is already on ${tab.label}. Nothing changed; " +
                    "tell them they are already there."
            }
            activity.result(VoiceResult("Open tab", shown, true))
            VoiceProtocol.toolResult(tool, true, told, state = state())
        } catch (cause: IllegalStateException) {
            diagnostics.warning("Tab navigation unavailable in the current activity state", cause)
            val message = "The app cannot switch tabs right now."
            activity.result(VoiceResult("Open tab", message, false))
            VoiceProtocol.toolResult(tool, false, message, state = state())
        }
    }

    private companion object {
        const val MAX_ACTIONS = 32
    }
}
