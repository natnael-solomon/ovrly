package app.ovrly.data

import app.ovrly.contract.ProcessingStatus

/**
 * Durable state of one share on this device. These names are the app's own and are not
 * `JobState` or `ProcessingStatus` from the contract:
 *
 * `local_pending -> uploading -> accepted -> queued / running / partial / succeeded / failed /
 * cancelled`
 *
 * [LOCAL_PENDING] and [UPLOADING] exist only on the device; from [ACCEPTED] on the server holds
 * a durable investigation and its read wins over anything stored here.
 */
internal enum class LocalJobState(val wireName: String, val terminal: Boolean) {
    LOCAL_PENDING("local_pending", false),
    UPLOADING("uploading", false),
    ACCEPTED("accepted", false),
    QUEUED("queued", false),
    RUNNING("running", false),
    PARTIAL("partial", false),
    SUCCEEDED("succeeded", true),
    FAILED("failed", true),
    CANCELLED("cancelled", true);

    /** True once the server has accepted the investigation. */
    val accepted: Boolean
        get() = this !in LOCAL_STATES

    /** The state after [event], or null when [event] is not allowed in this state. */
    fun next(event: JobEvent): LocalJobState? = when (event) {
        JobEvent.UploadStarted -> UPLOADING.takeIf { this == LOCAL_PENDING }

        JobEvent.UploadInterrupted -> LOCAL_PENDING.takeIf { this in LOCAL_STATES }

        JobEvent.Unrecoverable -> FAILED.takeIf { this in LOCAL_STATES }

        // A replayed create answer for an accepted item is just another server read.
        is JobEvent.Accepted -> fromServer(event.status) ?: if (accepted) this else ACCEPTED

        // Server wins for accepted items, even over a terminal local state; UNKNOWN keeps it.
        is JobEvent.Server -> if (accepted) fromServer(event.status) ?: this else null

        JobEvent.Gone -> FAILED.takeIf { accepted }
    }

    companion object {
        private val LOCAL_STATES = setOf(LOCAL_PENDING, UPLOADING)

        fun fromWire(name: String): LocalJobState? = entries.firstOrNull { it.wireName == name }

        /** The state a server status maps to; null for a status this version does not know. */
        fun fromServer(status: ProcessingStatus): LocalJobState? = when (status) {
            ProcessingStatus.WAITING -> QUEUED
            ProcessingStatus.CHECKING -> RUNNING
            ProcessingStatus.PARTIAL -> PARTIAL
            ProcessingStatus.COMPLETE -> SUCCEEDED
            ProcessingStatus.FAILED -> FAILED
            ProcessingStatus.CANCELLED -> CANCELLED
            ProcessingStatus.UNKNOWN -> null
        }
    }
}

/** What happened to a share; see [LocalJobState.next]. */
internal sealed interface JobEvent {
    /** The staged copy started uploading. */
    data object UploadStarted : JobEvent

    /** Uploading or creating failed in a way a later retry can fix. */
    data object UploadInterrupted : JobEvent

    /**
     * The share cannot be retried before the server accepted it: the staged copy is gone or
     * the server rejected the input for good.
     */
    data object Unrecoverable : JobEvent

    /** `POST /v1/investigations` answered (first time or replay). */
    data class Accepted(val status: ProcessingStatus) : JobEvent

    /** A later server read. */
    data class Server(val status: ProcessingStatus) : JobEvent

    /** The server no longer has the investigation (`NOT_FOUND`, for example an expired guest). */
    data object Gone : JobEvent
}
