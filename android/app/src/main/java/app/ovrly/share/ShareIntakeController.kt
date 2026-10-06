package app.ovrly.share

import app.ovrly.contract.InvestigationCreateRequest
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.Upload
import app.ovrly.contract.UploadDeclareRequest
import app.ovrly.data.ApiErrorCode
import app.ovrly.data.ApiFailure
import app.ovrly.data.ApiResult
import app.ovrly.data.ApiServices
import app.ovrly.data.CheckStatus
import app.ovrly.data.InvestigationUpdate
import app.ovrly.data.JobEvent
import app.ovrly.data.OvrlyApi
import app.ovrly.data.checkStatus
import java.io.FileNotFoundException
import java.io.IOException
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * What the intake sheet shows. [Staging] is the local state (a private copy on this device),
 * [Uploading] the upload state and [Tracking] the accepted state: the server holds a durable
 * investigation and its status is polled.
 */
internal sealed interface IntakeState {
    data object Idle : IntakeState

    data object Inspecting : IntakeState

    data class Staging(val copiedBytes: Long, val totalBytes: Long?) : IntakeState

    data class Duplicate(val investigationId: String, val status: CheckStatus) : IntakeState

    data class Uploading(val sentBytes: Long, val totalBytes: Long) : IntakeState

    data object Submitting : IntakeState

    data class Tracking(
        val investigationId: String,
        val status: CheckStatus,
        val notice: String? = null
    ) : IntakeState

    data class Rejected(val problem: ShareProblem, val maxBytes: Long) : IntakeState

    data class Failed(
        val title: String,
        val message: String,
        val retryable: Boolean,
        val requestId: String?
    ) : IntakeState
}

internal enum class IntakeAction { DISMISS, RETRY, OPEN_EXISTING, CHECK_AGAIN }

internal class IntakeDependencies(
    /** Absent when the build has no valid API base URL; intake then stops before uploading. */
    val service: ApiServices?,
    val staging: ShareStaging,
    val limits: ShareLimits,
    val idempotencyKeys: () -> String = { UUID.randomUUID().toString() }
)

/**
 * Turns one share into a durable investigation: on-device checks, a private streamed copy,
 * `POST /v1/uploads`, `PUT` content, `POST .../complete`, then `POST /v1/investigations` with
 * an `Idempotency-Key`, then status polling. A retry reuses the staged copy, the declared
 * upload and the same key, so the server replays the first answer instead of creating a
 * second investigation or a second upload.
 *
 * Every run carries a generation; [reset] starts a new one, so a cancelled run can neither
 * publish state nor keep its staged copy.
 */
internal class ShareIntakeController(
    private val scope: CoroutineScope,
    private val dependencies: IntakeDependencies,
    private val io: CoroutineDispatcher = Dispatchers.IO
) {
    private val mutableState = MutableStateFlow<IntakeState>(IntakeState.Idle)
    private val maxBytes = dependencies.limits.maxBytes
    private val generation = AtomicInteger()
    val state: StateFlow<IntakeState> = mutableState.asStateFlow()
    val waking: StateFlow<Boolean> = dependencies.service?.api?.waking ?: MutableStateFlow(false)

    @Volatile private var pending: Pending? = null

    @Volatile private var run: Job? = null

    @Volatile private var tracking: Job? = null

    init {
        // Before any byte is staged, so reconciliation never sweeps this sheet's copies.
        dependencies.service?.live?.addDirectory(dependencies.staging.directory)
    }

    /** Starts a new intake, replacing any previous one; returns the Settings summary. */
    suspend fun accept(read: () -> ShareRead): SharedInput {
        val gen = reset()
        mutableState.value = IntakeState.Inspecting
        val work = scope.async(io) {
            when (val result = read()) {
                is ShareRead.Rejected ->
                    publish(gen, IntakeState.Rejected(result.problem, maxBytes))

                is ShareRead.Accepted ->
                    proceed(Pending(result.candidate, dependencies.idempotencyKeys()), true, gen)
            }
            summary(mutableState.value)
        }
        run = work
        return work.await()
    }

    fun onAction(action: IntakeAction) {
        val work = pending
        when (action) {
            IntakeAction.DISMISS -> {
                reset()
                mutableState.value = IntakeState.Idle
            }

            IntakeAction.RETRY, IntakeAction.CHECK_AGAIN -> work?.let {
                if (action == IntakeAction.CHECK_AGAIN) {
                    it.idempotencyKey = dependencies.idempotencyKeys()
                }
                run?.cancel()
                val gen = generation.incrementAndGet()
                run = scope.launch(io) { proceed(it, false, gen) }
            }

            IntakeAction.OPEN_EXISTING -> (state.value as? IntakeState.Duplicate)?.let {
                work?.staged?.file?.delete()
                track(it.investigationId, it.status, generation.get())
            }
        }
    }

    /** Ends the intake for good: cancels work and deletes this intake's staging directory. */
    fun close() {
        reset()
        dependencies.staging.clear()
        dependencies.service?.live?.removeDirectory(dependencies.staging.directory)
    }

    /**
     * Cancels the current run and tracking, drops its copy, forgets a share the server has
     * not accepted yet and returns the new generation.
     */
    private fun reset(): Int {
        val gen = generation.incrementAndGet()
        run?.cancel()
        tracking?.cancel()
        pending?.let { work ->
            work.staged?.file?.delete()
            val service = dependencies.service
            if (service != null && work.recorded && !work.accepted) {
                service.background.launch {
                    service.jobs.abandon(work.localId)
                    service.live.remove(work.localId)
                }
            }
        }
        pending = null
        return gen
    }

    /** Writes [next] only if no newer run has started since [gen]. */
    private fun publish(gen: Int, next: IntakeState) {
        synchronized(mutableState) {
            if (gen == generation.get()) mutableState.value = next
        }
    }

    /**
     * Runs one attempt. The share is recorded in Room as `local_pending` once it passed the
     * device checks, `uploading` while bytes are sent and accepted once the server answers;
     * a retryable failure returns it to `local_pending`, any other failure marks it failed.
     */
    private suspend fun proceed(work: Pending, dedupe: Boolean, gen: Int) {
        if (gen != generation.get()) return
        pending = work
        val service = dependencies.service
        try {
            val staged = stageIfNeeded(work, gen)
            val shareKey = staged?.let { ShareKeys.file(it.sha256) }
                ?: ShareKeys.link((work.candidate as ShareCandidate.Link).url)
            if (dedupe) offerExisting(shareKey)
            if (service == null) throw IntakeStop(NOT_CONFIGURED)
            if (!work.recorded) {
                service.live.add(work.localId)
                service.jobs.startShare(
                    shareRecord(work.localId, work.candidate, work.idempotencyKey, staged, shareKey)
                )
                work.recorded = true
            }
            val source = when (val candidate = work.candidate) {
                is ShareCandidate.Link -> InvestigationSource.Url(candidate.url)

                is ShareCandidate.Video -> {
                    service.jobs.apply(work.localId, JobEvent.UploadStarted)
                    val uploader = IntakeUploader(service.api, maxBytes, { publish(gen, it) }) {
                        service.jobs.recordAttempt(work.localId, it)
                    }
                    val uploadId =
                        uploader.upload(work.upload, checkNotNull(staged), candidate.contentType)
                    InvestigationSource.Upload(uploadId, candidate.durationMs)
                }
            }
            publish(gen, IntakeState.Submitting)
            val created = service.api
                .createInvestigation(InvestigationCreateRequest(source), work.idempotencyKey)
                .orStop(maxBytes)
            service.jobs.accepted(work.localId, created)
            work.accepted = true
            service.live.remove(work.localId)
            staged?.file?.delete()
            track(created.id, created.checkStatus, gen)
        } catch (stop: IntakeStop) {
            if (service != null && work.recorded) {
                service.jobs.recordStop(work.localId, work.upload, stop.state)
            }
            publish(gen, stop.state)
        } finally {
            // A superseded run (dismissed, or replaced by a new share) never keeps its copy.
            if (gen != generation.get() && pending !== work) work.staged?.file?.delete()
        }
    }

    private suspend fun stageIfNeeded(work: Pending, gen: Int): StagedFile? {
        val video = work.candidate as? ShareCandidate.Video
        if (video != null && work.staged == null) work.staged = stage(video, gen)
        currentCoroutineContext().ensureActive()
        return work.staged
    }

    private suspend fun stage(video: ShareCandidate.Video, gen: Int): StagedFile {
        val job = currentCoroutineContext()[Job]
        publish(gen, IntakeState.Staging(0, video.sizeBytes))
        val problem = try {
            val staged = dependencies.staging.stage(
                video.open,
                maxBytes,
                cancelled = { job?.isActive == false || gen != generation.get() }
            ) { publish(gen, IntakeState.Staging(it, video.sizeBytes)) }
            if (staged.sizeBytes > 0) return staged
            staged.file.delete()
            ShareProblem.UNREADABLE
        } catch (_: StagingLimitExceeded) {
            ShareProblem.TOO_LARGE
        } catch (_: FileNotFoundException) {
            ShareProblem.EXPIRED
        } catch (_: SecurityException) {
            ShareProblem.EXPIRED
        } catch (_: IOException) {
            ShareProblem.UNREADABLE
        }
        throw IntakeStop(IntakeState.Rejected(problem, maxBytes))
    }

    /**
     * Stops with [IntakeState.Duplicate] when this item already has a live investigation. A
     * `NOT_FOUND` read marks the old one gone, so the share continues as a new one.
     */
    private suspend fun offerExisting(shareKey: String) {
        val service = dependencies.service ?: return
        val existing = service.jobs.findAccepted(shareKey) ?: return
        val result = service.investigations.refresh(existing)
        if (result is ApiResult.Success) {
            throw IntakeStop(IntakeState.Duplicate(existing, result.value.checkStatus))
        }
    }

    private fun track(id: String, initial: CheckStatus, gen: Int) {
        tracking?.cancel()
        publish(gen, IntakeState.Tracking(id, initial))
        val repository = dependencies.service?.investigations ?: return
        tracking = scope.launch(io) {
            repository.track(id).collect { update ->
                val previous = (state.value as? IntakeState.Tracking)?.status ?: initial
                publish(gen, trackingState(id, update, previous))
            }
        }
    }

    private class Pending(val candidate: ShareCandidate, var idempotencyKey: String) {
        val localId: String = UUID.randomUUID().toString()

        @Volatile var staged: StagedFile? = null
        val upload = UploadAttempt()

        /** Stored in Room; set once the share passed the device and duplicate checks. */
        @Volatile var recorded = false

        /** The server accepted it; dismissing the sheet no longer forgets it. */
        @Volatile var accepted = false
    }

    private companion object {
        val NOT_CONFIGURED = IntakeState.Failed(
            "Service not configured",
            "This build has no ovrly service address, so nothing was uploaded.",
            false,
            null
        )
    }
}

/** What one intake has achieved on the server so far; survives retries. */
internal class UploadAttempt {
    /** The upload declared for the staged copy; a retry completes it instead of a new one. */
    @Volatile var declared: Upload? = null

    /** Set once the server confirmed the upload complete. */
    @Volatile var uploadId: String? = null
}

/**
 * Uploads one staged copy. A retry first asks the server to complete the upload it already
 * declared (completion is idempotent), sends the bytes again only when they are missing or
 * do not match, and declares a new upload only when the old one expired or is gone.
 */
internal class IntakeUploader(
    private val api: OvrlyApi,
    private val maxBytes: Long,
    private val publish: (IntakeState) -> Unit,
    /**
     * Stores the attempt as soon as the server has a new declared or completed upload, before
     * the next call, so a retry after process death never declares or uploads again.
     */
    private val persist: suspend (UploadAttempt) -> Unit = {}
) {
    /** Returns the id of a completed upload of [staged]. */
    suspend fun upload(attempt: UploadAttempt, staged: StagedFile, contentType: String): String {
        attempt.uploadId?.let { return it }
        var declared = attempt.declared?.let { resume(attempt, it) }
        if (attempt.uploadId == null) {
            if (declared == null) {
                publish(IntakeState.Uploading(0, staged.sizeBytes))
                declared = declare(staged, contentType)
                attempt.declared = declared
                persist(attempt)
            }
            send(declared, staged)
            val completed = api.completeUpload(declared.id).orStop(maxBytes)
            if (!completed.isCompleted) throw IntakeStop(INCOMPATIBLE_UPLOAD)
            attempt.uploadId = completed.id
            persist(attempt)
        }
        return checkNotNull(attempt.uploadId)
    }

    /** Completes [previous] if its bytes arrived; returns it when they must be sent again. */
    private suspend fun resume(attempt: UploadAttempt, previous: Upload): Upload? =
        when (val result = api.completeUpload(previous.id)) {
            is ApiResult.Success -> {
                if (!result.value.isCompleted) throw IntakeStop(INCOMPATIBLE_UPLOAD)
                attempt.uploadId = result.value.id
                persist(attempt)
                previous
            }

            is ApiResult.Failure -> when ((result.failure as? ApiFailure.Server)?.code) {
                ApiErrorCode.UPLOAD_CONTENT_MISSING, ApiErrorCode.UPLOAD_MISMATCH -> previous

                ApiErrorCode.UPLOAD_EXPIRED, ApiErrorCode.NOT_FOUND -> {
                    attempt.declared = null
                    persist(attempt)
                    null
                }

                else -> throw IntakeStop(failureState(result.failure, maxBytes))
            }
        }

    private suspend fun declare(staged: StagedFile, contentType: String): Upload {
        val minType = UploadDeclareRequest.MIN_CONTENT_TYPE_LENGTH
        val maxType = UploadDeclareRequest.MAX_CONTENT_TYPE_LENGTH
        val type = contentType.takeIf { it.length in minType..maxType }
        return api.declareUpload(UploadDeclareRequest(staged.sizeBytes, staged.sha256, type))
            .orStop(maxBytes)
    }

    private suspend fun send(declared: Upload, staged: StagedFile) {
        val total = staged.sizeBytes
        var shownPercent = -1L
        publish(IntakeState.Uploading(0, total))
        api.putContent(declared, staged.file) { sent ->
            val percent = sent * PERCENT / total
            if (percent != shownPercent) {
                shownPercent = percent
                publish(IntakeState.Uploading(sent, total))
            }
        }.orStop(maxBytes)
    }

    private companion object {
        const val PERCENT = 100L
        val INCOMPATIBLE_UPLOAD = IntakeState.Failed(
            "Update needed",
            "The service did not confirm the upload in a form this version understands.",
            false,
            null
        )
    }
}

/** Ends the current intake run with [state]; never escapes the controller. */
internal class IntakeStop(val state: IntakeState) : Exception(state.javaClass.simpleName)

private fun <T> ApiResult<T>.orStop(maxBytes: Long): T = when (this) {
    is ApiResult.Success -> value
    is ApiResult.Failure -> throw IntakeStop(failureState(failure, maxBytes))
}

/** User copy for an API failure during intake. Unknown codes keep the server's message. */
internal fun failureState(failure: ApiFailure, maxBytes: Long): IntakeState = when (failure) {
    is ApiFailure.Server -> when (failure.code) {
        ApiErrorCode.UPLOAD_TOO_LARGE -> IntakeState.Rejected(ShareProblem.TOO_LARGE, maxBytes)

        ApiErrorCode.DURATION_LIMIT_EXCEEDED ->
            IntakeState.Rejected(ShareProblem.TOO_LONG, maxBytes)

        ApiErrorCode.UPLOAD_EXPIRED,
        ApiErrorCode.UPLOAD_MISMATCH,
        ApiErrorCode.UPLOAD_CONTENT_MISSING -> IntakeState.Failed(
            "Upload interrupted",
            "The upload did not finish. Try again to send the private copy again.",
            true,
            failure.requestId
        )

        else -> IntakeState.Failed(
            "The service did not accept this share",
            "${failure.error.message} (${failure.error.code})",
            failure.retryable,
            failure.requestId
        )
    }

    is ApiFailure.Network -> IntakeState.Failed(
        "Cannot reach ovrly",
        "Check your connection, then try again. The private copy is kept until you dismiss this.",
        true,
        failure.requestId
    )

    is ApiFailure.Incompatible -> IntakeState.Failed(
        "Update needed",
        "The ovrly service answered in a form this version cannot read.",
        false,
        failure.requestId
    )
}

internal fun trackingState(
    id: String,
    update: InvestigationUpdate,
    previous: CheckStatus
): IntakeState.Tracking = when (update) {
    is InvestigationUpdate.Loaded -> IntakeState.Tracking(id, update.status)

    is InvestigationUpdate.Unavailable -> IntakeState.Tracking(
        id,
        update.lastKnown?.checkStatus ?: previous,
        (failureState(update.failure, 0) as? IntakeState.Failed)?.message
    )
}

/** Latest share outcome for the Settings summary; the intake sheet shows the details. */
object ShareSummary {
    val latest = MutableStateFlow<SharedInput?>(null)
}

/** One-line summary for Settings; the sheet shows the full state. */
internal fun summary(state: IntakeState): SharedInput = when (state) {
    is IntakeState.Tracking ->
        SharedInput("Check accepted", "The ovrly service is checking this share.", true)

    is IntakeState.Duplicate ->
        SharedInput("Already shared", "This item already has a check.", true)

    is IntakeState.Rejected -> SharedInput(state.problem.title, state.problem.message, false)

    is IntakeState.Failed -> SharedInput(state.title, state.message, false)

    else -> SharedInput("Share in progress", "See the intake sheet for progress.", true)
}
