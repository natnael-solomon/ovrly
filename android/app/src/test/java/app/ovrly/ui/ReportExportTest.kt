package app.ovrly.ui

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset

/** The on-device report export (AN-11, #39; decision 0003) from the shared result fixtures. */
class ReportExportTest {
    private val retrieved = epochMillis("2026-10-06T09:30:00Z")!!

    private fun fixture(name: String): Investigation = InvestigationCodec.parseInvestigation(
        ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == name }
            .investigationPayload()
    )

    private fun export(
        view: ReportView,
        retrievedAt: Long? = retrieved
    ): ReportExport = reportExport(OpenReport(view, retrievedAt = retrievedAt), ZoneOffset.UTC)!!

    @Test
    fun aCompleteReportCarriesClaimsSourcesVersionAndDates() {
        val investigation = fixture("complete")
        val export = export(reportView(investigation))
        val text = export.text

        assertEquals("ovrly report: Link from video.example (version 2)", export.subject)
        assertTrue(text.startsWith(export.subject))
        assertTrue(text.contains("Version 2 of 2, published 4 October 2026, 12:11 UTC+00:00"))
        assertTrue(text.contains("Retrieved by this device: 6 October 2026, 09:30 UTC+00:00"))
        assertTrue(text.contains("Check: Complete"))
        assertTrue(text.contains("Coverage: All of the media was checked, 3:05 of 3:05 checked"))
        assertTrue(text.contains("Claims (2)"))
        val report = investigation.report!!
        report.claims.forEach { assertTrue(text.contains(it.proposition)) }
        assertTrue(text.contains("Assessment: Sources support this claim"))
        assertTrue(text.contains("Corrected by you"))
        assertTrue(text.contains("When: 0:12 to 0:18 in the video"))
        assertTrue(text.contains("Supports the claim: Synthetic source 1 (Synthetic Publisher)"))
        report.evidence.forEach { assertTrue(text.contains(it.source.url!!)) }
        assertTrue(text.contains("Limitations"))
        assertTrue(text.contains("no verdict on the whole video"))
        assertFalse("not provisional", text.contains("PROVISIONAL"))
        assertFalse(text.contains("earlier version"))
    }

    @Test
    fun noMediaTranscriptOriginalWordingOrCheckedLinkLeavesTheDevice() {
        val investigation = fixture("complete")
        val text = export(reportView(investigation)).text
        val report = investigation.report!!

        report.claims.forEach { assertFalse(text.contains(it.originalText)) }
        report.evidence.forEach { evidence ->
            evidence.excerpt?.let { assertFalse(text.contains(it)) }
        }
        assertFalse(text.contains("https://video.example/synthetic/clip-0001"))
        assertTrue(text.contains("holds no video, audio, screen images or transcript"))
    }

    @Test
    fun aProvisionalCaptureSaysSoAndKeepsTheCaptureTimeline() {
        val text = export(reportView(fixture("partial"))).text

        assertTrue(text.contains("PROVISIONAL: results may change."))
        assertTrue(text.contains("Sources challenge this claim (provisional)"))
        assertTrue(text.contains("after capture started (not a time in the original video)"))
        assertTrue(text.contains("Sources: none yet"))
        assertTrue(text.contains("Only part of the media was checked"))
        assertTrue(text.contains("capture timeline, not times in the original video"))
        assertTrue(text.contains("Contradicts the claim: Synthetic source 4"))
    }

    @Test
    fun anEarlierStaleFixtureVersionIsLabelledAsSuch() {
        val view = reportView(fixture("complete"), flags = ShownFlags(stale = true, fixture = true))
            .copy(latestVersion = 3)
        val text = export(view).text

        assertTrue(text.contains("Version 2 of 3 (an earlier version, kept unchanged)"))
        assertTrue(text.contains("DEVELOPMENT FIXTURE: not a check of this media."))
        assertTrue(text.contains("May be out of date"))
    }

    @Test
    fun noClaimsIsNeverAVerdictAndShallowSourcesAreDisclosed() {
        val none = export(reportView(fixture("no-claims"))).text
        assertTrue(none.contains("Claims (0)"))
        assertTrue(none.contains("This is not a finding that the video is accurate."))

        val shallow = export(reportView(fixture("insufficient-evidence"))).text
        assertTrue(shallow.contains("Retracted: do not rely on this source"))
        assertTrue(shallow.contains("Some sources were read only in part"))
    }

    @Test
    fun withoutAPublishedVersionThereIsNothingToExport() {
        assertNull(reportExport(OpenReport(reportView(fixture("failed")))))
        assertNull(reportExport(OpenReport(reportView(fixture("cancelled")))))
    }

    @Test
    fun anUnknownRetrievalTimeIsLeftOutRatherThanGuessed() {
        val text = export(reportView(fixture("complete")), retrievedAt = null).text
        assertFalse(text.contains("Retrieved by this device"))
    }
}
