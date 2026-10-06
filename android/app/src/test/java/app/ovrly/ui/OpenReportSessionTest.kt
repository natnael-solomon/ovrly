package app.ovrly.ui

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.data.ReanalysisKeys
import app.ovrly.data.ReanalysisRequest
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A slow report load never brings back a report the user closed or replaced (#34). */
class OpenReportSessionTest {
    private fun fixture(name: String): Investigation = InvestigationCodec.parseInvestigation(
        ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == name }
            .investigationPayload()
    )

    private val complete = fixture("complete")
    private val partial = fixture("partial")

    /** A session whose loads wait until the test releases them. */
    private class Gate {
        val release = CompletableDeferred<Unit>()
        val versions = mutableListOf<Int?>()
        val session = OpenReportSession { investigation, version, _ ->
            versions += version
            release.await()
            OpenReport(reportView(investigation))
        }
    }

    @Test
    fun aRefreshShowsTheOpenReportWithBusyAndKeepsTheNotice() = runBlocking {
        val session = OpenReportSession { investigation, _, _ ->
            OpenReport(reportView(investigation))
        }
        session.open(complete.id)
        session.refresh(complete, emptyList(), busy = false)
        session.update { it.copy(notice = "Saved") }
        session.refresh(complete, emptyList(), busy = true)
        val shown = session.report.value!!
        assertEquals(complete.id, shown.view.investigationId)
        assertTrue(shown.busy)
        assertEquals("Saved", shown.notice)
    }

    @Test
    fun closingDuringALoadKeepsTheReportClosed() = runBlocking {
        val gate = Gate()
        gate.session.open(complete.id)
        val loading = launch(start = CoroutineStart.UNDISPATCHED) {
            gate.session.refresh(complete, emptyList(), busy = false)
        }
        gate.session.close()
        gate.release.complete(Unit)
        loading.join()
        assertNull(gate.session.report.value)
        assertNull(gate.session.openId)
    }

    @Test
    fun openingAnotherReportDuringALoadDropsTheOldOne() = runBlocking {
        val gate = Gate()
        gate.session.open(complete.id)
        val loading = launch(start = CoroutineStart.UNDISPATCHED) {
            gate.session.refresh(complete, emptyList(), busy = false)
        }
        gate.session.open(partial.id)
        gate.release.complete(Unit)
        loading.join()
        assertNull(gate.session.report.value)
        gate.session.refresh(partial, emptyList(), busy = false)
        assertEquals(partial.id, gate.session.report.value!!.view.investigationId)
    }

    @Test
    fun aVersionChosenDuringALoadWinsOverTheOlderLoad() = runBlocking {
        val stale = CompletableDeferred<Unit>()
        val slow = OpenReportSession { investigation, version, _ ->
            if (version == null) stale.await()
            OpenReport(reportView(investigation)).copy(comparedWith = version)
        }
        slow.open(complete.id)
        val old = launch(start = CoroutineStart.UNDISPATCHED) {
            slow.refresh(complete, emptyList(), busy = false)
        }
        slow.show(1)
        slow.refresh(complete, emptyList(), busy = false)
        stale.complete(Unit)
        old.join()
        assertEquals(1, slow.report.value!!.comparedWith)
    }

    @Test
    fun aReportNotStoredYetShowsNothing() = runBlocking {
        val gate = Gate()
        gate.release.complete(Unit)
        gate.session.open(partial.id)
        gate.session.refresh(complete, emptyList(), busy = false)
        assertNull(gate.session.report.value)
        gate.session.refresh(null, emptyList(), busy = false)
        assertNull(gate.session.report.value)
        assertTrue(gate.versions.isEmpty())
    }

    @Test
    fun reanalysisKeysAreStablePerRequestAndNewPerChoice() {
        var next = 0
        val keys = ReanalysisKeys { "synthetic-key-${next++}" }
        val video = "00000000-0000-4000-8000-000000000401"
        val other = "00000000-0000-4000-8000-000000000402"
        val first = keys.keyFor(partial.id, ReanalysisRequest.Expansion(1, video))
        assertEquals(first, keys.keyFor(partial.id, ReanalysisRequest.Expansion(1, video)))
        assertNotEquals(first, keys.keyFor(partial.id, ReanalysisRequest.Expansion(1, other)))
        assertNotEquals(first, keys.keyFor(partial.id, ReanalysisRequest.Expansion(2, video)))
        assertNotEquals(first, keys.keyFor(complete.id, ReanalysisRequest.Expansion(1, video)))
        keys.answered(partial.id, ReanalysisRequest.Expansion(1, video))
        assertNotEquals(first, keys.keyFor(partial.id, ReanalysisRequest.Expansion(1, video)))
    }
}
