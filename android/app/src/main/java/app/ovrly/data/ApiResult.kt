package app.ovrly.data

import app.ovrly.contract.ContractError
import app.ovrly.contract.ContractErrorAction

/** Outcome of one API call. A [Failure] is never a success, whatever the server sent. */
internal sealed interface ApiResult<out T> {
    data class Success<T>(val value: T, val requestId: String) : ApiResult<T>

    data class Failure(val failure: ApiFailure) : ApiResult<Nothing>
}

/** Why a call did not succeed. Every variant carries the request id sent with the call. */
internal sealed interface ApiFailure {
    val requestId: String

    /** Whether repeating the same request later can succeed without changing anything. */
    val retryable: Boolean

    /** The server answered with the shared error shape (`error.schema.json`). */
    data class Server(val status: Int, val error: ContractError) : ApiFailure {
        val code: ApiErrorCode get() = ApiErrorCode.fromWire(error.code)
        val action: ContractErrorAction get() = error.action
        override val requestId: String get() = error.requestId
        override val retryable: Boolean get() = error.retryable
    }

    /** No HTTP response: offline, DNS, refused, reset or timed out (after the cold retry). */
    data class Network(override val requestId: String, val reason: String) : ApiFailure {
        override val retryable: Boolean get() = true
    }

    /**
     * The server answered with something this client cannot read: an error body outside the
     * error shape, or a success body the contract codecs reject. Never treated as success.
     */
    data class Incompatible(override val requestId: String, val status: Int, val reason: String) :
        ApiFailure {
        override val retryable: Boolean get() = false
    }
}

/**
 * Error codes the backend emits today (`services/api`). A code this version does not know
 * maps to [UNKNOWN]: the failure, its message, `retryable` and `action` are still kept, and
 * it never becomes a success.
 */
internal enum class ApiErrorCode {
    AUTHENTICATION_REQUIRED,
    INVALID_CREDENTIAL,
    CLIENT_IDENTITY_REJECTED,
    VALIDATION_FAILED,
    NOT_FOUND,
    METHOD_NOT_ALLOWED,
    REQUEST_FAILED,
    DATABASE_UNAVAILABLE,
    INTERNAL_ERROR,
    UPLOAD_TOO_LARGE,
    UPLOAD_ALREADY_COMPLETED,
    UPLOAD_EXPIRED,
    UPLOAD_CONTENT_MISSING,
    UPLOAD_MISMATCH,
    UPLOAD_INCOMPLETE,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_KEY_INVALID,
    IDEMPOTENCY_KEY_REUSED,
    DURATION_LIMIT_EXCEEDED,
    ACCOUNT_LINK_UNAVAILABLE,
    INVALID_ID_TOKEN,
    ACCOUNT_ALREADY_LINKED,
    JOB_NOT_CANCELLABLE,
    PROCESSING_FAILED,
    CAPTURE_CHUNK_CONFLICT,
    CAPTURE_CLOSE_CONFLICT,
    CAPTURE_CLOSED,
    CAPTURE_EXPIRED,
    CAPTURE_FINAL_CHUNK,
    CAPTURE_TOO_LARGE,
    UNKNOWN;

    companion object {
        fun fromWire(code: String): ApiErrorCode =
            entries.firstOrNull { it != UNKNOWN && it.name == code } ?: UNKNOWN
    }
}

/** True when the stored bearer credential was refused and a new guest identity may help. */
internal val ApiFailure.isCredentialRejected: Boolean
    get() = this is ApiFailure.Server &&
        (code == ApiErrorCode.INVALID_CREDENTIAL || code == ApiErrorCode.AUTHENTICATION_REQUIRED)
