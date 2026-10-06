package app.ovrly.ui

import app.ovrly.contract.Investigation
import app.ovrly.contract.ReportVersion
import app.ovrly.data.CheckStatus
import app.ovrly.data.ReanalysisRequest
import app.ovrly.data.checkStatus

/*
 * Report screen view state (#34). Work state (where the check is) and evidence state (what
 * the shown version covers and how final it is) are separate banners so one is never read
 * as the other. There is no report-level verdict.
 */

/** Where the check is. Never says anything about the claims. */
internal data class WorkBanner(
    val status: String,
    val stage: String?,
    val message: String?,
    val tone: Tone
)

/** How much media the shown version covers and how final it is. */
internal data class CoverageBanner(
    val coverage: Label,
    val amount: String?,
    val provisional: Boolean,
    val stale: Boolean,
    /** A development stub (`OVRLY_STUB_REPORTS`), never a check of this media. */
    val fixture: Boolean
)

/** A whole report screen for one investigation and one shown version. */
internal data class ReportView(
    val investigationId: String,
    val title: String,
    val work: WorkBanner,
    val coverage: CoverageBanner?,
    val version: Int?,
    val latestVersion: Int,
    val changeSummary: String?,
    val supersedes: String?,
    val claims: List<ClaimView>,
    /** Shown instead of claims when there are none to show; never a verdict on the video. */
    val empty: String?,
    val captured: Boolean,
    /** Corrections start from the latest published version only. */
    val canCorrect: Boolean,
    /** Id of the shown version, which an explicit save keeps (#36). */
    val reportId: String? = null
)

/** Flags about the shown version that come from the store or the version list. */
internal data class ShownFlags(val stale: Boolean = false, val fixture: Boolean = false)

internal fun reportView(
    investigation: Investigation,
    shown: ReportVersion? = investigation.report,
    flags: ShownFlags = ShownFlags()
): ReportView {
    val status = investigation.checkStatus
    val latest = investigation.report
    val kind = investigation.source.kind.wireName
    return ReportView(
        investigationId = investigation.id,
        title = sourceTitle(investigation.source, kind, null),
        work = workBanner(investigation),
        coverage = shown?.let {
            CoverageBanner(
                coverage = investigation.coverage.label(),
                amount = investigation.coverage.amount(),
                provisional = it.provisional,
                stale = flags.stale,
                // The version carries the flag itself (#102); the version list is a fallback.
                fixture = it.fixture || flags.fixture
            )
        },
        version = shown?.version,
        latestVersion = investigation.version,
        changeSummary = shown?.changeSummary,
        supersedes = shown?.supersedes,
        claims = shown?.let(::claimViews).orEmpty(),
        empty = emptyMessage(status, shown),
        captured = investigation.isCaptured,
        canCorrect = latest != null && shown?.version == latest.version,
        reportId = shown?.id
    )
}

internal fun claimViews(report: ReportVersion): List<ClaimView> = report.claims.map { claim ->
    claimView(claim, report.assessmentFor(claim.id), report.evidenceFor(claim.id))
}

private fun workBanner(investigation: Investigation): WorkBanner {
    val status = investigation.checkStatus
    val error = investigation.error
    val stopping = investigation.job?.cancelRequested == true && !status.terminal
    val message = when {
        error != null -> {
            val next = if (error.retryable) " You can try again." else ""
            "${error.message} (${error.code}).$next"
        }

        stopping -> "Stopping the check. Results published so far are kept."

        status == CheckStatus.PARTIAL -> "Still checking. More claims and sources may follow."

        status == CheckStatus.UNKNOWN -> "This status is $NOT_RECOGNISED."

        else -> null
    }
    return WorkBanner(
        status = if (stopping) STOPPING else status.label,
        stage = investigation.stage.label.takeUnless { status.terminal },
        message = message,
        tone = status.tone
    )
}

private fun emptyMessage(status: CheckStatus, shown: ReportVersion?): String? = when {
    shown != null && shown.claims.isEmpty() ->
        "No checkable factual claims were found in the checked media. " +
            "This is not a finding that the video is accurate."

    shown != null -> null

    status == CheckStatus.FAILED -> "No results: the check could not be completed."

    status == CheckStatus.CANCELLED ->
        "The check was cancelled before any results were published."

    else -> "No results yet. Claims appear here as they are checked."
}

/**
 * What changed for one claim between [earlier] and [shown]. The original wording is the
 * same in every version and is always shown next to this, so it is not listed as a change.
 */
internal fun claimChanges(
    claimId: String,
    earlier: ReportVersion,
    shown: ReportVersion
): List<String> {
    val before = earlier.claims.firstOrNull { it.id == claimId }
    val after = shown.claims.firstOrNull { it.id == claimId }
    return when {
        before == null -> listOf("Not in version ${earlier.version}; found later.")

        after == null -> listOf("Not in version ${shown.version}.")

        else -> buildList {
            val v = earlier.version
            if (before.proposition != after.proposition) {
                add("Meaning in version $v: \"${before.proposition}\"")
            }
            val old = overallText(earlier, claimId)
            if (old != overallText(shown, claimId)) add("Assessment in version $v: $old")
            val oldSources = earlier.evidenceFor(claimId).size
            val newSources = shown.evidenceFor(claimId).size
            if (oldSources != newSources) {
                add("Sources: $oldSources in version $v, $newSources in version ${shown.version}")
            }
            if (isEmpty()) add("No change to this claim since version $v.")
        }
    }
}

private fun overallText(report: ReportVersion, claimId: String): String =
    report.assessmentFor(claimId)?.overall?.label?.text ?: "Not assessed"

/** Why a corrected meaning cannot be sent, or null when it can. */
internal fun correctionProblem(current: String, text: String): String? = when {
    text.isBlank() -> "Write what the claim means."

    text.length > ReanalysisRequest.MAX_PROPOSITION_LENGTH ->
        "Keep it under ${ReanalysisRequest.MAX_PROPOSITION_LENGTH} characters."

    text.trim() == current.trim() -> "This is the same meaning as now."

    else -> null
}
