package app.ovrly.voice

import app.ovrly.contract.VoiceAction
import app.ovrly.contract.VoiceActionRequest
import app.ovrly.contract.VoiceActionResponse
import app.ovrly.contract.VoiceActionResult
import app.ovrly.contract.VoiceTarget
import app.ovrly.contract.VoiceTargetKind
import app.ovrly.data.ApiErrorCode
import app.ovrly.data.ApiFailure
import app.ovrly.data.ApiResult
import app.ovrly.data.LocalJobs
import app.ovrly.data.VoiceApi
import app.ovrly.data.cachedInvestigation
import java.util.UUID
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject

/** One of the user's checks, as the assistant and the `latest` alias see it. */
internal data class VoiceCheck(
    val investigationId: String,
    val reportId: String?,
    val jobId: String?,
    val status: String?,
    val source: String
)

/**
 * The user's recent checks from the Room store. They are sent to the assistant as the state
 * key `checks` with every tool result (ids, status and source kind only; no URL, title or
 * transcript), and they resolve the `latest` alias on the device.
 */
internal class VoiceTargets(private val jobs: LocalJobs) {
    suspend fun recent(limit: Int = MAX_CHECKS): List<VoiceCheck> = jobs.dao.all()
        .filter { it.serverId != null }
        .sortedByDescending { it.createdAt }
        .take(limit)
        .map { record ->
            val id = checkNotNull(record.serverId)
            val investigation = jobs.cachedInvestigation(id)
            VoiceCheck(
                investigationId = id,
                reportId = investigation?.report?.id,
                jobId = investigation?.job?.id,
                status = record.processingStatus,
                source = when (record.sourceKind) {
                    "url" -> "link"
                    "upload" -> "video"
                    else -> record.sourceKind
                }
            )
        }

    /** The id to send for [command], or the message to show when `latest` has no target. */
    suspend fun resolve(command: VoiceCommand): Resolution {
        if (command.target != VoiceCommandContract.LATEST) {
            return Resolution.Target(command.target)
        }
        val latest = recent(1).firstOrNull()
        return when {
            latest == null -> Resolution.Missing(NO_CHECKS)

            kindOf(command.action) == VoiceTargetKind.REPORT ->
                latest.reportId?.let(Resolution::Target)
                    ?: Resolution.Missing("Your latest check has no report yet.")

            kindOf(command.action) == VoiceTargetKind.JOB ->
                latest.jobId?.let(Resolution::Target)
                    ?: Resolution.Missing("Your latest check has no job to act on.")

            else -> Resolution.Target(latest.investigationId)
        }
    }

    sealed interface Resolution {
        data class Target(val id: String) : Resolution

        data class Missing(val message: String) : Resolution
    }

    companion object {
        const val MAX_CHECKS = 5
        const val NO_CHECKS = "You have no checks yet. Share a video or link with ovrly first."

        fun state(checks: List<VoiceCheck>): JSONObject = JSONObject().put(
            VoiceProtocol.STATE_CHECKS,
            JSONArray(
                checks.map { check ->
                    JSONObject()
                        .put("investigation_id", check.investigationId)
                        .put("report_id", check.reportId ?: JSONObject.NULL)
                        .put("job_id", check.jobId ?: JSONObject.NULL)
                        .put("status", check.status ?: JSONObject.NULL)
                        .put("source", check.source)
                }
            )
        )
    }
}

internal fun kindOf(action: VoiceCommandAction): VoiceTargetKind =
    VoiceAction.fromWire(action.wireName).targetKind

/** A cancellation waiting for the user's on-screen answer, bound to one call and target. */
internal data class PendingConfirmation(
    val id: Long,
    val action: VoiceCommandAction,
    val target: String
)

/**
 * On-device confirmation for `queue_cancel` (BC-D04). The approval is bound to the exact
 * request and target, expires after [timeoutMillis], and every pending approval is discarded
 * when voice stops or the app leaves the foreground. A remote `confirmed` flag is never read.
 *
 * A command records [epoch] when it starts; [discardAll] advances it, so a cancellation still
 * resolving its target or queued behind another dialog never asks once voice has ended.
 */
internal class VoiceConfirmations(private val timeoutMillis: Long = TIMEOUT_MILLIS) {
    private val mutable = MutableStateFlow<PendingConfirmation?>(null)
    val pending: StateFlow<PendingConfirmation?> = mutable.asStateFlow()
    private val oneAtATime = Mutex()
    private val lock = Any()
    private var nextId = 0L
    private var discarded = 0L
    private var answer: CompletableDeferred<Boolean>? = null

    /** Changes whenever pending approvals are discarded. */
    val epoch: Long get() = synchronized(lock) { discarded }

    /**
     * True only when the user approved this exact request in time and nothing was discarded
     * since [since].
     */
    suspend fun confirm(action: VoiceCommandAction, target: String, since: Long = epoch): Boolean =
        oneAtATime.withLock { await(action, target, since) }

    private suspend fun await(action: VoiceCommandAction, target: String, since: Long): Boolean {
        val deferred = CompletableDeferred<Boolean>()
        val request = synchronized(lock) {
            if (discarded != since) return false
            answer = deferred
            PendingConfirmation(++nextId, action, target).also { mutable.value = it }
        }
        return try {
            withTimeoutOrNull(timeoutMillis) { deferred.await() } ?: false
        } finally {
            synchronized(lock) {
                if (mutable.value?.id == request.id) mutable.value = null
                if (answer === deferred) answer = null
            }
        }
    }

    /** Answers the request shown as [id]; an answer for anything else is ignored. */
    fun answer(id: Long, approved: Boolean) {
        synchronized(lock) {
            if (mutable.value?.id == id) answer?.complete(approved)
        }
    }

    fun discardAll() {
        synchronized(lock) {
            discarded++
            answer?.complete(false)
        }
    }

    private companion object {
        const val TIMEOUT_MILLIS = 30_000L
    }
}

/** What the app does after the server accepted an action. */
internal interface VoiceEffects {
    fun openCheck(investigationId: String)

    /** Brings stored checks in line with the server after a queue action. */
    suspend fun refreshChecks()

    /** Runs after every command, before its outcome is reported. */
    suspend fun finished() = Unit
}

/**
 * The production [VoiceCommandExecutor]: resolves the target, asks for on-screen confirmation
 * when the action needs it, then calls `POST /v1/voice/actions` once per command with a new
 * `request_id`. Client retries inside one command (cold start, credential refresh) reuse the
 * same request, so the server replays instead of acting twice.
 */
internal class VoiceBackend(
    private val api: VoiceApi,
    private val targets: VoiceTargets,
    private val confirmations: VoiceConfirmations,
    private val effects: VoiceEffects,
    private val scope: CoroutineScope,
    private val requestIds: () -> String = { "voice-${UUID.randomUUID()}" }
) : VoiceCommandExecutor {
    /**
     * Reads the discard epoch on the caller's thread before switching to [scope], so a stop
     * that lands before the command starts running still discards its confirmation.
     */
    override fun execute(command: VoiceCommand, done: (VoiceCommandOutcome) -> Unit) {
        val epoch = confirmations.epoch
        scope.launch {
            val outcome = run(command, epoch)
            effects.finished()
            done(outcome)
        }
    }

    suspend fun run(command: VoiceCommand, epoch: Long = confirmations.epoch): VoiceCommandOutcome =
        when (val resolved = targets.resolve(command)) {
            is VoiceTargets.Resolution.Missing ->
                VoiceCommandOutcome(false, resolved.message, NO_TARGET)

            is VoiceTargets.Resolution.Target -> confirmed(command.action, resolved.id, epoch)
        }

    private suspend fun confirmed(command: VoiceCommandAction, target: String, epoch: Long) =
        if (command.requiresConfirmation && !confirmations.confirm(command, target, epoch)) {
            VoiceCommandOutcome(false, NOT_CONFIRMED, CONFIRMATION_DECLINED)
        } else {
            send(command, target)
        }

    private suspend fun send(command: VoiceCommandAction, id: String): VoiceCommandOutcome {
        val action = VoiceAction.fromWire(command.wireName)
        val target = VoiceTarget(action.targetKind, id)
        val outcome = outcome(api.perform(VoiceActionRequest(requestIds(), action, target)))
        if (outcome.success) {
            when (command) {
                VoiceCommandAction.OPEN_CHECK -> effects.openCheck(id)
                VoiceCommandAction.SAVE_REPORT -> Unit
                else -> effects.refreshChecks()
            }
        }
        return outcome
    }

    companion object {
        const val NO_TARGET = "VOICE_TARGET_UNRESOLVED"
        const val CONFIRMATION_DECLINED = "VOICE_CONFIRMATION_DECLINED"
        const val UNKNOWN_RESULT = "VOICE_RESULT_UNKNOWN"
        const val NETWORK = "NETWORK"
        const val INCOMPATIBLE = "INCOMPATIBLE_RESPONSE"
        const val NOT_CONFIRMED = "Cancellation was not confirmed on screen. Nothing changed."

        /** Maps every answer to what the user sees; only `accepted` is ever a success. */
        fun outcome(result: ApiResult<VoiceActionResponse>): VoiceCommandOutcome = when (result) {
            is ApiResult.Success -> response(result.value)
            is ApiResult.Failure -> failure(result.failure)
        }

        private fun response(response: VoiceActionResponse): VoiceCommandOutcome =
            when (response.result) {
                VoiceActionResult.ACCEPTED -> VoiceCommandOutcome(true, response.message)

                VoiceActionResult.DENIED ->
                    VoiceCommandOutcome(false, response.message, response.error?.code)

                VoiceActionResult.UNKNOWN -> VoiceCommandOutcome(
                    false,
                    "The ovrly service answered in a way this app version does not " +
                        "understand, so nothing is assumed to have happened.",
                    UNKNOWN_RESULT
                )
            }

        private fun failure(failure: ApiFailure): VoiceCommandOutcome = when (failure) {
            is ApiFailure.Server -> VoiceCommandOutcome(
                false,
                when (failure.code) {
                    ApiErrorCode.VALIDATION_FAILED ->
                        "The service did not accept that request, so nothing changed."

                    ApiErrorCode.IDEMPOTENCY_KEY_REUSED ->
                        "That request was already used for a different action. Nothing changed."

                    else -> failure.error.message
                },
                failure.error.code
            )

            is ApiFailure.Network -> VoiceCommandOutcome(
                false,
                "Cannot reach ovrly. Check your connection and try again.",
                NETWORK
            )

            is ApiFailure.Incompatible -> VoiceCommandOutcome(
                false,
                "The ovrly service answered in a form this app version cannot read.",
                INCOMPATIBLE
            )
        }
    }
}
