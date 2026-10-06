package app.ovrly.voice

import org.json.JSONObject

internal enum class VoiceCommandAction(val wireName: String, val label: String) {
    OPEN_CHECK("open_check", "Open check"),
    SAVE_REPORT("save_report", "Save report"),
    QUEUE_CANCEL("queue_cancel", "Cancel check"),
    QUEUE_RETRY("queue_retry", "Retry check"),
    QUEUE_CONTINUE("queue_continue", "Continue check");

    val requiresConfirmation: Boolean
        get() = this == QUEUE_CANCEL
}

internal enum class VoiceCommandError(val message: String) {
    VOICE_ACTION_UNSUPPORTED(
        "Voice can open a check, save a report, or cancel, retry or continue a check. " +
            "It cannot do that."
    ),
    VOICE_ACTION_INVALID_ARGUMENTS("This action requires exactly one valid string id."),
    VOICE_ACTION_UNAVAILABLE(
        "This build has no ovrly service address, so voice actions cannot run."
    )
}

internal sealed interface VoiceCommandValidation {
    data class Accepted(val action: VoiceCommandAction, val targetId: String) :
        VoiceCommandValidation

    data class Rejected(val error: VoiceCommandError) : VoiceCommandValidation
}

/** A validated product command; [target] is an opaque id or [VoiceCommandContract.LATEST]. */
internal data class VoiceCommand(val action: VoiceCommandAction, val target: String)

/**
 * The BC-D04 allowlist the client accepts from the provider or from a typed command.
 * Validation establishes syntax only, never target existence, ownership or job eligibility:
 * the server decides those through `POST /v1/voice/actions`.
 */
internal object VoiceCommandContract {
    /** Target alias resolved on the device to the most recent check; never sent to the server. */
    const val LATEST = "latest"
    private const val MAX_ID_LENGTH = 128
    private val idPattern = Regex("[A-Za-z0-9][A-Za-z0-9_-]*")

    /** Typed verbs, so the same actions work without a voice session. */
    private val verbs = mapOf(
        "open" to VoiceCommandAction.OPEN_CHECK,
        "save" to VoiceCommandAction.SAVE_REPORT,
        "cancel" to VoiceCommandAction.QUEUE_CANCEL,
        "retry" to VoiceCommandAction.QUEUE_RETRY,
        "continue" to VoiceCommandAction.QUEUE_CONTINUE
    )

    const val TYPED_HINT =
        "Type open, save, cancel, retry or continue, optionally followed by an id. " +
            "Without an id it uses your latest check."

    fun validate(name: String, arguments: String?): VoiceCommandValidation {
        val action = VoiceCommandAction.entries.singleOrNull { it.wireName == name }
            ?: return VoiceCommandValidation.Rejected(VoiceCommandError.VOICE_ACTION_UNSUPPORTED)
        val args = try {
            arguments?.let(VoiceProtocol::objectFrom)
        } catch (_: VoiceProtocolException) {
            null
        }
        val id = args?.opt("id")
        return if (args?.length() == 1 && id is String && validId(id)) {
            VoiceCommandValidation.Accepted(action, id)
        } else {
            VoiceCommandValidation.Rejected(
                VoiceCommandError.VOICE_ACTION_INVALID_ARGUMENTS
            )
        }
    }

    /**
     * Parses a typed command: a verb, then an optional id or `latest`. Verbs are matched
     * without case; ids are opaque and case-sensitive, so they are never normalized.
     */
    fun parseTyped(text: String): VoiceCommand? {
        val words = text.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val action = words.firstOrNull()?.lowercase()?.let(verbs::get)
        val target = when (words.size) {
            1 -> LATEST
            2 -> words[1].takeUnless { it.equals(LATEST, ignoreCase = true) } ?: LATEST
            else -> null
        }
        return if (action != null && target != null && validId(target)) {
            VoiceCommand(action, target)
        } else {
            null
        }
    }

    private fun validId(id: String): Boolean =
        id.length in 1..MAX_ID_LENGTH && idPattern.matches(id)

    /** The tool result for a command the client refuses before any request is made. */
    fun reject(tool: VoiceEvent.Tool, error: VoiceCommandError, state: JSONObject): String =
        VoiceProtocol.toolResult(tool, false, error.message, error.name, state)
}
