package app.ovrly.contract

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json

/**
 * The two JSON configurations every codec of the contract uses, and the version pin.
 *
 * [strict] parses request bodies: the server validates them with `additionalProperties:
 * false`, so an unknown key is a client bug. [tolerant] parses read models: an unknown key
 * is an additive change by a newer server and is ignored, but missing required fields, wrong
 * types, bad identifiers and shape violations still fail. Neither coerces values.
 */
internal object ContractJson {
    /** Contract version these models implement; must equal `packages/contracts/VERSION`. */
    const val CONTRACT_VERSION = "0.3.0-draft"

    val strict: Json = Json {
        ignoreUnknownKeys = false
        isLenient = false
        coerceInputValues = false
        encodeDefaults = false
    }

    val tolerant: Json = Json {
        ignoreUnknownKeys = true
        isLenient = false
        coerceInputValues = false
        encodeDefaults = false
    }

    /** Runs [decode] and reports any contract violation as a [ContractParseException]. */
    inline fun <T> parse(kind: String, decode: () -> T): T = try {
        decode()
    } catch (cause: SerializationException) {
        throw ContractParseException("Invalid $kind: ${cause.message}", cause)
    } catch (cause: IllegalArgumentException) {
        throw ContractParseException("Invalid $kind: ${cause.message}", cause)
    }

    /** Runs [write]; a model that has no wire form (an UNKNOWN enum) is an argument error. */
    inline fun encode(write: () -> String): String = try {
        write()
    } catch (cause: SerializationException) {
        throw IllegalArgumentException(cause.message, cause)
    }
}

/**
 * Scalar syntax rules shared by the schemas (`common.schema.json` and `error.schema.json`).
 * Each check throws [IllegalArgumentException] naming the field, which the codecs report as
 * a [ContractParseException]; built in code, a model with a bad value cannot exist either.
 */
internal object ContractSyntax {
    const val MIN_ERROR_CODE_LENGTH = 3
    const val MAX_ERROR_CODE_LENGTH = 64
    const val MAX_ERROR_MESSAGE_LENGTH = 240
    const val MIN_URL_LENGTH = 8
    const val MAX_URL_LENGTH = 2048

    private val uuid = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    private val sha256 = Regex("[0-9a-f]{64}")
    private val timestamp = Regex(
        "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}" +
            "(\\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})"
    )
    private val httpUrl = Regex("https?://\\S+")
    private val errorCode = Regex("[A-Z][A-Z0-9_]*")

    fun opaqueId(field: String, value: String) = OpaqueId.require(field, value)

    /** Lowercase RFC 4122 text form, the backend row key of uploads, investigations and jobs. */
    fun uuid(field: String, value: String) {
        require(uuid.matches(value)) { "$field must be a lowercase RFC 4122 UUID" }
    }

    fun sha256(field: String, value: String) {
        require(sha256.matches(value)) { "$field must be 64 lowercase hexadecimal characters" }
    }

    /** RFC 3339 date-time with an explicit offset and at most six fractional digits. */
    fun timestamp(field: String, value: String) {
        require(timestamp.matches(value)) { "$field must be an RFC 3339 timestamp with offset" }
    }

    fun httpUrl(field: String, value: String) {
        require(value.length in MIN_URL_LENGTH..MAX_URL_LENGTH && httpUrl.matches(value)) {
            "$field must be an absolute http or https URL of at most $MAX_URL_LENGTH characters"
        }
    }

    fun text(field: String, value: String, max: Int, min: Int = 1) {
        require(value.length in min..max) { "$field must be $min to $max characters" }
    }

    fun errorCode(field: String, value: String) {
        val lengthOk = value.length in MIN_ERROR_CODE_LENGTH..MAX_ERROR_CODE_LENGTH
        require(lengthOk && errorCode.matches(value)) {
            "$field must be $MIN_ERROR_CODE_LENGTH to $MAX_ERROR_CODE_LENGTH " +
                "SCREAMING_SNAKE_CASE chars"
        }
    }

    fun errorMessage(field: String, value: String) =
        text(field, value, max = MAX_ERROR_MESSAGE_LENGTH)

    fun positive(field: String, value: Long) {
        require(value > 0) { "$field must be positive" }
    }
}
