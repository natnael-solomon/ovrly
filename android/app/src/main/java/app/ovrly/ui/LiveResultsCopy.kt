package app.ovrly.ui

import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.OverallAssessment
import app.ovrly.overlay.LiveAssessment
import app.ovrly.overlay.LiveClaim
import app.ovrly.overlay.LiveClaimState
import app.ovrly.overlay.LiveConnection
import app.ovrly.overlay.LiveCoverage
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase

internal const val CAPTURED_SEGMENT_LABEL = "Captured segment analyzed"
internal const val UPDATE_NOTICE_LABEL = "Assessment updated"
internal const val CONTINUE_RESEARCH_LABEL = "Continue research in queue"
internal const val KEEP_AVAILABLE_LABEL = "Keep only available results"
private const val NO_CLAIMS_TEXT =
    "No checkable claims found in the captured segment. This is not a verdict on the video."
private const val NOT_CHECKED = "Incomplete: not checked"
internal const val PENDING_CLAIM_TEXT = "Claim found; wording arrives with the next result."
private const val MS_PER_SECOND = 1000
private const val SECONDS_PER_MINUTE = 60

internal fun clockLabel(ms: Long): String {
    val seconds = (ms / MS_PER_SECOND).coerceAtLeast(0)
    val remainder = (seconds % SECONDS_PER_MINUTE).toString().padStart(2, '0')
    return "${seconds / SECONDS_PER_MINUTE}:$remainder"
}

internal fun capturedCoverageLabel(coverage: LiveCoverage): String = buildString {
    append("${clockLabel(coverage.capturedMs)} captured, not the full video")
    if (coverage.missingMs > 0) append("; ${clockLabel(coverage.missingMs)} not received")
}

internal fun livePhaseLabel(phase: LiveSessionPhase): String = when (phase) {
    LiveSessionPhase.NOT_CONNECTED -> "Live results not connected"
    LiveSessionPhase.CAPTURING -> "Capturing; results update as segments are checked"
    LiveSessionPhase.CONTINUING -> "Stopped; research continues in queue"
    LiveSessionPhase.KEEPING_AVAILABLE -> "Stopped; only available results kept"
    LiveSessionPhase.CLOSED -> "Capture closed"
    LiveSessionPhase.ABANDONED -> "Capture ended without Stop"
    LiveSessionPhase.UNKNOWN -> "Unknown session state"
}

/** The pill's text after Stop, in place of the timer. */
internal fun afterStopLabel(phase: LiveSessionPhase): String =
    if (phase == LiveSessionPhase.CONTINUING) "Research continues" else livePhaseLabel(phase)

/** Phase line, plus the connection state when the source is retrying or gave up. */
internal fun liveStatusLabel(results: LiveResults): String {
    val connection = when (results.connection) {
        LiveConnection.OK -> ""
        LiveConnection.RETRYING -> "; reconnecting, showing the last results"
        LiveConnection.LOST -> "; connection lost, showing the last results"
        LiveConnection.ENDED -> "; stopped waiting for updates, showing the last results"
    }
    return livePhaseLabel(results.phase) + connection
}

internal fun emptyClaimsLabel(extraction: CoverageStatus?, phase: LiveSessionPhase): String = when {
    phase == LiveSessionPhase.NOT_CONNECTED -> "No live results. Research is not connected."
    extraction == CoverageStatus.NOT_STARTED -> "Waiting for the first captured segment."
    extraction == CoverageStatus.PARTIAL -> "Looking for checkable claims."
    extraction == CoverageStatus.COMPLETE -> NO_CLAIMS_TEXT
    else -> "Claim search state unknown."
}

private val StoppedEarly = setOf(LiveSessionPhase.KEEPING_AVAILABLE, LiveSessionPhase.ABANDONED)

/** State text for one claim. Unfinished claims after an early stop read as incomplete. */
internal fun liveStateLabel(claim: LiveClaim, phase: LiveSessionPhase): String {
    val stopped = phase in StoppedEarly
    return when (claim.state) {
        LiveClaimState.WAITING -> if (stopped) NOT_CHECKED else "Waiting"
        LiveClaimState.CHECKING_EVIDENCE -> if (stopped) NOT_CHECKED else "Checking evidence"
        LiveClaimState.PROVISIONAL -> if (stopped) "Provisional, incomplete" else "Provisional"
        LiveClaimState.UPDATED -> if (claim.complete) "Updated" else "Updated, still provisional"
        LiveClaimState.ASSESSED -> "Assessed"
        LiveClaimState.FAILED -> "Incomplete: check failed"
        LiveClaimState.CANCELLED -> "Incomplete: research stopped"
        LiveClaimState.UNKNOWN -> "Unknown state"
    }
}

internal fun overallLabel(overall: OverallAssessment): String = when (overall) {
    OverallAssessment.SUPPORTED -> "Supported"
    OverallAssessment.CHALLENGED -> "Challenged"
    OverallAssessment.QUALIFIED -> "Qualified"
    OverallAssessment.MIXED -> "Mixed evidence"
    OverallAssessment.INSUFFICIENT_EVIDENCE -> "Insufficient evidence"
    OverallAssessment.UNKNOWN -> "Unrecognized assessment"
}

internal fun assessmentLabel(assessment: LiveAssessment): String =
    overallLabel(assessment.overall) + if (assessment.provisional) ", provisional" else ", final"

internal fun claimDescription(claim: LiveClaim, phase: LiveSessionPhase): String = listOfNotNull(
    claim.startMs?.let { "At ${clockLabel(it)}" },
    claim.text ?: PENDING_CLAIM_TEXT,
    claim.assessment?.let(::assessmentLabel),
    liveStateLabel(claim, phase)
).joinToString(". ") { it.trimEnd('.') } + "."
