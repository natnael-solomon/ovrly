package app.ovrly.voice

import java.net.URI
import java.net.URISyntaxException
import java.util.Base64
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

data class VoiceState(
    val status: String = "Disabled",
    val message: String = "Experimental voice is not configured. No microphone or network is active.",
    val active: Boolean = false,
)

internal data class VoiceConfiguration(
    val enabled: Boolean,
    val baseUrl: String,
    val publishableKey: String,
) {
    fun unavailableState(): VoiceState? {
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
            return VoiceState("Configuration error", "Use an HTTPS or WSS origin without credentials, paths, queries or fragments.")
        }
        if (!publishableKey.matches(Regex("vox_pub_[A-Za-z0-9_-]{1,240}")) ||
            publishableKey.contains("vox_sk", ignoreCase = true)
        ) {
            return VoiceState("Configuration error", "Only a Voxide publishable key (vox_pub_) is allowed. Never put a secret key in the app.")
        }
        return null
    }

    val httpsOrigin: String
        get() = baseUrl.replaceFirst(Regex("^wss:"), "https:").trimEnd('/')
}

internal class VoiceProtocolException : IllegalArgumentException("Invalid Voxide message")

internal sealed interface VoiceEvent {
    data object Ready : VoiceEvent
    data class Audio(val pcm: ByteArray) : VoiceEvent
    data class Tool(val id: String, val name: String, val validArguments: Boolean) : VoiceEvent
    data object Interrupted : VoiceEvent
    data object TurnComplete : VoiceEvent
    data class Error(val usageLimit: Boolean) : VoiceEvent
    data object Text : VoiceEvent
    data object Unknown : VoiceEvent
}

/**
 * Experimental wire adapter derived from the public @voxide/react 0.8.0 core.js:
 * https://unpkg.com/@voxide/react@0.8.0/dist/core.js
 * Browser SDK evidence is not a provider guarantee of native Android support.
 */
internal object VoiceProtocol {
    const val ACTION = "open_design_gallery"
    const val MAX_MESSAGE_BYTES = 96 * 1024
    const val MAX_AUDIO_BYTES = 48_000
    const val MAX_INPUT_BYTES = 640

    fun manifest(): String = JSONObject()
        .put("actions", JSONArray().put(JSONObject()
            .put("name", ACTION)
            .put("description", "Open the design gallery in this app.")
            .put("params", JSONObject())
            .put("scope", "global")
            .put("dangerous", false)))
        .put("stateSchema", JSONArray())
        .put("environment", "production")
        .toString()

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
                ) throw VoiceProtocolException()
                VoiceEvent.Audio(pcm)
            }
            "tool_call" -> {
                val id = json.string("id", 128)
                val name = json.string("name", 64)
                if (!id.matches(Regex("[A-Za-z0-9_.:-]+")) ||
                    !name.matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))
                ) throw VoiceProtocolException()
                val args = json.opt("args")
                val knownFields = setOf("type", "id", "name", "args")
                VoiceEvent.Tool(
                    id, name,
                    args is JSONObject && args.length() == 0 &&
                        json.keys().asSequence().all { it in knownFields },
                )
            }
            "interrupted" -> VoiceEvent.Interrupted
            "turn_complete" -> VoiceEvent.TurnComplete
            "text", "text_user" -> {
                json.string("text", 8192)
                if (json.has("turnComplete") && json.opt("turnComplete") !is Boolean) {
                    throw VoiceProtocolException()
                }
                VoiceEvent.Text
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

    fun toolResult(tool: VoiceEvent.Tool, success: Boolean, message: String): String {
        val result = JSONObject().put("status", if (success) "success" else "error")
            .put(if (success) "result" else "message", message)
        return JSONObject().put("type", "tool_result").put("id", tool.id)
            .put("name", tool.name).put("result", result).put("state", JSONObject()).toString()
    }

    fun objectFrom(text: String): JSONObject {
        if (text.length > MAX_MESSAGE_BYTES ||
            text.toByteArray(Charsets.UTF_8).size > MAX_MESSAGE_BYTES
        ) throw VoiceProtocolException()
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
                } else if (escape !in "\"\\/bfnrt") fail()
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
    private fun take(char: Char): Boolean = if (peek() == char) { index++; true } else false
    private fun expect(char: Char) { if (!take(char)) fail() }
    private fun fail(): Nothing = throw VoiceProtocolException()
}
