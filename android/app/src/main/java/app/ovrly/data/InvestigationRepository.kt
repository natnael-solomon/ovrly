package app.ovrly.data

import app.ovrly.contract.Investigation
import app.ovrly.contract.ProcessingStatus
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/** What the UI shows for an investigation, from the contract's `processing_status`. */
internal enum class CheckStatus(val terminal: Boolean) {
    WAITING(false),
    CHECKING(false),
    PARTIAL(false),
    COMPLETE(true),
    FAILED(true),
    CANCELLED(true),

    /** A status this version does not know: never complete, keep asking the server. */
    UNKNOWN(false);

    companion object {
        fun of(status: ProcessingStatus): CheckStatus = when (status) {
            ProcessingStatus.WAITING -> WAITING
            ProcessingStatus.CHECKING -> CHECKING
            ProcessingStatus.PARTIAL -> PARTIAL
            ProcessingStatus.COMPLETE -> COMPLETE
            ProcessingStatus.FAILED -> FAILED
            ProcessingStatus.CANCELLED -> CANCELLED
            ProcessingStatus.UNKNOWN -> UNKNOWN
        }
    }
}

internal val Investigation.checkStatus: CheckStatus get() = CheckStatus.of(processingStatus)

/** One observation of an investigation while it is tracked. */
internal sealed interface InvestigationUpdate {
    data class Loaded(val investigation: Investigation) : InvestigationUpdate {
        val status: CheckStatus get() = investigation.checkStatus
    }

    /** The server could not be read; [lastKnown] is the previous successful read, if any. */
    data class Unavailable(val failure: ApiFailure, val lastKnown: Investigation?) :
        InvestigationUpdate
}

/** Poll intervals. A failure doubles the wait up to [maxMillis]; a success resets it. */
internal data class PollingPolicy(
    val activeMillis: Long = 3_000,
    val partialMillis: Long = 5_000,
    val maxMillis: Long = 30_000
)

/**
 * Single entry point for investigation state, for share intake, #31 (overlay results) and
 * #34 (report screens). Every server read is written to Room through [LocalJobs], so the UI
 * is rebuilt from the store and the server, never from ViewModel memory.
 */
internal class InvestigationRepository(
    private val api: OvrlyApi,
    private val jobs: LocalJobs,
    private val polling: PollingPolicy = PollingPolicy()
) {
    /** The last stored read of [id], or null if none was stored. */
    suspend fun cached(id: String): Investigation? = jobs.cachedInvestigation(id)

    /** The newest cached report version of [id] and whether it may be out of date. */
    suspend fun cachedReport(id: String): CachedReport? = jobs.cachedReport(id)

    /**
     * Reads [id] from the server and stores the result: a read wins over local state, a
     * `NOT_FOUND` marks the investigation gone, and an unreachable server marks provisional
     * reports that were not confirmed lately as stale.
     */
    suspend fun refresh(id: String): ApiResult<Investigation> {
        val result = api.getInvestigation(id)
        when (result) {
            is ApiResult.Success -> jobs.recordRead(result.value)

            is ApiResult.Failure -> when {
                result.failure.isNotFound -> jobs.gone(id)
                result.failure is ApiFailure.Network -> jobs.markUnconfirmed()
                else -> Unit
            }
        }
        return result
    }

    /**
     * Emits every read until the status is terminal or a failure cannot be retried (for
     * example `NOT_FOUND` after the workspace expired, or a response this client cannot read).
     */
    fun track(id: String): Flow<InvestigationUpdate> = flow {
        var failureWait = polling.activeMillis
        var keepPolling = true
        while (keepPolling) {
            val wait = when (val result = refresh(id)) {
                is ApiResult.Success -> {
                    emit(InvestigationUpdate.Loaded(result.value))
                    failureWait = polling.activeMillis
                    keepPolling = !result.value.checkStatus.terminal
                    intervalFor(result.value.checkStatus)
                }

                is ApiResult.Failure -> {
                    emit(InvestigationUpdate.Unavailable(result.failure, cached(id)))
                    keepPolling = result.failure.retryable
                    failureWait.also { failureWait = (it * 2).coerceAtMost(polling.maxMillis) }
                }
            }
            if (keepPolling) delay(wait)
        }
    }

    private fun intervalFor(status: CheckStatus): Long = when (status) {
        CheckStatus.PARTIAL -> polling.partialMillis
        CheckStatus.UNKNOWN -> polling.maxMillis
        else -> polling.activeMillis
    }
}

/** The server does not know the object, or no longer has it. */
internal val ApiFailure.isNotFound: Boolean
    get() = this is ApiFailure.Server && code == ApiErrorCode.NOT_FOUND
