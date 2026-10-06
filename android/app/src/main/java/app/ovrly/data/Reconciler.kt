package app.ovrly.data

import app.ovrly.contract.ContractParseException
import app.ovrly.contract.InvestigationCreateRequest
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.SourceKind
import app.ovrly.contract.UploadCodec
import app.ovrly.share.IntakeState
import app.ovrly.share.IntakeStop
import app.ovrly.share.IntakeUploader
import app.ovrly.share.ShareProblem
import app.ovrly.share.ShareStaging
import app.ovrly.share.StagedFile
import app.ovrly.share.UploadAttempt
import app.ovrly.share.failureState
import app.ovrly.share.recordAttempt
import app.ovrly.share.recordStop
import java.io.File
import kotlinx.coroutines.sync.Mutex

/** What one reconciliation pass did, for logs and tests. */
internal data class ReconcileReport(
    val refreshed: Int = 0,
    val unreachable: Int = 0,
    val retried: Int = 0,
    val accepted: Int = 0,
    val failed: Int = 0
)

/**
 * Brings the store in line with the server on app start and on every return to the
 * foreground:
 *
 * - accepted items that are not finished are read again and the server's answer wins;
 * - local items left `local_pending` or `uploading` (by an earlier process, or by a sheet that
 *   is gone) are retried with their staged copy, declared upload and idempotency key, or
 *   marked failed when the copy is gone; items an open sheet is handling are left to it;
 * - staging directories nothing waits for any more are deleted.
 */
internal class Reconciler(
    private val api: OvrlyApi,
    private val jobs: LocalJobs,
    private val repository: InvestigationRepository,
    private val stagingRoot: File?,
    private val maxBytes: Long,
    private val live: LiveShares
) {
    private val running = Mutex()

    /** Runs one pass; a pass already in progress makes this call return without work. */
    suspend fun reconcile(): ReconcileReport? {
        if (!running.tryLock()) return null
        return try {
            pass()
        } finally {
            running.unlock()
        }
    }

    private suspend fun pass(): ReconcileReport {
        var report = ReconcileReport()
        for (localId in jobs.dao.all().map { it.localId }) {
            // Re-read just before acting: a sheet may have abandoned or finished it meanwhile.
            val record = jobs.dao.get(localId) ?: continue
            val state = record.jobState
            val serverId = record.serverId
            report = when {
                state.accepted && !state.terminal && serverId != null ->
                    report.refreshedWith(repository.refresh(serverId))

                !state.accepted && localId !in live -> report.retriedWith(retry(record))

                else -> report
            }
        }
        sweep()
        return report
    }

    private fun ReconcileReport.refreshedWith(result: ApiResult<*>) = when (result) {
        is ApiResult.Success -> copy(refreshed = refreshed + 1)
        is ApiResult.Failure -> copy(unreachable = unreachable + 1)
    }

    private fun ReconcileReport.retriedWith(outcome: LocalJobState?) = copy(
        retried = retried + 1,
        accepted = accepted + if (outcome?.accepted == true) 1 else 0,
        failed = failed + if (outcome == LocalJobState.FAILED) 1 else 0
    )

    /** Retries one local share; returns its state afterwards. */
    private suspend fun retry(record: InvestigationRecord): LocalJobState? {
        if (record.jobState == LocalJobState.UPLOADING) {
            jobs.apply(record.localId, JobEvent.UploadInterrupted)
        }
        val attempt = UploadAttempt().apply {
            declared = record.declaredUpload?.let(::parseUpload)
            uploadId = record.uploadId
        }
        return try {
            val source = sourceOf(record, attempt)
            val created = api
                .createInvestigation(InvestigationCreateRequest(source), record.idempotencyKey)
            when (created) {
                is ApiResult.Success -> {
                    record.stagedPath?.let { File(it).delete() }
                    jobs.accepted(record.localId, created.value)?.jobState
                }

                is ApiResult.Failure -> throw IntakeStop(failureState(created.failure, maxBytes))
            }
        } catch (stop: IntakeStop) {
            jobs.recordStop(record.localId, attempt, stop.state)
            jobs.dao.get(record.localId)?.jobState
        }
    }

    private suspend fun sourceOf(
        record: InvestigationRecord,
        attempt: UploadAttempt
    ): InvestigationSource {
        val url = record.sourceUrl
        if (record.sourceKind == SourceKind.URL.wireName && url != null) {
            return InvestigationSource.Url(url, record.durationMs)
        }
        val staged = stagedFile(record) ?: throw IntakeStop(LOST)
        jobs.apply(record.localId, JobEvent.UploadStarted)
        val uploader = IntakeUploader(api, maxBytes, {}) { jobs.recordAttempt(record.localId, it) }
        val uploadId = uploader.upload(attempt, staged, record.contentType.orEmpty())
        return InvestigationSource.Upload(uploadId, record.durationMs)
    }

    private fun stagedFile(record: InvestigationRecord): StagedFile? {
        val file = record.stagedPath?.let(::File)?.takeIf { it.isFile }
        val size = record.sizeBytes
        val sha = record.sha256
        return if (file != null && size != null && sha != null) {
            StagedFile(file, size, sha)
        } else {
            null
        }
    }

    private suspend fun sweep() {
        val root = stagingRoot ?: return
        val keep = jobs.dao.all()
            .filter { !it.jobState.accepted }
            .mapNotNull { record -> record.stagedPath?.let { File(it).parentFile } }
            .toSet() + live.directories()
        ShareStaging.sweepOrphans(root, ProcessStart.millis, keep)
    }

    private companion object {
        val LOST = IntakeState.Rejected(ShareProblem.EXPIRED, 0)

        fun parseUpload(json: String) = try {
            UploadCodec.parseUpload(json)
        } catch (_: ContractParseException) {
            null
        }
    }
}
