package app.ovrly.ui

import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.JobState
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.ReportVersion
import app.ovrly.contract.Timebase
import app.ovrly.data.CheckStatus
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import app.ovrly.data.checkStatus
import app.ovrly.data.jobState

/*
 * Inbox and Library rows (#34), built from the Room store and the contract read model only.
 * A row shows the stage and how long ago the check started, never a queue position.
 */

/** What a user can do with one Inbox item. */
internal enum class InboxAction {
    OPEN,
    CANCEL,
    RETRY,
    CONTINUE,

    /** Opens the check "Try again" created; replaces [RETRY] once it exists. */
    OPEN_RETRY
}

/** One row of the Inbox or Library. */
internal data class InboxItem(
    val localId: String,
    val serverId: String?,
    val title: String,
    val status: String,
    val stage: String?,
    val age: String,
    val detail: String?,
    val tone: Tone,
    val actions: List<InboxAction>,
    /** Finished with a published report: listed under Library rather than Inbox. */
    val library: Boolean,
    val createdAt: Long,
    val captured: Boolean,
    /** Server id of the new check a retry of this one created, if any. */
    val retriedAs: String? = null,
    /**
     * An accepted shared link or upload that has not failed or been cancelled: the only kind
     * of check the server accepts as the confirmed full video of a captured clip.
     */
    val fullVideoSource: Boolean = false
)

internal fun inboxItem(
    record: InvestigationRecord,
    investigation: Investigation?,
    now: Long
): InboxItem {
    val state = record.jobState
    val created = investigation?.createdAt?.let(::epochMillis) ?: record.createdAt
    val title = sourceTitle(investigation?.source, record.sourceKind, record.sourceUrl)
    if (investigation == null || !state.accepted) {
        return localItem(record, state, title, created, now)
    }
    val status = investigation.checkStatus
    val stopping = investigation.job?.cancelRequested == true && !status.terminal
    return InboxItem(
        localId = record.localId,
        serverId = investigation.id,
        title = title,
        status = if (stopping) STOPPING else status.label,
        stage = investigation.stage.label.takeUnless { status.terminal },
        age = "Started ${age(created, now)}",
        detail = listOfNotNull(
            itemDetail(investigation),
            RETRIED.takeIf { record.retriedAs != null }
        ).joinToString(". ").ifEmpty { null },
        tone = status.tone,
        actions = actionsFor(investigation).let { actions ->
            if (record.retriedAs == null) {
                actions
            } else {
                actions - InboxAction.RETRY + InboxAction.OPEN_RETRY
            }
        },
        library = status.terminal && investigation.report != null,
        createdAt = created,
        captured = investigation.isCaptured,
        retriedAs = record.retriedAs,
        fullVideoSource = investigation.canBeFullVideo
    )
}

private fun localItem(
    record: InvestigationRecord,
    state: LocalJobState,
    title: String,
    created: Long,
    now: Long
): InboxItem {
    val failed = state == LocalJobState.FAILED
    val status = when (state) {
        LocalJobState.LOCAL_PENDING -> "Waiting to upload from this device"
        LocalJobState.UPLOADING -> "Uploading"
        LocalJobState.FAILED -> "Could not be sent"
        else -> "Accepted by ovrly"
    }
    val detail = when {
        failed && record.errorCode != null -> "Reason: ${record.errorCode}. Share it again."
        failed -> "The private copy is gone. Share the video again."
        state.accepted -> null
        else -> "Kept on this device until it is sent. Sending resumes when you are online."
    }
    return InboxItem(
        localId = record.localId,
        serverId = record.serverId,
        title = title,
        status = status,
        stage = null,
        age = "Shared ${age(created, now)}",
        detail = detail,
        tone = if (failed) Tone.WARNING else Tone.NEUTRAL,
        actions = if (!failed && !state.accepted) listOf(InboxAction.RETRY) else emptyList(),
        library = false,
        createdAt = created,
        captured = record.sourceKind == SOURCE_CAPTURE
    )
}

private fun itemDetail(investigation: Investigation): String? {
    val error = investigation.error
    val report = investigation.report
    val cancelled = investigation.processingStatus == ProcessingStatus.CANCELLED
    return when {
        error != null -> "${error.message} (${error.code})"
        cancelled && report == null -> "Cancelled before any results were published."
        report != null && report.fixture -> "$FIXTURE. ${claimCount(report)}"
        report != null && report.claims.isEmpty() -> "No checkable claims found"
        report != null -> claimCount(report)
        else -> null
    }
}

private fun claimCount(report: ReportVersion): String {
    val claims = report.claims.size
    val assessed = report.assessments.size
    val noun = if (claims == 1) "claim" else "claims"
    return if (assessed == claims) "$claims $noun" else "$claims $noun, $assessed assessed so far"
}

/**
 * Open any accepted check. Cancel only a job that is still queued or running; retry a
 * retryable failure or a check cancelled before publishing (as a new check of the same
 * source); continue a cancelled check that already published a version (a deeper search).
 */
internal fun actionsFor(investigation: Investigation): List<InboxAction> {
    val status = investigation.checkStatus
    val report = investigation.report
    // Every accepted check opens, so a failure or cancel can be read in full.
    val actions = mutableListOf(InboxAction.OPEN)
    if (!status.terminal && investigation.jobIsCancellable) actions += InboxAction.CANCEL
    val retryable = when (status) {
        CheckStatus.FAILED -> investigation.error?.retryable == true
        CheckStatus.CANCELLED -> report == null
        else -> false
    }
    if (retryable && investigation.sourceCanBeResent) actions += InboxAction.RETRY
    if (status == CheckStatus.CANCELLED && report != null) actions += InboxAction.CONTINUE
    return actions
}

private val Investigation.jobIsCancellable: Boolean
    get() = job?.let { it.state in ACTIVE_JOB_STATES && !it.cancelRequested } == true

private val Investigation.sourceCanBeResent: Boolean
    get() = source is InvestigationSource.Url || source is InvestigationSource.Upload

private val ACTIVE_JOB_STATES = setOf(JobState.QUEUED, JobState.LEASED, JobState.RUNNING)

/** Captured from the screen rather than shared: its times are on the capture timeline. */
internal val Investigation.isCaptured: Boolean
    get() = source is InvestigationSource.Capture ||
        report?.claims?.any { it.interval.timebase == Timebase.CAPTURE } == true

/** A shared link or upload whose check is still usable: neither failed nor cancelled. */
private val Investigation.canBeFullVideo: Boolean
    get() = sourceCanBeResent && !isCaptured &&
        checkStatus != CheckStatus.FAILED && checkStatus != CheckStatus.CANCELLED

/**
 * Accepted shared checks (a link or upload, not failed or cancelled) that started after
 * [captured]: the only candidates for its full video, the same ones the server accepts. The
 * user still confirms the match; nothing is matched automatically, and a captured clip is
 * never offered as the full video of another.
 */
internal fun fullVideoCandidates(captured: InboxItem, items: List<InboxItem>): List<InboxItem> =
    items.filter { it.serverId != null && it.serverId != captured.serverId }
        .filter { it.fullVideoSource && !it.captured && it.createdAt >= captured.createdAt }

internal const val STOPPING = "Stopping"
private const val RETRIED = "Checked again as a new check"
private const val FIXTURE = "Development fixture, not a check of this video"
private const val SOURCE_CAPTURE = "capture"
