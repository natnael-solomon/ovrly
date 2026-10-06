package app.ovrly.overlay

import app.ovrly.contract.Assessment
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Modality
import app.ovrly.contract.OverallAssessment
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.ReportVersion
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * What the compact overlay shows for one claim. [UNKNOWN] covers a processing status or an
 * assessment this contract version does not define; it is rendered neutrally and is never
 * [ASSESSED]. [FAILED] and [CANCELLED] are incomplete, not findings.
 */
internal enum class LiveClaimState {
    WAITING,
    CHECKING_EVIDENCE,
    PROVISIONAL,
    UPDATED,
    ASSESSED,
    FAILED,
    CANCELLED,
    UNKNOWN
}

/** The part of an [Assessment] the overlay shows and compares between polls. */
internal data class LiveAssessment(
    val version: Int,
    val overall: OverallAssessment,
    val provisional: Boolean,
    val summary: String
)

/** A provisional assessment the user saw, what replaced it and the report's reason. */
internal data class LiveAssessmentChange(
    val before: LiveAssessment,
    val after: LiveAssessment,
    val reason: String
)

internal data class LiveClaim(
    val id: String,
    /** Normalized proposition; null until the report carries the claim. */
    val text: String?,
    /** Capture-relative start, used for spoken order; null until the report carries it. */
    val startMs: Long?,
    val modality: Modality?,
    val state: LiveClaimState,
    val assessment: LiveAssessment?,
    val change: LiveAssessmentChange?
) {
    /** True only for a final, known assessment. Everything else stays visibly incomplete. */
    val complete: Boolean
        get() = assessment != null &&
            !assessment.provisional &&
            (state == LiveClaimState.ASSESSED || state == LiveClaimState.UPDATED)
}

internal enum class LiveSessionPhase {
    /** No live source is connected; nothing here comes from research. */
    NOT_CONNECTED,
    CAPTURING,

    /** Closed with `continue_research: true`. */
    CONTINUING,

    /** Closed with `continue_research: false`. */
    KEEPING_AVAILABLE,

    /** Closed without a recorded choice. */
    CLOSED,
    ABANDONED,
    UNKNOWN
}

/**
 * Health of the live source. Earlier results stay visible while it is not [OK]. [ENDED]
 * means polling stopped at its time bound while research was still pending.
 */
internal enum class LiveConnection { OK, RETRYING, LOST, ENDED }

/** Captured interval length and the part of it the server never received. */
internal data class LiveCoverage(val capturedMs: Long, val missingMs: Long)

internal data class LiveResults(
    val phase: LiveSessionPhase,
    val coverage: LiveCoverage?,
    val extraction: CoverageStatus?,
    /** Claims in spoken order: capture-relative start, then status order. */
    val claims: List<LiveClaim>,
    val connection: LiveConnection = LiveConnection.OK
) {
    companion object {
        val NotConnected = LiveResults(LiveSessionPhase.NOT_CONNECTED, null, null, emptyList())
    }
}

/**
 * Session state and claims for the compact overlay. Real sources poll the capture session
 * status; fixture sources set [label] so their content is never mistaken for live research.
 */
internal interface LiveResultsSource {
    val results: StateFlow<LiveResults>
    val label: String?
}

/**
 * The live adapter until the #18 HTTP client and #26 session wiring land. It reports
 * [LiveSessionPhase.NOT_CONNECTED] and never emits claims.
 */
internal object NotConnectedLiveResultsSource : LiveResultsSource {
    override val results: StateFlow<LiveResults> =
        MutableStateFlow(LiveResults.NotConnected).asStateFlow()
    override val label: String? = null
}

internal fun liveSessionPhase(
    state: CaptureSessionState,
    continueResearch: Boolean?
): LiveSessionPhase = when (state) {
    CaptureSessionState.OPEN -> LiveSessionPhase.CAPTURING

    CaptureSessionState.CLOSED -> when (continueResearch) {
        true -> LiveSessionPhase.CONTINUING
        false -> LiveSessionPhase.KEEPING_AVAILABLE
        null -> LiveSessionPhase.CLOSED
    }

    CaptureSessionState.ABANDONED -> LiveSessionPhase.ABANDONED

    CaptureSessionState.UNKNOWN -> LiveSessionPhase.UNKNOWN
}

/** Per-claim state before update detection. A missing status is unknown, not waiting. */
internal fun liveClaimState(status: ProcessingStatus?, assessment: Assessment?): LiveClaimState {
    val known = assessment?.takeIf { it.overall != OverallAssessment.UNKNOWN }
    return when (status) {
        null, ProcessingStatus.UNKNOWN -> LiveClaimState.UNKNOWN

        ProcessingStatus.WAITING -> LiveClaimState.WAITING

        ProcessingStatus.CHECKING -> LiveClaimState.CHECKING_EVIDENCE

        ProcessingStatus.PARTIAL -> when {
            assessment == null -> LiveClaimState.CHECKING_EVIDENCE
            known == null -> LiveClaimState.UNKNOWN
            else -> LiveClaimState.PROVISIONAL
        }

        ProcessingStatus.COMPLETE -> when {
            known == null -> LiveClaimState.UNKNOWN
            known.provisional -> LiveClaimState.PROVISIONAL
            else -> LiveClaimState.ASSESSED
        }

        ProcessingStatus.FAILED -> LiveClaimState.FAILED

        ProcessingStatus.CANCELLED -> LiveClaimState.CANCELLED
    }
}

/**
 * Folds one status poll and the latest report of the same investigation into overlay state.
 * A claim whose provisional assessment was shown in [previous] and has since been replaced
 * becomes [LiveClaimState.UPDATED] and keeps that change until it is replaced again. A
 * report for another investigation is ignored.
 */
internal fun reduceLiveResults(
    status: CaptureStatus,
    report: ReportVersion?,
    previous: LiveResults?
): LiveResults {
    val matching = report?.takeIf { it.investigationId == status.session.investigationId }
    val prior = previous?.claims?.associateBy { it.id }.orEmpty()
    val statuses = status.claims.associate { it.claimId to it.processingStatus }
    val reportClaims = matching?.claims?.associateBy { it.id }.orEmpty()
    val ids = LinkedHashSet<String>().apply {
        status.claims.forEach { add(it.claimId) }
        matching?.claims?.forEach { add(it.id) }
    }
    val claims = ids.map { id ->
        val claim = reportClaims[id]
        val assessment = matching?.assessmentFor(id)
        liveClaim(
            id = id,
            base = liveClaimState(statuses[id], assessment),
            assessment = assessment,
            before = prior[id],
            reason = matching?.changeSummary.orEmpty()
        ).copy(
            text = claim?.proposition,
            startMs = claim?.interval?.startMs,
            modality = claim?.modality
        )
    }
    return LiveResults(
        phase = liveSessionPhase(status.session.state, status.continueResearch),
        coverage = LiveCoverage(
            capturedMs = status.manifest.durationMs.toLong(),
            missingMs = status.manifest.missingIntervals.sumOf { it.endMs - it.startMs }
        ),
        extraction = status.claimExtractionStatus,
        claims = claims.sortedWith(compareBy<LiveClaim, Long?>(nullsLast()) { it.startMs })
    )
}

private val ShownAssessments = setOf(LiveClaimState.PROVISIONAL, LiveClaimState.ASSESSED)

private fun liveClaim(
    id: String,
    base: LiveClaimState,
    assessment: Assessment?,
    before: LiveClaim?,
    reason: String
): LiveClaim {
    val current = assessment
        ?.takeIf { base in ShownAssessments }
        ?.let { LiveAssessment(it.version, it.overall, it.provisional, it.summary) }
    val shown = before?.assessment
    val change = when {
        current == null || before == null || shown == null -> null
        replacesProvisional(before, current) -> LiveAssessmentChange(shown, current, reason)
        shown == current -> before.change
        else -> null
    }
    return LiveClaim(
        id = id,
        text = null,
        startMs = null,
        modality = null,
        state = if (change != null) LiveClaimState.UPDATED else base,
        assessment = current,
        change = change
    )
}

private fun replacesProvisional(before: LiveClaim, current: LiveAssessment): Boolean {
    val shown = before.assessment ?: return false
    val wasProvisional =
        before.state == LiveClaimState.PROVISIONAL || before.state == LiveClaimState.UPDATED
    return wasProvisional && shown.provisional && shown != current
}

/** Claims whose change is new in [next] compared with [previous]: the update-notice trigger. */
internal fun newAssessmentUpdates(previous: LiveResults?, next: LiveResults): List<LiveClaim> {
    val before = previous?.claims?.associateBy { it.id }.orEmpty()
    return next.claims.filter { it.change != null && it.change != before[it.id]?.change }
}
