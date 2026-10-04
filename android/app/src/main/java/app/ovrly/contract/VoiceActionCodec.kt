package app.ovrly.contract

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull

/** A payload or model that violates the shared contract. The message names the first failure. */
internal class ContractParseException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

/**
 * Production parser and encoder for the voice-actions contract. This is the single entry
 * point for #18 (AN-03) and #35 (AN-09); do not add a second parser.
 *
 * Requests are strict: unknown keys fail because the server validates them with
 * `additionalProperties: false`. Responses tolerate unknown keys (an optional field is an
 * additive change) but still fail on missing required fields, wrong types, bad identifiers
 * and shape violations. Unknown enum strings in responses map to each enum's `UNKNOWN`.
 */
internal object VoiceActionCodec {
    /** Contract version these models implement; must equal `packages/contracts/VERSION`. */
    const val CONTRACT_VERSION = "0.1.0-draft"

    private val requestJson = Json {
        ignoreUnknownKeys = false
        isLenient = false
        coerceInputValues = false
        encodeDefaults = false
    }

    private val responseJson = Json {
        ignoreUnknownKeys = true
        isLenient = false
        coerceInputValues = false
        encodeDefaults = false
    }

    fun parseRequest(payload: String): VoiceActionRequest =
        parse("request") { requestJson.decodeFromString<VoiceActionRequest>(payload) }

    fun parseResponse(payload: String): VoiceActionResponse =
        parse("response") { responseJson.decodeFromString<VoiceActionResponse>(payload) }

    fun encodeRequest(request: VoiceActionRequest): String =
        encode { requestJson.encodeToString(VoiceActionRequest.serializer(), request) }

    /** Encodes a response, for fakes and tests; a response carrying UNKNOWN cannot be encoded. */
    fun encodeResponse(response: VoiceActionResponse): String =
        encode { responseJson.encodeToString(VoiceActionResponse.serializer(), response) }

    private inline fun <T> parse(kind: String, decode: () -> T): T = try {
        decode()
    } catch (cause: SerializationException) {
        throw ContractParseException("Invalid voice-action $kind: ${cause.message}", cause)
    } catch (cause: IllegalArgumentException) {
        throw ContractParseException("Invalid voice-action $kind: ${cause.message}", cause)
    }

    private inline fun encode(write: () -> String): String = try {
        write()
    } catch (cause: SerializationException) {
        throw IllegalArgumentException(cause.message, cause)
    }
}

/**
 * Serializes a contract enum by its wire name. Decoding an undefined value yields the
 * enum's `UNKNOWN` entry; encoding `UNKNOWN` is refused because no wire name exists for it.
 */
internal open class WireEnumSerializer<E : Enum<E>>(
    serialName: String,
    private val fromWire: (String) -> E,
    private val wireName: (E) -> String
) : KSerializer<E> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor(serialName, PrimitiveKind.STRING)

    override fun deserialize(decoder: Decoder): E = fromWire(decoder.decodeString())

    override fun serialize(encoder: Encoder, value: E) {
        val name = wireName(value)
        if (name.isEmpty()) {
            throw SerializationException("${descriptor.serialName} UNKNOWN has no wire value")
        }
        encoder.encodeString(name)
    }
}

internal object VoiceTargetKindSerializer : WireEnumSerializer<VoiceTargetKind>(
    "app.ovrly.contract.VoiceTargetKind",
    { VoiceTargetKind.fromWire(it) },
    VoiceTargetKind::wireName
)

internal object VoiceActionSerializer : WireEnumSerializer<VoiceAction>(
    "app.ovrly.contract.VoiceAction",
    { VoiceAction.fromWire(it) },
    VoiceAction::wireName
)

internal object VoiceActionResultSerializer : WireEnumSerializer<VoiceActionResult>(
    "app.ovrly.contract.VoiceActionResult",
    { VoiceActionResult.fromWire(it) },
    VoiceActionResult::wireName
)

internal object ContractErrorActionSerializer : WireEnumSerializer<ContractErrorAction>(
    "app.ovrly.contract.ContractErrorAction",
    { ContractErrorAction.fromWire(it) },
    ContractErrorAction::wireName
)

/**
 * JSON booleans only, used for `retryable` (the contract's only boolean): kotlinx would
 * otherwise accept the strings "true" and "false".
 */
internal object StrictBooleanSerializer : KSerializer<Boolean> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("app.ovrly.contract.StrictBoolean", PrimitiveKind.BOOLEAN)

    override fun deserialize(decoder: Decoder): Boolean {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement()
            ?: return decoder.decodeBoolean()
        val literal = element as? JsonPrimitive
        if (literal == null || literal.isString || literal.booleanOrNull == null) {
            throw SerializationException("retryable must be a JSON boolean but was $element")
        }
        return literal.boolean
    }

    override fun serialize(encoder: Encoder, value: Boolean) = encoder.encodeBoolean(value)
}
