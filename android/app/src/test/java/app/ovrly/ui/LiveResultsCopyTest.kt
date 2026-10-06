package app.ovrly.ui

import app.ovrly.contract.OverallAssessment
import app.ovrly.overlay.FixtureLiveResultsSource
import app.ovrly.overlay.LiveClaimState
import app.ovrly.overlay.LiveCoverage
import app.ovrly.overlay.LiveSessionPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveResultsCopyTest {
    private val updated = FixtureLiveResultsSource().apply { repeat(3) { advance() } }.results.value
    private val speech = updated.claims.first()
    private val text = updated.claims.last()

    private fun label(state: LiveClaimState) =
        liveStateLabel(speech.copy(state = state), updated.phase)

    @Test fun agreedLabelsAreExact() {
        assertEquals("Captured segment analyzed", CAPTURED_SEGMENT_LABEL)
        assertEquals("Assessment updated", UPDATE_NOTICE_LABEL)
        assertEquals("Continue research in queue", CONTINUE_RESEARCH_LABEL)
        assertEquals("Keep only available results", KEEP_AVAILABLE_LABEL)
    }

    @Test fun everyStateHasAVisibleLabelAndUnknownIsNeutral() {
        val labels = LiveClaimState.entries.map(::label)
        assertEquals(labels.size, labels.toSet().size)
        assertEquals("Unknown state", label(LiveClaimState.UNKNOWN))
        assertEquals("Unrecognized assessment", overallLabel(OverallAssessment.UNKNOWN))
    }

    @Test fun earlyStopKeepsUnfinishedClaimsVisiblyIncomplete() {
        val waiting = text.copy(state = LiveClaimState.WAITING)
        val checking = text.copy(state = LiveClaimState.CHECKING_EVIDENCE)
        for (phase in listOf(LiveSessionPhase.KEEPING_AVAILABLE, LiveSessionPhase.ABANDONED)) {
            assertTrue(liveStateLabel(waiting, phase).startsWith("Incomplete"))
            assertTrue(liveStateLabel(checking, phase).startsWith("Incomplete"))
            assertTrue(liveStateLabel(text, phase).contains("incomplete"))
        }
        assertEquals("Waiting", liveStateLabel(waiting, LiveSessionPhase.CONTINUING))
        assertTrue(label(LiveClaimState.FAILED).startsWith("Incomplete"))
        assertTrue(label(LiveClaimState.CANCELLED).startsWith("Incomplete"))
    }

    @Test fun updatedLabelSaysWhetherItIsStillProvisional() {
        assertEquals("Updated", liveStateLabel(speech, updated.phase))
        val stillProvisional = speech.copy(assessment = speech.assessment?.copy(provisional = true))
        assertEquals("Updated, still provisional", liveStateLabel(stillProvisional, updated.phase))
    }

    @Test fun capturedSegmentLabelStatesCoverageAndGaps() {
        assertEquals(
            "0:20 captured, not the full video; 0:10 not received",
            capturedCoverageLabel(LiveCoverage(capturedMs = 20_000, missingMs = 10_000))
        )
        assertEquals(
            "1:05 captured, not the full video",
            capturedCoverageLabel(LiveCoverage(capturedMs = 65_400, missingMs = 0))
        )
    }

    @Test fun talkBackDescriptionCarriesTimeTextAssessmentAndState() {
        val description = claimDescription(speech, updated.phase)
        assertTrue(description.startsWith("At 0:04. Nearly two thirds"))
        assertTrue(description.contains("Challenged, final"))
        assertTrue(description.endsWith("Updated."))
        assertFalse(description.contains(".."))
    }

    @Test fun copyNeverUsesAnEmDash() {
        val copy = LiveSessionPhase.entries.map(::livePhaseLabel) +
            LiveClaimState.entries.map(::label) +
            OverallAssessment.entries.map(::overallLabel) +
            claimDescription(speech, updated.phase) +
            capturedCoverageLabel(LiveCoverage(1, 1))
        assertTrue(copy.none { it.contains('\u2014') })
    }
}
