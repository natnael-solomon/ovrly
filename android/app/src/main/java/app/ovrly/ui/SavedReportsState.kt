package app.ovrly.ui

import app.ovrly.data.StoredCheck
import app.ovrly.data.StoredSave

/*
 * Saved reports (AN-10, #36; AC08): the rows of Saved reports in the Library, the save control
 * of an open report and the read-only view of a saved copy. A save is always the user's
 * explicit action; nothing here saves anything by itself.
 */

/** One row of Saved reports. */
internal data class SavedItem(
    val reportId: String,
    val investigationId: String,
    val version: Int,
    val title: String,
    val detail: String,
    /** The check itself is on this device, so opening shows it; otherwise the saved copy. */
    val onDevice: Boolean,
    /** False when the stored copy uses values this app version cannot read. */
    val readable: Boolean
)

/** The save control of the shown version. */
internal data class SaveState(
    val reportId: String,
    val saved: Boolean,
    val version: Int?,
    /** Another version of the same check that is saved, when the shown one is not. */
    val otherSavedVersion: Int? = null,
    /** The screen shows the saved copy, not the check as it is now. */
    val copy: Boolean = false
)

internal fun savedItems(
    saves: List<StoredSave>,
    checks: List<StoredCheck>,
    now: Long
): List<SavedItem> = saves.map { save ->
    val entry = save.entry
    val check = checks.firstOrNull { it.investigation?.id == entry.investigationId }
    val title = check?.let {
        sourceTitle(it.investigation?.source, it.record.sourceKind, it.record.sourceUrl)
    } ?: SAVED_TITLE
    val saved = epochMillis(entry.savedAt)?.let { "Saved ${age(it, now)}" } ?: "Saved"
    val notes = listOfNotNull(
        "Version ${entry.version}",
        saved,
        FIXTURE_NOTE.takeIf { save.report?.fixture == true },
        "Uses values $NOT_RECOGNISED".takeIf { save.report == null }
    )
    SavedItem(
        reportId = entry.reportId,
        investigationId = entry.investigationId,
        version = entry.version,
        title = title,
        detail = notes.joinToString(". "),
        onDevice = check?.investigation != null,
        readable = save.report != null
    )
}

/** Whether the shown version of [view] is saved; null when it has no version to save. */
internal fun saveState(
    view: ReportView,
    saves: List<StoredSave>,
    copy: Boolean = false
): SaveState? {
    val reportId = view.reportId ?: return null
    val saved = saves.any { it.entry.reportId == reportId }
    val other = saves.filter { it.entry.investigationId == view.investigationId }
        .filter { it.entry.reportId != reportId }
        .maxOfOrNull { it.entry.version }
    return SaveState(
        reportId = reportId,
        saved = saved,
        version = view.version,
        otherSavedVersion = other.takeUnless { saved },
        copy = copy
    )
}

/**
 * The saved copy of a report whose check is not on this device: the snapshot taken at save
 * time, read-only, with no work state of its own. Null when the copy cannot be read.
 */
internal fun savedCopyView(save: StoredSave, title: String = SAVED_TITLE): ReportView? {
    val report = save.report ?: return null
    return ReportView(
        investigationId = report.investigationId,
        title = title,
        work = WorkBanner(
            status = SAVED_COPY,
            stage = null,
            message = "This is the copy you saved. It does not change, and later versions of " +
                "the check are not shown here.",
            tone = Tone.NEUTRAL
        ),
        coverage = CoverageBanner(
            coverage = Label("Coverage as when you saved it"),
            amount = null,
            provisional = report.provisional,
            stale = false,
            fixture = report.fixture
        ),
        version = report.version,
        latestVersion = report.version,
        changeSummary = report.changeSummary,
        supersedes = report.supersedes,
        claims = claimViews(report),
        empty = if (report.claims.isEmpty()) {
            "No checkable factual claims were found in the checked media. " +
                "This is not a finding that the video is accurate."
        } else {
            null
        },
        captured = false,
        canCorrect = false,
        reportId = report.id
    )
}

/** What saving does and does not keep; shown next to Saved reports and in Settings. */
internal const val SAVED_SCOPE =
    "Only reports you save are kept for you on the ovrly service. Your check history, " +
        "captures, shared files and checks still running are not kept with them."

/** Decision 0003 disclosure: second-device recovery is not part of this submission. */
internal const val RECOVERY_DISCLOSURE =
    "This submission does not yet complete AC08 recovery of saved reports on a second " +
        "Android device. It demonstrates guest checking and explicitly saved reports for " +
        "one owner. A report saved on one device cannot yet be restored on another device."

private const val FIXTURE_NOTE = "Development fixture, not a check of this video"
internal const val SAVED_TITLE = "Saved report"
internal const val SAVED_COPY = "Saved copy"
