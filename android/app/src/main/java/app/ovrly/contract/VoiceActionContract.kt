package app.ovrly.contract

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Typed models for `packages/contracts` version 0.1.0-draft, voice-actions slice
 * (`POST /v1/voice/actions`). Field names and limits follow the JSON Schemas under
 * `packages/contracts/schemas`; constructors enforce the schema so an instance that
 * exists is one the contract allows. Parse and encode with [VoiceActionCodec].
 */

/** Opaque identifier syntax shared by `request_id` and `target.id`. */
internal object OpaqueId {
    const val MAX_LENGTH = 128
    private val pattern = Regex("[A-Za-z0-9][A-Za-z0-9_-]*")

    fun isValid(value: String): Boolean = value.length in 1..MAX_LENGTH && pattern.matches(value)

    fun require(field: String, value: String) {
        require(isValid(value)) {
            "$field must be 1 to $MAX_LENGTH ASCII letters, digits, '_' or '-', " +
                "starting with a letter or digit"
        }
    }
}

/** Resource type a target id refers to. [UNKNOWN] covers values this version does not define. */
internal enum class VoiceTargetKind(val wireName: String) {
    INVESTIGATION("investigation"),
    REPORT("report"),
    JOB("job"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): VoiceTargetKind = known(entries, name) { it.wireName }
    }
}

/**
 * Allowlisted voice actions (BC-D04). [UNKNOWN] covers any other action name; it is never
 * sent and never counts as accepted. The client-local `switch_tab` is deliberately absent.
 */
internal enum class VoiceAction(val wireName: String, val targetKind: VoiceTargetKind) {
    OPEN_CHECK("open_check", VoiceTargetKind.INVESTIGATION),
    SAVE_REPORT("save_report", VoiceTargetKind.REPORT),
    QUEUE_CANCEL("queue_cancel", VoiceTargetKind.JOB),
    QUEUE_RETRY("queue_retry", VoiceTargetKind.JOB),
    QUEUE_CONTINUE("queue_continue", VoiceTargetKind.JOB),
    UNKNOWN("", VoiceTargetKind.UNKNOWN);

    companion object {
        fun fromWire(name: String): VoiceAction = known(entries, name) { it.wireName }
    }
}

/** Outcome of a voice action. [UNKNOWN] is not success; callers must not treat it as accepted. */
internal enum class VoiceActionResult(val wireName: String) {
    ACCEPTED("accepted"),
    DENIED("denied"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): VoiceActionResult = known(entries, name) { it.wireName }
    }
}

/** Error codes a denied voice action may carry. [UNKNOWN] covers codes added by a newer server. */
internal enum class VoiceActionErrorCode(val wireName: String) {
    VOICE_ACTION_UNSUPPORTED("VOICE_ACTION_UNSUPPORTED"),
    VOICE_TARGET_NOT_FOUND("VOICE_TARGET_NOT_FOUND"),
    VOICE_TARGET_NOT_OWNED("VOICE_TARGET_NOT_OWNED"),
    VOICE_ACTION_INVALID_STATE("VOICE_ACTION_INVALID_STATE"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): VoiceActionErrorCode = known(entries, name) { it.wireName }
    }
}

/** Every contract enum lists its UNKNOWN fallback last; only exact wire names match. */
private inline fun <E : Enum<E>> known(entries: List<E>, name: String, wireName: (E) -> String): E {
    val unknown = entries.last()
    return entries.firstOrNull { it != unknown && wireName(it) == name } ?: unknown
}

/** `target` object: the single resource an action applies to. Syntax only, never existence. */
@Serializable
internal data class VoiceTarget(
    @Serializable(with = VoiceTargetKindSerializer::class)
    val kind: VoiceTargetKind,
    val id: String
) {
    init {
        OpaqueId.require("target.id", id)
    }
}

/** What the client may do about an error. [UNKNOWN] covers values this version does not define. */
internal enum class ContractErrorAction(val wireName: String) {
    NONE("none"),
    RETRY("retry"),
    AUTHENTICATE("authenticate"),
    FIX_REQUEST("fix_request"),
    UPLOAD_AGAIN("upload_again"),
    UNKNOWN("");

    companion object {
        fun fromWire(name: String): ContractErrorAction = known(entries, name) { it.wireName }
    }
}

/**
 * Shared error shape from `error.schema.json`: `code`, `message`, `retryable`, `action` and
 * `request_id`. [code] keeps the wire string so endpoints with other code sets can reuse it;
 * voice responses expose the typed value through [VoiceActionResponse.errorCode].
 */
@Serializable
internal data class ContractError(
    val code: String,
    val message: String,
    @Serializable(with = StrictBooleanSerializer::class)
    val retryable: Boolean,
    @Serializable(with = ContractErrorActionSerializer::class)
    val action: ContractErrorAction,
    @SerialName("request_id")
    val requestId: String
) {
    init {
        require(code.length in MIN_CODE_LENGTH..MAX_CODE_LENGTH && codePattern.matches(code)) {
            "error.code must be $MIN_CODE_LENGTH to $MAX_CODE_LENGTH SCREAMING_SNAKE_CASE chars"
        }
        require(message.length in 1..MAX_MESSAGE_LENGTH) {
            "error.message must be 1 to $MAX_MESSAGE_LENGTH characters"
        }
        OpaqueId.require("error.request_id", requestId)
    }

    companion object {
        private const val MIN_CODE_LENGTH = 3
        private const val MAX_CODE_LENGTH = 64
        private const val MAX_MESSAGE_LENGTH = 240
        private val codePattern = Regex("[A-Z][A-Z0-9_]*")
    }
}

/** Request body. Exactly one allowlisted action against one target whose kind matches it. */
@Serializable
internal data class VoiceActionRequest(
    @SerialName("request_id")
    val requestId: String,
    @Serializable(with = VoiceActionSerializer::class)
    val action: VoiceAction,
    val target: VoiceTarget
) {
    init {
        OpaqueId.require("request_id", requestId)
        require(action != VoiceAction.UNKNOWN) { "action is outside the voice allowlist" }
        require(target.kind == action.targetKind) {
            "target.kind must be ${action.targetKind.wireName} for ${action.wireName}"
        }
    }
}

/**
 * Response body. [action] keeps the wire string because a denial echoes whatever name was
 * requested; [knownAction] maps it to the allowlist. An accepted result requires an
 * allowlisted action, a target and no error; a denied result requires an error. An
 * [VoiceActionResult.UNKNOWN] result keeps the rest of the payload and is never success.
 */
@Serializable
internal data class VoiceActionResponse(
    @SerialName("request_id")
    val requestId: String,
    @Serializable(with = VoiceActionResultSerializer::class)
    val result: VoiceActionResult,
    val action: String,
    val message: String,
    val target: VoiceTarget? = null,
    val error: ContractError? = null
) {
    val knownAction: VoiceAction
        get() = VoiceAction.fromWire(action)

    val errorCode: VoiceActionErrorCode?
        get() = error?.let { VoiceActionErrorCode.fromWire(it.code) }

    val isAccepted: Boolean
        get() = result == VoiceActionResult.ACCEPTED

    init {
        OpaqueId.require("request_id", requestId)
        require(action.length in 1..MAX_ACTION_LENGTH) {
            "action must be 1 to $MAX_ACTION_LENGTH characters"
        }
        require(message.length in 1..MAX_MESSAGE_LENGTH) {
            "message must be 1 to $MAX_MESSAGE_LENGTH characters"
        }
        require(error == null || error.requestId == requestId) {
            "error.request_id must echo the response request_id"
        }
        when (result) {
            VoiceActionResult.ACCEPTED -> {
                require(knownAction != VoiceAction.UNKNOWN) {
                    "an accepted response must name an allowlisted action"
                }
                require(target != null) { "an accepted response must echo the target" }
                require(error == null) { "an accepted response must not carry an error" }
            }

            VoiceActionResult.DENIED ->
                require(error != null) { "a denied response must carry an error" }

            VoiceActionResult.UNKNOWN -> Unit
        }
    }

    companion object {
        private const val MAX_ACTION_LENGTH = 64
        private const val MAX_MESSAGE_LENGTH = 200
    }
}
