package app.ovrly.voice

import java.net.URI
import java.net.URISyntaxException
import java.util.Base64
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

enum class VoiceInteractionPhase {
    IDLE,
    CONNECTING,
    LISTENING,
    THINKING,
    SPEAKING,
    FINISHING,
    ERROR
}

/** Lossy presentation cues for the voice orb; never use them for control decisions. */
sealed interface VoiceOrbEvent {
    data object Snap : VoiceOrbEvent
    data object Onset : VoiceOrbEvent
    data class Burst(val strength: Float) : VoiceOrbEvent
    data object Interrupt : VoiceOrbEvent
    data object Shake : VoiceOrbEvent
}

data class VoiceState(
    val status: String = "Disabled",
    val message: String = "Experimental voice is not configured. No microphone or network is active.",
    val active: Boolean = false,
    val phase: VoiceInteractionPhase = VoiceInteractionPhase.IDLE
)

internal data class VoiceConfiguration(
    val enabled: Boolean,
    val baseUrl: String,
    val publishableKey: String,
    val mock: Boolean = false
) {
    fun unavailableState(): VoiceState? {
        if (mock) return null
        if (!enabled || publishableKey.isBlank()) return VoiceState()
        val uri = try {
            URI(baseUrl)
        } catch (_: URISyntaxException) {
            null
        }
        if (uri == null || uri.scheme !in setOf("https", "wss") ||
            uri.host.isNullOrBlank() || uri.rawUserInfo != null ||
            uri.rawQuery != null || uri.rawFragment != null ||
            uri.rawPath !in listOf("", "/") || uri.port !in -1..65535 || uri.port == 0 ||
            baseUrl.contains("vox_sk", ignoreCase = true)
        ) {
            return VoiceState(
                "Configuration error",
                "Use an HTTPS or WSS origin without credentials, paths, queries or fragments."
            )
        }
        if (!publishableKey.matches(Regex("vox_pub_[A-Za-z0-9_-]{1,240}")) ||
            publishableKey.contains("vox_sk", ignoreCase = true)
        ) {
            return VoiceState(
                "Configuration error",
                "Only a Voxide publishable key (vox_pub_) is allowed. Never put a secret key in the app."
            )
        }
        return null
    }

    val httpsOrigin: String
        get() = baseUrl.replaceFirst(Regex("^wss:"), "https:").trimEnd('/')

    companion object {
        const val SETUP_MILLIS = 30_000L
        const val INPUT_WINDOW_MILLIS = 300_000L
        const val FINISHING_GRACE_MILLIS = 30_000L
        const val SILENCE_MILLIS = 15_000L
    }
}

internal class VoiceProtocolException : IllegalArgumentException("Invalid Voxide message")

internal sealed interface VoiceEvent {
    data object Ready : VoiceEvent
    data class Audio(val pcm: ByteArray) : VoiceEvent
    data class Tool(
        val id: String,
        val name: String,
        /** Raw args object, or null when args is not an object or the envelope has extra fields. */
        val arguments: String? = null
    ) : VoiceEvent
    data object Interrupted : VoiceEvent
    data object TurnComplete : VoiceEvent
    data class Error(val usageLimit: Boolean) : VoiceEvent

    /**
     * Speech recognized by the provider: [user] text is what the user said (`text_user`),
     * otherwise the assistant's reply. Shown on screen only; never logged or stored.
     */
    data class Text(val user: Boolean, val text: String, val turnComplete: Boolean) :
        VoiceEvent

    data object Unknown : VoiceEvent
}

/**
 * Experimental wire adapter derived from the public @voxide/react 0.8.0 core.js:
 * https://unpkg.com/@voxide/react@0.8.0/dist/core.js
 * Browser SDK evidence is not a provider guarantee of native Android support.
 */
internal object VoiceProtocol {
    const val ACTION = "open_tab"

    /** State key the provider sees with every tool result; see [VoiceTargets]. */
    const val STATE_CHECKS = "checks"
    const val MAX_MESSAGE_BYTES = 96 * 1024
    const val MAX_AUDIO_BYTES = 48_000
    const val MAX_INPUT_BYTES = 640
    private const val TAB_DESCRIPTION =
        "Switch the app to one of its main tabs. These are the ONLY tabs: " +
            "space (Your space, the user's saved reports) and explore (Explore, sample reports). " +
            "If the user asks for anything else, say voice can't open it. " +
            "The result says whether the user was already on that tab; tell them so."
    private const val ID_RULE =
        "Use an id from the app state `checks`, or the word latest for the user's most " +
            "recent check. Never invent an id. Only these actions exist: open_check, " +
            "save_report, queue_cancel, queue_retry, queue_continue and open_tab; voice " +
            "cannot delete, publish or change settings. Repeat the result message to the user."

    /** The server allowlist (BC-D04); each one is sent to `POST /v1/voice/actions`. */
    private fun describe(action: VoiceCommandAction): String = when (action) {
        VoiceCommandAction.OPEN_CHECK ->
            "Open one of the user's checks (a fact-check of a shared video or link). " +
                "id: the check's investigation_id."

        VoiceCommandAction.SAVE_REPORT ->
            "Save a check's report to the user's saved reports. id: the check's report_id."

        VoiceCommandAction.QUEUE_CANCEL ->
            "Cancel a check that is still running. The app asks the user to confirm on " +
                "screen first; do not say it is cancelled unless the result says so. " +
                "id: the check's job_id."

        VoiceCommandAction.QUEUE_RETRY ->
            "Ask the service to retry a check's job. It only works while the job is still " +
                "in progress; the result says when it is not possible. id: the check's job_id."

        VoiceCommandAction.QUEUE_CONTINUE ->
            "Ask the service to continue a check's job. It only works while the job is " +
                "still in progress; the result says when it is not possible. " +
                "id: the check's job_id."
    }
    fun manifest(): String {
        val actions = JSONArray().put(
            JSONObject()
                .put("name", ACTION)
                .put("description", TAB_DESCRIPTION)
                .put(
                    "params",
                    JSONObject().put(
                        "tab",
                        JSONObject()
                            .put("type", "string")
                            .put("required", true)
                            .put("description", "The tab to open. Must be one of the listed tabs.")
                            .put("enum", JSONArray(VoiceTab.entries.map { it.wireName }))
                    )
                )
                .put("scope", "global")
                .put("dangerous", false)
        )
        for (action in VoiceCommandAction.entries) {
            actions.put(
                JSONObject()
                    .put("name", action.wireName)
                    .put("description", "${describe(action)} $ID_RULE")
                    .put(
                        "params",
                        JSONObject().put(
                            "id",
                            JSONObject()
                                .put("type", "string")
                                .put("required", true)
                                .put("description", "An id from the app state, or latest.")
                        )
                    )
                    .put("scope", "global")
                    .put("dangerous", action.requiresConfirmation)
            )
        }
        return JSONObject()
            .put("actions", actions)
            .put("stateSchema", JSONArray().put(STATE_CHECKS))
            .put("environment", "production")
            .toString()
    }

    fun parse(text: String): VoiceEvent {
        val json = objectFrom(text)
        return when (json.string("type", 64)) {
            "ready" -> VoiceEvent.Ready

            "audio" -> {
                val encoded = json.string("data", MAX_AUDIO_BYTES * 4 / 3)
                val pcm = try {
                    Base64.getDecoder().decode(encoded)
                } catch (_: IllegalArgumentException) {
                    throw VoiceProtocolException()
                }
                if (pcm.isEmpty() || pcm.size > MAX_AUDIO_BYTES || pcm.size % 2 != 0 ||
                    Base64.getEncoder().encodeToString(pcm) != encoded
                ) {
                    throw VoiceProtocolException()
                }
                VoiceEvent.Audio(pcm)
            }

            "tool_call" -> {
                val id = json.string("id", 128)
                val name = json.string("name", 64)
                if (!id.matches(Regex("[A-Za-z0-9_.:-]+")) ||
                    !name.matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))
                ) {
                    throw VoiceProtocolException()
                }
                val args = json.opt("args")
                val knownFields = setOf("type", "id", "name", "args")
                val validEnvelope = json.keys().asSequence().all { it in knownFields }
                VoiceEvent.Tool(
                    id,
                    name,
                    if (args is JSONObject && validEnvelope) args.toString() else null
                )
            }

            "interrupted" -> VoiceEvent.Interrupted

            "turn_complete" -> VoiceEvent.TurnComplete

            "text", "text_user" -> {
                val text = json.string("text", 8192)
                if (json.has("turnComplete") && json.opt("turnComplete") !is Boolean) {
                    throw VoiceProtocolException()
                }
                VoiceEvent.Text(
                    user = json.getString("type") == "text_user",
                    text = text,
                    turnComplete = json.optBoolean("turnComplete", false)
                )
            }

            "error" -> VoiceEvent.Error(json.string("message", 2048) == "usage_limit")

            else -> VoiceEvent.Unknown
        }
    }

    fun input(pcm: ByteArray): String {
        require(pcm.isNotEmpty() && pcm.size <= MAX_INPUT_BYTES && pcm.size % 2 == 0)
        return JSONObject().put("type", "audio_input")
            .put("data", Base64.getEncoder().encodeToString(pcm)).toString()
    }

    fun interrupt(): String = JSONObject().put("type", "interrupt").toString()

    fun toolResult(
        tool: VoiceEvent.Tool,
        success: Boolean,
        message: String,
        code: String? = null,
        state: JSONObject = JSONObject()
    ): String {
        val result = JSONObject().put("status", if (success) "success" else "error")
            .put(if (success) "result" else "message", message)
        if (code != null) result.put("code", code)
        return JSONObject().put("type", "tool_result").put("id", tool.id)
            .put("name", tool.name).put("result", result).put("state", state).toString()
    }

    fun objectFrom(text: String): JSONObject {
        if (text.length > MAX_MESSAGE_BYTES ||
            text.toByteArray(Charsets.UTF_8).size > MAX_MESSAGE_BYTES
        ) {
            throw VoiceProtocolException()
        }
        try {
            StrictJson(text).validate()
            return JSONObject(text)
        } catch (_: JSONException) {
            throw VoiceProtocolException()
        }
    }

    private fun JSONObject.string(key: String, limit: Int): String {
        val value = opt(key)
        if (value !is String || value.length > limit) throw VoiceProtocolException()
        return value
    }
}

// Android's JSONObject accepts non-JSON syntax and duplicate keys. Reject both before parsing.
private class StrictJson(private val source: String) {
    private var index = 0
    private var values = 0

    fun validate() {
        whitespace()
        if (peek() != '{') fail()
        value(0)
        whitespace()
        if (index != source.length) fail()
    }

    private fun value(depth: Int) {
        if (depth > 12 || ++values > 512) fail()
        whitespace()
        when (peek()) {
            '{' -> {
                index++
                whitespace()
                val keys = mutableSetOf<String>()
                if (take('}')) return
                do {
                    whitespace()
                    val key = string()
                    if (!keys.add(key)) fail()
                    whitespace()
                    expect(':')
                    value(depth + 1)
                    whitespace()
                    if (take('}')) return
                    expect(',')
                } while (true)
            }

            '[' -> {
                index++
                whitespace()
                if (take(']')) return
                do {
                    value(depth + 1)
                    whitespace()
                    if (take(']')) return
                    expect(',')
                } while (true)
            }

            '"' -> string()

            't' -> literal("true")

            'f' -> literal("false")

            'n' -> literal("null")

            else -> number()
        }
    }

    private fun string(): String {
        val start = index
        expect('"')
        while (index < source.length) {
            val char = source[index++]
            if (char == '"') {
                return JSONArray("[${source.substring(start, index)}]").getString(0)
            }
            if (char.code < 32) fail()
            if (char == '\\') {
                val escape = source.getOrNull(index++) ?: fail()
                if (escape == 'u') {
                    repeat(4) {
                        if (source.getOrNull(index++)?.digitToIntOrNull(16) == null) fail()
                    }
                } else if (escape !in "\"\\/bfnrt") {
                    fail()
                }
            }
        }
        fail()
    }

    private fun number() {
        take('-')
        if (!take('0')) {
            if (peek() !in '1'..'9') fail()
            while (peek() in '0'..'9') index++
        }
        if (take('.')) digits()
        if (take('e') || take('E')) {
            if (!take('+')) take('-')
            digits()
        }
    }

    private fun digits() {
        if (peek() !in '0'..'9') fail()
        while (peek() in '0'..'9') index++
    }

    private fun literal(text: String) {
        if (!source.startsWith(text, index)) fail()
        index += text.length
    }

    private fun whitespace() {
        while (peek() in listOf(' ', '\t', '\r', '\n')) index++
    }

    private fun peek(): Char = source.getOrNull(index) ?: '\u0000'
    private fun take(char: Char): Boolean = if (peek() == char) {
        index++
        true
    } else {
        false
    }
    private fun expect(char: Char) {
        if (!take(char)) fail()
    }
    private fun fail(): Nothing = throw VoiceProtocolException()
}
