package app.ovrly.voice

import org.json.JSONObject

internal enum class VoiceCommandAction(val wireName: String) {
    OPEN_CHECK("open_check"),
    SAVE_REPORT("save_report"),
    QUEUE_CANCEL("queue_cancel"),
    QUEUE_RETRY("queue_retry"),
    QUEUE_CONTINUE("queue_continue");

    val requiresConfirmation: Boolean
        get() = this == QUEUE_CANCEL
}

internal enum class VoiceCommandError(val message: String) {
    VOICE_ACTION_UNSUPPORTED("This voice action is not supported."),
    VOICE_ACTION_INVALID_ARGUMENTS("This action requires exactly one valid string id."),
    VOICE_ACTION_UNAVAILABLE("This voice action is not connected to the app yet.")
}

internal sealed interface VoiceCommandValidation {
    data class Accepted(val action: VoiceCommandAction, val targetId: String) :
        VoiceCommandValidation

    data class Rejected(val error: VoiceCommandError) : VoiceCommandValidation
}

/**
 * Planned product commands, separate from the advertised open_tab action.
 * Validation establishes syntax only, never target existence, ownership or job eligibility.
 */
internal object VoiceCommandContract {
    private const val MAX_ID_LENGTH = 128
    private val idPattern = Regex("[A-Za-z0-9][A-Za-z0-9_-]*")

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

    private fun validId(id: String): Boolean =
        id.length in 1..MAX_ID_LENGTH && idPattern.matches(id)

    fun rejectForCurrentBuild(tool: VoiceEvent.Tool): String {
        val error = when (val command = validate(tool.name, tool.arguments)) {
            is VoiceCommandValidation.Accepted -> VoiceCommandError.VOICE_ACTION_UNAVAILABLE
            is VoiceCommandValidation.Rejected -> command.error
        }
        val envelope = JSONObject(VoiceProtocol.toolResult(tool, false, error.message))
        envelope.getJSONObject("result").put("code", error.name)
        return envelope.toString()
    }
}
