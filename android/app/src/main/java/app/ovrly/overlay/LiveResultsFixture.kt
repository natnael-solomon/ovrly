package app.ovrly.overlay

import app.ovrly.contract.Assessment
import app.ovrly.contract.CaptureClaimState
import app.ovrly.contract.CaptureManifest
import app.ovrly.contract.CaptureModalityCoverage
import app.ovrly.contract.CaptureSession
import app.ovrly.contract.CaptureSessionState
import app.ovrly.contract.CaptureStatus
import app.ovrly.contract.CaptureWork
import app.ovrly.contract.Claim
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Evidence
import app.ovrly.contract.EvidenceRelation
import app.ovrly.contract.EvidenceSource
import app.ovrly.contract.Interval
import app.ovrly.contract.Modality
import app.ovrly.contract.OverallAssessment
import app.ovrly.contract.ProcessingStatus
import app.ovrly.contract.Relation
import app.ovrly.contract.ReportVersion
import app.ovrly.contract.RetractionStatus
import app.ovrly.contract.RetrievalRelevance
import app.ovrly.contract.SourceInspectionLevel
import app.ovrly.contract.SourceType
import app.ovrly.contract.Timebase
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** One synthetic poll: the capture status and the latest report of its investigation. */
internal data class LiveFixtureStep(val status: CaptureStatus, val report: ReportVersion?)

/**
 * Synthetic live-capture timeline for development, previews and device screenshots. The
 * session comes from `packages/contracts/fixtures/intake/capture-status-waiting.json` (open,
 * attached to the `partial` investigation) and report version 1 is
 * `fixtures/results/partial.json` verbatim; `LiveResultsFixtureTest` checks both against the
 * committed files. Version 2 is an Android-side continuation of the same synthetic claims.
 * Nothing here was captured or researched.
 */
internal object LiveResultsFixture {
    const val INVESTIGATION_ID = "00000000-0000-4000-8000-000000000002"
    const val SPEECH_CLAIM = "clm_synthetic_0003"
    const val TEXT_CLAIM = "clm_synthetic_0004"
    private const val SESSION_ID = "00000000-0000-4000-8000-000000000501"
    private const val CHUNK_MS = 10_000L

    val session = CaptureSession(
        id = SESSION_ID,
        investigationId = INVESTIGATION_ID,
        state = CaptureSessionState.OPEN,
        timebase = Timebase.CAPTURE,
        startedAt = "2026-10-05T12:00:00Z",
        closedAt = null,
        maxDurationMs = 180_000,
        chunkDurationMs = CHUNK_MS,
        chunksReceived = 1,
        highestSeq = 0,
        receivedMs = CHUNK_MS,
        gaps = emptyList(),
        duplicateHandling = CaptureSession.DUPLICATE_HANDLING,
        outOfOrderHandling = CaptureSession.OUT_OF_ORDER_HANDLING
    )

    private val speechClaim = Claim(
        id = SPEECH_CLAIM,
        occurrenceId = "occ_synthetic_0003",
        interval = Interval(startMs = 4200, endMs = 9800, timebase = Timebase.CAPTURE),
        modality = Modality.SPEECH,
        originalText =
            "a recent survey found that nearly two thirds of city buses are electric now",
        proposition = "Nearly two thirds of the city's buses are electric, per a recent survey.",
        correction = null
    )

    private val textClaim = Claim(
        id = TEXT_CLAIM,
        occurrenceId = "occ_synthetic_0004",
        interval = Interval(startMs = 31000, endMs = 36000, timebase = Timebase.CAPTURE),
        modality = Modality.TEXT,
        originalText = "BUS FLEET: 0 DIESEL BY 2024",
        proposition = "The bus fleet had no diesel vehicles by 2024.",
        correction = null
    )

    private val fleetEvidence = Evidence(
        id = "evd_synthetic_0004",
        claimId = SPEECH_CLAIM,
        source = EvidenceSource(
            id = "src_synthetic_0004",
            title = "Synthetic source 4",
            publisher = "Synthetic Publisher",
            url = "https://sources.example/synthetic/0004",
            publishedAt = "2026-09-04T12:00:00Z"
        ),
        sourceType = SourceType.GOVERNMENT,
        inspectionLevel = SourceInspectionLevel.FULL_TEXT,
        retrievalRelevance = RetrievalRelevance.HIGH,
        retractionStatus = RetractionStatus.NONE,
        excerpt = "41 percent of the municipal bus fleet was battery-electric at end of 2025.",
        retrievedAt = "2026-10-04T12:02:00Z"
    )

    private val fleetRelation = EvidenceRelation(
        evidenceId = "evd_synthetic_0004",
        relation = Relation.CHALLENGE,
        note = "The fleet report gives 41 percent, well short of two thirds."
    )

    /** `fixtures/results/partial.json` report version 1. */
    val provisionalReport = ReportVersion(
        id = "rpt_synthetic_0002_v1",
        investigationId = INVESTIGATION_ID,
        version = 1,
        createdAt = "2026-10-04T12:00:45Z",
        provisional = true,
        changeSummary = "First partial results while the capture continues.",
        supersedes = null,
        claims = listOf(speechClaim, textClaim),
        evidence = listOf(fleetEvidence),
        assessments = listOf(
            Assessment(
                id = "asm_synthetic_0003",
                claimId = SPEECH_CLAIM,
                version = 1,
                relations = listOf(fleetRelation),
                overall = OverallAssessment.CHALLENGED,
                provisional = true,
                summary = "The official fleet report gives a much lower electric share; " +
                    "more may follow."
            )
        )
    )

    /** Version 1 before any assessment: both claims found, evidence still being checked. */
    private val checkingReport = provisionalReport.copy(assessments = emptyList())

    /** Synthetic version 2: the speech claim's provisional assessment is now final. */
    val updatedReport = ReportVersion(
        id = "rpt_synthetic_0002_v2",
        investigationId = INVESTIGATION_ID,
        version = 2,
        createdAt = "2026-10-04T12:01:30Z",
        provisional = true,
        changeSummary = "Checking finished for the bus share claim: no source gives a higher " +
            "share, so the assessment is no longer provisional. The on-screen claim is still " +
            "being checked.",
        supersedes = "rpt_synthetic_0002_v1",
        claims = listOf(speechClaim, textClaim),
        evidence = listOf(fleetEvidence),
        assessments = listOf(
            Assessment(
                id = "asm_synthetic_0005",
                claimId = SPEECH_CLAIM,
                version = 2,
                relations = listOf(fleetRelation),
                overall = OverallAssessment.CHALLENGED,
                provisional = false,
                summary = "The official fleet report gives 41 percent electric, " +
                    "well short of two thirds."
            ),
            Assessment(
                id = "asm_synthetic_0006",
                claimId = TEXT_CLAIM,
                version = 2,
                relations = emptyList(),
                overall = OverallAssessment.INSUFFICIENT_EVIDENCE,
                provisional = true,
                summary = "No source found yet on diesel buses in 2024."
            )
        )
    )

    /** Waiting, checking evidence, provisional, then updated, while the capture runs. */
    val timeline: List<LiveFixtureStep> = listOf(
        LiveFixtureStep(status(chunks = 1, emptyList(), CoverageStatus.NOT_STARTED), null),
        LiveFixtureStep(
            status(
                chunks = 4,
                listOf(
                    TEXT_CLAIM to ProcessingStatus.WAITING,
                    SPEECH_CLAIM to ProcessingStatus.CHECKING
                ),
                CoverageStatus.PARTIAL
            ),
            checkingReport
        ),
        LiveFixtureStep(
            status(
                chunks = 5,
                listOf(
                    SPEECH_CLAIM to ProcessingStatus.PARTIAL,
                    TEXT_CLAIM to ProcessingStatus.CHECKING
                ),
                CoverageStatus.PARTIAL
            ),
            provisionalReport
        ),
        LiveFixtureStep(
            status(
                chunks = 6,
                listOf(
                    SPEECH_CLAIM to ProcessingStatus.COMPLETE,
                    TEXT_CLAIM to ProcessingStatus.PARTIAL
                ),
                CoverageStatus.PARTIAL
            ),
            updatedReport
        )
    )

    /** [step] closed with the user's Stop choice; unfinished claims keep their state. */
    fun closed(step: LiveFixtureStep, continueResearch: Boolean): LiveFixtureStep = step.copy(
        status = step.status.copy(
            session = step.status.session.copy(
                state = CaptureSessionState.CLOSED,
                closedAt = "2026-10-05T12:00:40Z"
            ),
            continueResearch = continueResearch
        )
    )

    private fun status(
        chunks: Int,
        claims: List<Pair<String, ProcessingStatus>>,
        extraction: CoverageStatus
    ): CaptureStatus {
        val receivedMs = chunks * CHUNK_MS
        val captured = listOf(Interval(0, receivedMs, Timebase.CAPTURE))
        return CaptureStatus(
            session = session.copy(
                chunksReceived = chunks,
                highestSeq = chunks - 1,
                receivedMs = receivedMs
            ),
            continueResearch = null,
            expiresAt = "2026-10-05T12:18:00Z",
            manifest = CaptureManifest(
                durationMs = receivedMs.toInt(),
                missingIntervals = emptyList(),
                declaredCoverage = CaptureModalityCoverage(speech = captured, text = captured)
            ),
            work = (0 until chunks).map { seq ->
                CaptureWork(
                    seq = seq,
                    jobId = "00000000-0000-4000-8000-00000000060$seq",
                    processingStatus = if (seq == chunks - 1) {
                        ProcessingStatus.CHECKING
                    } else {
                        ProcessingStatus.COMPLETE
                    },
                    error = null
                )
            },
            claims = claims.map { (id, state) -> CaptureClaimState(id, state, null) },
            claimExtractionStatus = extraction
        )
    }
}

/**
 * Fixture-backed [LiveResultsSource] for development, previews and device screenshots. It is
 * labelled as such and is only reachable from previews, tests and the debug-only launcher.
 */
internal class FixtureLiveResultsSource(
    private val steps: List<LiveFixtureStep> = LiveResultsFixture.timeline
) : LiveResultsSource {
    private var index = 0
    private var current = steps.first()
    private var closed = false
    private val mutable = MutableStateFlow(reduce(current, null))

    override val results: StateFlow<LiveResults> = mutable.asStateFlow()
    override val label: String = LABEL

    /** Moves to the next poll; false once the timeline is exhausted or the session closed. */
    fun advance(): Boolean {
        if (closed || index >= steps.lastIndex) return false
        index += 1
        current = steps[index]
        mutable.value = reduce(current, mutable.value)
        return true
    }

    fun close(continueResearch: Boolean) {
        if (closed) return
        closed = true
        current = LiveResultsFixture.closed(current, continueResearch)
        mutable.value = reduce(current, mutable.value)
    }

    private fun reduce(step: LiveFixtureStep, previous: LiveResults?) =
        reduceLiveResults(step.status, step.report, previous)

    companion object {
        const val LABEL = "Fixture / not live"
    }
}
