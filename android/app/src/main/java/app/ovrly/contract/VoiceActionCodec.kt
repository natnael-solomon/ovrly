package app.ovrly.contract

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/** A payload or model that violates the shared contract. The message names the first failure. */
internal class ContractParseException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

/**
 * Production parser and encoder for the voice-actions contract. This is the single entry
 * point for #18 (AN-03) and #35 (AN-09); do not add a second parser. The read models of the
 * other schemas use the same [ContractJson] configurations through [InvestigationCodec],
 * [UploadCodec] and [CaptureCodec].
 *
 * Requests are strict: unknown keys fail because the server validates them with
 * `additionalProperties: false`. Responses tolerate unknown keys (an optional field is an
 * additive change) but still fail on missing required fields, wrong types, bad identifiers
 * and shape violations. Unknown enum strings in responses map to each enum's `UNKNOWN`.
 */
internal object VoiceActionCodec {
    /** Contract version these models implement; must equal `packages/contracts/VERSION`. */
    const val CONTRACT_VERSION = ContractJson.CONTRACT_VERSION

    fun parseRequest(payload: String): VoiceActionRequest =
        ContractJson.parse("voice-action request") {
            ContractJson.strict.decodeFromString(VoiceActionRequest.serializer(), payload)
        }

    fun parseResponse(payload: String): VoiceActionResponse =
        ContractJson.parse("voice-action response") {
            ContractJson.tolerant.decodeFromString(VoiceActionResponse.serializer(), payload)
        }

    fun encodeRequest(request: VoiceActionRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(VoiceActionRequest.serializer(), request)
    }

    /** Encodes a response, for fakes and tests; a response carrying UNKNOWN cannot be encoded. */
    fun encodeResponse(response: VoiceActionResponse): String = ContractJson.encode {
        ContractJson.tolerant.encodeToString(VoiceActionResponse.serializer(), response)
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
 * JSON booleans only: kotlinx would otherwise accept the strings "true" and "false". [field]
 * names the property in the failure when one serializer serves a single field.
 */
internal open class StrictBooleanSerializer(private val field: String? = null) :
    KSerializer<Boolean> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("app.ovrly.contract.StrictBoolean", PrimitiveKind.BOOLEAN)

    override fun deserialize(decoder: Decoder): Boolean {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement()
            ?: return decoder.decodeBoolean()
        val literal = element as? JsonPrimitive
        if (literal == null || literal.isString || literal.booleanOrNull == null) {
            val subject = field?.let { "$it must be" } ?: "expected"
            throw SerializationException("$subject a JSON boolean but was $element")
        }
        return literal.boolean
    }

    override fun serialize(encoder: Encoder, value: Boolean) = encoder.encodeBoolean(value)
}

/** `retryable` of the shared error shape. */
internal object RetryableSerializer : StrictBooleanSerializer("retryable")

/** Every other boolean of the contract (`cancel_requested`, `provisional`). */
internal object ContractBooleanSerializer : StrictBooleanSerializer()

/** JSON integers only: no quoted numbers, no fractions. Serves every `Long` of the contract. */
internal object StrictLongSerializer : KSerializer<Long> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("app.ovrly.contract.StrictLong", PrimitiveKind.LONG)

    override fun deserialize(decoder: Decoder): Long {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement()
            ?: return decoder.decodeLong()
        return requireNotNull(integerLiteral(element)?.longOrNull) {
            "expected a JSON integer but was $element"
        }
    }

    override fun serialize(encoder: Encoder, value: Long) = encoder.encodeLong(value)
}

/** JSON integers only, for the counters and version numbers typed `Int`. */
internal object StrictIntSerializer : KSerializer<Int> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("app.ovrly.contract.StrictInt", PrimitiveKind.INT)

    override fun deserialize(decoder: Decoder): Int {
        val element = (decoder as? JsonDecoder)?.decodeJsonElement()
            ?: return decoder.decodeInt()
        return requireNotNull(integerLiteral(element)?.intOrNull) {
            "expected a JSON integer but was $element"
        }
    }

    override fun serialize(encoder: Encoder, value: Int) = encoder.encodeInt(value)
}

private fun integerLiteral(element: JsonElement): JsonPrimitive? =
    (element as? JsonPrimitive)?.takeUnless { it.isString }
