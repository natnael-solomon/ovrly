package app.ovrly.share

import app.ovrly.contract.SourceKind
import app.ovrly.contract.UploadCodec
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.JobEvent
import app.ovrly.data.LocalJobState
import app.ovrly.data.LocalJobs

/** Keys that recognise a repeated share: the content hash of a file, a hash of a link. */
internal object ShareKeys {
    fun file(sha256: String) = "file:$sha256"

    fun link(url: String) = "url:${sha256Hex(url)}"
}

/** The Room record of a share that passed the device checks; timestamps are set on save. */
internal fun shareRecord(
    localId: String,
    candidate: ShareCandidate,
    idempotencyKey: String,
    staged: StagedFile?,
    shareKey: String
): InvestigationRecord {
    val video = candidate as? ShareCandidate.Video
    return InvestigationRecord(
        localId = localId,
        state = LocalJobState.LOCAL_PENDING.wireName,
        sourceKind = if (video == null) SourceKind.URL.wireName else SourceKind.UPLOAD.wireName,
        sourceUrl = (candidate as? ShareCandidate.Link)?.url,
        idempotencyKey = idempotencyKey,
        shareKey = shareKey,
        stagedPath = staged?.file?.path,
        contentType = video?.contentType,
        durationMs = video?.durationMs,
        sizeBytes = staged?.sizeBytes,
        sha256 = staged?.sha256,
        createdAt = 0,
        updatedAt = 0
    )
}

/**
 * Records how an attempt ended before the server accepted the share: a retryable failure
 * returns it to `local_pending` (with the declared upload, so a retry completes it), anything
 * else marks it failed.
 */
internal suspend fun LocalJobs.recordStop(
    localId: String,
    attempt: UploadAttempt,
    stop: IntakeState
) {
    val retryable = stop is IntakeState.Failed && stop.retryable
    val event = if (retryable) JobEvent.UploadInterrupted else JobEvent.Unrecoverable
    apply(localId, event) {
        it.copy(
            declaredUpload = attempt.declared?.let(UploadCodec::encodeUpload),
            uploadId = attempt.uploadId,
            errorCode = (stop as? IntakeState.Rejected)?.problem?.name
        )
    }
}

/**
 * Stores what the server already has for [localId] (the declared upload, then the completed
 * upload id) without changing its state; called before the next network call.
 */
internal suspend fun LocalJobs.recordAttempt(localId: String, attempt: UploadAttempt) {
    val record = dao.get(localId) ?: return
    dao.upsert(
        record.copy(
            declaredUpload = attempt.declared?.let(UploadCodec::encodeUpload),
            uploadId = attempt.uploadId
        )
    )
}
