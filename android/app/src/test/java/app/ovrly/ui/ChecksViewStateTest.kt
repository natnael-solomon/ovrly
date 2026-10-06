package app.ovrly.ui

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ReportVersion
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LocalJobState
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Inbox, Library and Report view state for the six shared result fixtures (#34). */
class ChecksViewStateTest {
    private val now = epochMillis("2026-10-04T12:30:00Z")!!

    private fun payload(name: String): JsonElement = ContractFixtures.element(
        ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == name }
            .investigationPayload()
    )

    private fun fixture(name: String): Investigation =
        InvestigationCodec.parseInvestigation(payload(name).toString())

    private fun replaced(name: String, vararg changes: Pair<List<String>, JsonElement>) =
        InvestigationCodec.parseInvestigation(
            changes.fold(payload(name)) { json, (path, value) ->
                ContractFixtures.replace(json, path, value)
            }.toString()
        )

    private fun record(investigation: Investigation) = InvestigationRecord(
        localId = "local-${investigation.id}",
        serverId = investigation.id,
        state = LocalJobState.ACCEPTED.wireName,
        sourceKind = investigation.source.kind.wireName,
        idempotencyKey = "synthetic-key",
        createdAt = 0,
        updatedAt = 0
    )

    private fun item(investigation: Investigation) =
        inboxItem(record(investigation), investigation, now)

    private val verdictTones = setOf(Tone.SUPPORT, Tone.CHALLENGE, Tone.QUALIFY, Tone.MIXED)

    @Test
    fun completeShowsTheCorrectedVersionWithItsOriginalWordingAndEvidence() {
        val view = reportView(fixture("complete"))
        assertEquals("Complete", view.work.status)
        assertNull(view.work.stage)
        val coverage = view.coverage!!
        assertEquals("All of the media was checked", coverage.coverage.text)
        assertEquals("3:05 of 3:05 checked", coverage.amount)
        assertFalse(coverage.provisional)
        assertEquals(2, view.version)
        assertEquals("rpt_synthetic_0001_v1", view.supersedes)
        assertTrue(view.canCorrect)
        assertFalse(view.captured)
        val (first, second) = view.claims
        assertEquals("Corrected by you", first.correction)
        assertEquals(
            "Two thirds of the country's buses are electric.",
            first.supersededProposition
        )
        assertEquals(
            "a recent survey found that nearly two thirds of city buses are electric now",
            first.originalText
        )
        assertEquals("0:12 to 0:18 in the video", first.interval)
        assertEquals("Spoken", first.modality)
        assertEquals(Label("Sources support this claim", Tone.SUPPORT), first.assessment)
        assertEquals(2, first.evidence.size)
        val evidence = first.evidence.first()
        assertEquals("Full text read", evidence.access)
        assertEquals("Government source", evidence.sourceType)
        assertNull(evidence.retraction)
        assertEquals("Supports the claim", evidence.relation.text)
        assertEquals(
            "The fleet report gives 64 percent battery-electric at end of 2025.",
            evidence.rationale
        )
        assertTrue(evidence.passage!!.startsWith("64 percent"))
        assertEquals("On-screen text", second.modality)
        assertEquals(Tone.QUALIFY, second.assessment.tone)
        assertEquals("Qualifies the claim", second.evidence.single().relation.text)
        val row = item(fixture("complete"))
        assertTrue(row.library)
        assertEquals(listOf(InboxAction.OPEN), row.actions)
        assertEquals("Link from video.example", row.title)
    }

    @Test
    fun partialIsProvisionalOnTheCaptureTimelineWithAnUnassessedClaim() {
        val investigation = fixture("partial")
        val view = reportView(investigation)
        assertEquals("Partial results ready", view.work.status)
        assertEquals("Finding sources", view.work.stage)
        val coverage = view.coverage!!
        assertTrue(coverage.provisional)
        assertEquals(Tone.WARNING, coverage.coverage.tone)
        assertEquals("1:00 checked", coverage.amount)
        assertTrue(view.captured)
        val (assessed, pending) = view.claims
        assertTrue(assessed.interval.endsWith("(not a time in the original video)"))
        assertFalse(assessed.interval.contains("in the video"))
        assertEquals("Sources challenge this claim (provisional)", assessed.assessment.text)
        assertEquals(1, assessed.contradicting)
        assertEquals("Contradicts the claim", assessed.evidence.single().relation.text)
        assertEquals(Label("Not assessed yet"), pending.assessment)
        assertTrue(pending.evidence.isEmpty())
        val row = item(investigation)
        assertFalse(row.library)
        assertEquals(listOf(InboxAction.OPEN, InboxAction.CANCEL), row.actions)
        assertEquals("Finding sources", row.stage)
        assertEquals("Started 30 min ago", row.age)
        assertEquals("2 claims, 1 assessed so far", row.detail)
        assertTrue(row.captured)
    }

    @Test
    fun failedShowsTheErrorAndNoFindings() {
        val investigation = fixture("failed")
        val view = reportView(investigation)
        assertEquals("Could not be checked", view.work.status)
        assertEquals(Tone.WARNING, view.work.tone)
        assertEquals("Processing failed (MEDIA_UNSUPPORTED).", view.work.message)
        assertNull(view.coverage)
        assertTrue(view.claims.isEmpty())
        assertEquals("No results: the check could not be completed.", view.empty)
        assertFalse(view.canCorrect)
        val row = item(investigation)
        assertEquals(listOf(InboxAction.OPEN), row.actions)
        assertFalse(row.library)
        assertEquals("Processing failed (MEDIA_UNSUPPORTED)", row.detail)
    }

    @Test
    fun aRetryableFailureOffersRetry() {
        val retryable = replaced("failed", listOf("error", "retryable") to JsonPrimitive(true))
        assertEquals(listOf(InboxAction.OPEN, InboxAction.RETRY), actionsFor(retryable))
        assertTrue(reportView(retryable).work.message!!.endsWith("You can try again."))
    }

    @Test
    fun cancelledWithoutAReportOffersANewCheckAndNoFindings() {
        val investigation = fixture("cancelled")
        val view = reportView(investigation)
        assertEquals("Cancelled", view.work.status)
        assertNull(view.coverage)
        assertEquals("The check was cancelled before any results were published.", view.empty)
        val row = item(investigation)
        assertEquals(listOf(InboxAction.OPEN, InboxAction.RETRY), row.actions)
        assertEquals("Cancelled before any results were published.", row.detail)
        assertFalse(row.library)
    }

    @Test
    fun aFixtureVersionIsLabelledFromTheReportItselfNeverAsLiveResults() {
        val stub = replaced("complete", listOf("report", "fixture") to JsonPrimitive(true))
        assertTrue(stub.report!!.fixture)
        assertTrue(reportView(stub).coverage!!.fixture)
        assertTrue(item(stub).detail!!.startsWith("Development fixture, not a check"))
        val real = fixture("complete")
        assertFalse(reportView(real).coverage!!.fixture)
        assertEquals("2 claims", item(real).detail)
    }

    @Test
    fun aCheckAlreadyRetriedOffersTheNewCheckInsteadOfAnotherRetry() {
        val cancelled = fixture("cancelled")
        val retried = record(cancelled).copy(retriedAs = "00000000-0000-4000-8000-000000000401")
        val row = inboxItem(retried, cancelled, now)
        assertEquals(listOf(InboxAction.OPEN, InboxAction.OPEN_RETRY), row.actions)
        assertEquals("00000000-0000-4000-8000-000000000401", row.retriedAs)
        assertTrue(row.detail!!.endsWith("Checked again as a new check"))
    }

    @Test
    fun cancelledAfterPublishingOffersContinue() {
        val complete = payload("complete")
        val cancelled = InvestigationCodec.parseInvestigation(
            listOf(
                listOf("processing_status") to JsonPrimitive("cancelled"),
                listOf("state") to JsonPrimitive("cancelled"),
                listOf("job", "state") to JsonPrimitive("cancelled")
            ).fold(complete) { json, (path, value) -> ContractFixtures.replace(json, path, value) }
                .toString()
        )
        assertEquals(listOf(InboxAction.OPEN, InboxAction.CONTINUE), actionsFor(cancelled))
        assertTrue(item(cancelled).library)
    }

    @Test
    fun insufficientEvidenceWarnsAboutRetractedAndShallowSources() {
        val view = reportView(fixture("insufficient-evidence"))
        val claim = view.claims.single()
        assertEquals(Label("Not enough sound evidence", Tone.INSUFFICIENT), claim.assessment)
        val warnings = claim.evidence.mapNotNull { it.retraction?.text }
        assertEquals(listOf("Retracted: do not rely on this source"), warnings)
        assertEquals(
            setOf("Abstract only", "Title and metadata only"),
            claim.evidence.map { it.access }.toSet()
        )
        claim.evidence.forEach {
            assertEquals(Tone.INSUFFICIENT, it.relation.tone)
            assertFalse(it.relation.tone in verdictTones)
        }
    }

    @Test
    fun noClaimsIsNeverAVerdictOnTheVideo() {
        val investigation = fixture("no-claims")
        val view = reportView(investigation)
        assertTrue(view.claims.isEmpty())
        assertTrue(view.empty!!.contains("not a finding that the video is accurate"))
        assertEquals("All of the media was checked", view.coverage!!.coverage.text)
        val row = item(investigation)
        assertEquals("No checkable claims found", row.detail)
        assertTrue(row.library)
    }

    @Test
    fun unknownContractValuesReadNeutrallyAndNeverAsAVerdict() {
        val future = JsonPrimitive("__future_value__")
        val claim = listOf("report", "claims", "0")
        val evidence = listOf("report", "evidence", "0")
        val investigation = replaced(
            "complete",
            listOf("stage") to future,
            listOf("coverage", "status") to future,
            claim + "modality" to future,
            claim + listOf("interval", "timebase") to future,
            claim + listOf("correction", "attributed_to") to future,
            evidence + "inspection_level" to future,
            evidence + "source_type" to future,
            evidence + "retraction_status" to future,
            evidence + "retrieval_relevance" to future,
            listOf("report", "assessments", "0", "overall") to future,
            listOf("report", "assessments", "0", "relations", "0", "relation") to future
        )
        val view = reportView(investigation)
        assertEquals(Label("Coverage $NOT_RECOGNISED"), view.coverage!!.coverage)
        val first = view.claims.first()
        assertEquals(Label("Assessment $NOT_RECOGNISED"), first.assessment)
        assertTrue(first.modality.endsWith(NOT_RECOGNISED))
        assertTrue(first.interval.endsWith("timeline $NOT_RECOGNISED"))
        assertTrue(first.correction!!.endsWith(NOT_RECOGNISED))
        val unknown = first.evidence.single { it.id == "evd_synthetic_0001" }
        assertEquals(Label("Relation $NOT_RECOGNISED"), unknown.relation)
        assertEquals(Label("Retraction status $NOT_RECOGNISED"), unknown.retraction)
        assertTrue(unknown.access.endsWith(NOT_RECOGNISED))
        assertTrue(unknown.sourceType.endsWith(NOT_RECOGNISED))
        assertTrue(unknown.relevance.endsWith(NOT_RECOGNISED))
        // The known evidence beside it keeps its label; the unknown one sorts after it.
        assertEquals("Supports the claim", first.evidence.first().relation.text)
    }

    @Test
    fun anUnknownStatusIsNeitherCompleteNorCancellable() {
        val future = JsonPrimitive("__future_value__")
        val investigation = replaced(
            "partial",
            listOf("processing_status") to future,
            listOf("job", "state") to future
        )
        val view = reportView(investigation)
        assertEquals("Status $NOT_RECOGNISED", view.work.status)
        assertEquals(Tone.NEUTRAL, view.work.tone)
        assertEquals("This status is $NOT_RECOGNISED.", view.work.message)
        val row = item(investigation)
        assertFalse(row.library)
        assertFalse(InboxAction.CANCEL in row.actions)
    }

    @Test
    fun aRequestedCancelShowsStoppingAndNoSecondCancel() {
        val stopping = replaced("partial", listOf("job", "cancel_requested") to JsonPrimitive(true))
        val row = item(stopping)
        assertEquals("Stopping", row.status)
        assertEquals(listOf(InboxAction.OPEN), row.actions)
        assertEquals("Stopping", reportView(stopping).work.status)
    }

    @Test
    fun claimChangesCompareWithTheVersionBefore() {
        val current = fixture("complete").report!!
        val report = (payload("complete") as JsonObject).getValue("report")
        val earlier = InvestigationCodec.parseReportVersion(
            listOf(
                listOf("version") to JsonPrimitive(1),
                listOf("id") to JsonPrimitive("rpt_synthetic_0001_v1"),
                listOf("supersedes") to JsonNull,
                listOf("claims", "0", "proposition") to
                    JsonPrimitive("Two thirds of the country's buses are electric."),
                listOf("claims", "0", "correction") to JsonNull,
                listOf("assessments", "0", "version") to JsonPrimitive(1),
                listOf("assessments", "0", "overall") to JsonPrimitive("mixed"),
                listOf("assessments", "1", "version") to JsonPrimitive(1)
            ).fold(report) { json, (path, value) ->
                ContractFixtures.replace(json, path, value)
            }.toString()
        )
        val changed = claimChanges("clm_synthetic_0001", earlier, current)
        assertEquals(
            listOf(
                "Meaning in version 1: \"Two thirds of the country's buses are electric.\"",
                "Assessment in version 1: Sources disagree"
            ),
            changed
        )
        assertEquals(
            listOf("No change to this claim since version 1."),
            claimChanges("clm_synthetic_0002", earlier, current)
        )
        assertEquals(
            listOf("Not in version 2."),
            claimChanges("clm_synthetic_0002", earlier, withoutSecondClaim(current))
        )
    }

    private fun withoutSecondClaim(report: ReportVersion) = report.copy(
        claims = report.claims.take(1),
        evidence = report.evidence.filter { it.claimId == "clm_synthetic_0001" },
        assessments = report.assessments.take(1)
    )

    @Test
    fun correctionsMustChangeTheMeaningWithinTheLimit() {
        val current = "The bus fleet had no diesel vehicles by 2024."
        assertEquals("Write what the claim means.", correctionProblem(current, "  "))
        assertEquals("This is the same meaning as now.", correctionProblem(current, " $current "))
        assertTrue(correctionProblem(current, "x".repeat(2001))!!.startsWith("Keep it under"))
        assertNull(correctionProblem(current, "Scheduled service had no diesel buses by 2024."))
    }

    @Test
    fun localSharesShowTheirOwnStateNotAServerStatus() {
        val pending = InvestigationRecord(
            localId = "local-1",
            state = LocalJobState.LOCAL_PENDING.wireName,
            sourceKind = "url",
            sourceUrl = "https://www.video.example/synthetic",
            idempotencyKey = "synthetic-key",
            createdAt = now - 120_000,
            updatedAt = now
        )
        val row = inboxItem(pending, null, now)
        assertEquals("Waiting to upload from this device", row.status)
        assertEquals("Shared 2 min ago", row.age)
        assertEquals("Link from video.example", row.title)
        assertEquals(listOf(InboxAction.RETRY), row.actions)
        val failed = inboxItem(pending.copy(state = LocalJobState.FAILED.wireName), null, now)
        assertEquals(Tone.WARNING, failed.tone)
        assertTrue(failed.actions.isEmpty())
    }

    @Test
    fun ageIsRelativeAndNeverAQueuePosition() {
        assertEquals("just now", age(now, now))
        assertEquals("just now", age(now + 5_000, now))
        assertEquals("59 min ago", age(now - 59 * 60_000L, now))
        assertEquals("3 h ago", age(now - 3 * 3_600_000L, now))
        assertEquals("1 day ago", age(now - 25 * 3_600_000L, now))
        assertEquals("4 days ago", age(now - 4 * 86_400_000L, now))
    }

    @Test
    fun onlyLaterSharedChecksAreFullVideoCandidates() {
        val captured = item(fixture("partial"))
        val laterShare = item(fixture("complete")).copy(createdAt = captured.createdAt + 1)
        val earlierShare = laterShare.copy(serverId = "earlier", createdAt = captured.createdAt - 1)
        val otherCapture = laterShare.copy(serverId = "capture-2", captured = true)
        val local = laterShare.copy(serverId = null)
        assertTrue(laterShare.fullVideoSource)
        assertEquals(
            listOf(laterShare),
            fullVideoCandidates(
                captured,
                listOf(captured, laterShare, earlierShare, otherCapture, local)
            )
        )
    }

    @Test
    fun failedCancelledAndUnknownSourceChecksAreNeverFullVideoCandidates() {
        val captured = item(fixture("partial"))
        val later = { name: String -> item(fixture(name)).copy(createdAt = captured.createdAt + 1) }
        val failed = later("failed")
        val cancelled = later("cancelled")
        val unknownSource = item(
            replaced(
                "complete",
                listOf("source") to JsonObject(mapOf("kind" to JsonPrimitive("__future_kind__")))
            )
        ).copy(createdAt = captured.createdAt + 1)
        val usable = later("insufficient-evidence")
        listOf(failed, cancelled, unknownSource).forEach { assertFalse(it.fullVideoSource) }
        assertTrue(usable.fullVideoSource)
        assertEquals(
            listOf(usable),
            fullVideoCandidates(captured, listOf(failed, cancelled, unknownSource, usable))
        )
    }
}
