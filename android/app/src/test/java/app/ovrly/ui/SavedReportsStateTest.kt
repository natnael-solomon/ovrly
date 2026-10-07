package app.ovrly.ui

import app.ovrly.contract.ContractError
import app.ovrly.contract.ContractErrorAction
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.ReportVersion
import app.ovrly.data.ApiFailure
import app.ovrly.data.ApiTestServer
import app.ovrly.data.InvestigationRecord
import app.ovrly.data.LinkOutcome
import app.ovrly.data.LocalJobState
import app.ovrly.data.SIGN_IN_UNAVAILABLE
import app.ovrly.data.SavedFixtures
import app.ovrly.data.SavedReportEntry
import app.ovrly.data.StoredCheck
import app.ovrly.data.StoredSave
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Saved reports and account copy (AN-10, #36), from the shared result fixtures. */
class SavedReportsStateTest {
    private val report = SavedFixtures.report
    private val other = SavedFixtures.other
    private val now = 1_791_200_000_000L

    private fun save(
        report: ReportVersion = this.report,
        savedAt: String = "2026-10-07T00:00:00Z",
        readable: Boolean = true
    ) = StoredSave(
        SavedReportEntry(
            reportId = report.id,
            investigationId = report.investigationId,
            version = report.version,
            savedAt = savedAt,
            json = InvestigationCodec.encodeReportVersion(report),
            storedAt = 1L
        ),
        report.takeIf { readable }
    )

    private fun check(name: String): StoredCheck {
        val investigation = InvestigationCodec.parseInvestigation(
            ApiTestServer.result(name).investigationPayload()
        )
        val record = InvestigationRecord(
            localId = "local-${investigation.id}",
            serverId = investigation.id,
            state = LocalJobState.SUCCEEDED.wireName,
            sourceKind = investigation.source.kind.wireName,
            idempotencyKey = "synthetic-key",
            createdAt = now,
            updatedAt = now
        )
        return StoredCheck(record, investigation)
    }

    @Test
    fun savedRowsSayWhichVersionWhenAndWhetherTheCheckIsOnThisDevice() {
        val savedAt = "2026-10-07T00:00:00Z"
        val savedMillis = epochMillis(savedAt)!!
        val items = savedItems(
            listOf(save(savedAt = savedAt), save(other), save(other, readable = false)),
            listOf(check("complete")),
            savedMillis + 4 * 60_000
        )
        val first = items[0]
        assertEquals("Link from video.example", first.title)
        assertEquals("Version 2. Saved 4 min ago", first.detail)
        assertTrue(first.onDevice)
        assertTrue(first.readable)

        val notHere = items[1]
        assertEquals(SAVED_TITLE, notHere.title)
        assertFalse(notHere.onDevice)
        assertTrue(notHere.detail.startsWith("Version 1. Saved"))

        val unreadable = items[2]
        assertFalse(unreadable.readable)
        assertTrue(unreadable.detail.endsWith("Uses values $NOT_RECOGNISED"))
    }

    @Test
    fun theSaveControlFollowsTheShownVersion() {
        val complete = check("complete").investigation!!
        val view = reportView(complete)
        assertEquals(report.id, view.reportId)

        val unsaved = saveState(view, emptyList())!!
        assertFalse(unsaved.saved)
        assertEquals(2, unsaved.version)
        assertNull(unsaved.otherSavedVersion)

        val saved = saveState(view, listOf(save()))!!
        assertTrue(saved.saved)
        assertFalse(saved.copy)

        // Another saved version of the same check is mentioned while this one is not saved.
        val earlier = save().let {
            it.copy(entry = it.entry.copy(reportId = "rpt_v1", version = 1))
        }
        assertEquals(1, saveState(view, listOf(earlier))!!.otherSavedVersion)
        assertNull(saveState(view, listOf(earlier, save()))!!.otherSavedVersion)

        val failed = check("failed").investigation!!
        assertNull(saveState(reportView(failed), listOf(save())))
    }

    @Test
    fun aSavedCopyIsReadOnlyAndSaysItDoesNotChange() {
        val view = savedCopyView(save())!!
        assertEquals(SAVED_COPY, view.work.status)
        assertTrue(view.work.message!!.contains("does not change"))
        assertFalse(view.canCorrect)
        assertFalse(view.captured)
        assertEquals(report.version, view.version)
        assertEquals(report.version, view.latestVersion)
        assertEquals(report.claims.size, view.claims.size)
        assertEquals(report.id, view.reportId)
        val coverage = checkNotNull(view.coverage)
        assertFalse(coverage.stale)
        assertEquals(report.provisional, coverage.provisional)
        assertNull(savedCopyView(save(readable = false)))
        assertTrue(saveState(view, listOf(save()), copy = true)!!.copy)

        val noClaims = SavedFixtures.fixtureReport("no-claims")
        val empty = savedCopyView(save(noClaims))!!
        assertTrue(empty.claims.isEmpty())
        assertNotNull(empty.empty)
        assertTrue(empty.empty!!.contains("not a finding that the video is accurate"))
    }

    @Test
    fun linkResultsReadPlainlyAndNeverClaimRecoveryOnAnotherDevice() {
        assertNull(linkNotice(LinkOutcome.Cancelled))
        assertEquals(SIGN_IN_UNAVAILABLE, linkNotice(LinkOutcome.Unavailable(SIGN_IN_UNAVAILABLE)))
        assertEquals(
            "Linked. Reports you save are kept for your Google account.",
            linkNotice(LinkOutcome.Linked(switched = false, merged = 0, stored = true))
        )
        val switched = linkNotice(LinkOutcome.Linked(switched = true, merged = 1, stored = false))!!
        assertTrue(switched.contains("1 saved report from this device moved"))
        assertTrue(switched.contains("check history is not merged"))
        assertTrue(switched.contains("could not be stored on this device"))
        assertTrue(
            linkNotice(LinkOutcome.Linked(switched = true, merged = 3, stored = true))!!
                .contains("3 saved reports")
        )
        val codes = mapOf(
            "ACCOUNT_ALREADY_LINKED" to "already linked to a different Google account",
            "INVALID_ID_TOKEN" to "could not be verified",
            "ACCOUNT_LINK_UNAVAILABLE" to "Checking still works",
            "INVALID_CREDENTIAL" to "Nothing was linked",
            "SOMETHING_NEW" to "Synthetic (SOMETHING_NEW)"
        )
        codes.forEach { (code, text) ->
            val error = ContractError(code, "Synthetic", false, ContractErrorAction.NONE, "req-1")
            val notice = linkNotice(LinkOutcome.Failed(ApiFailure.Server(400, error)))!!
            assertTrue("$code: $notice", notice.contains(text))
        }
        val offline = linkNotice(LinkOutcome.Failed(ApiFailure.Network("req-2", "timeout")))
        assertTrue(offline!!.startsWith("You are offline"))

        assertTrue(RECOVERY_DISCLOSURE.contains("cannot yet be restored on another device"))
        assertTrue(SAVED_SCOPE.contains("Only reports you save are kept"))
    }
}
