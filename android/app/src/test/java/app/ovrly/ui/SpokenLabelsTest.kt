package app.ovrly.ui

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Interval
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.Timebase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What TalkBack reads for times, claims and the recording timer (AN-11, #39). */
class SpokenLabelsTest {
    @Test
    fun clockOffsetsAreReadInWordsNotAsATimeOfDay() {
        assertEquals("0 seconds", spokenClock(0))
        assertEquals("1 second", spokenClock(1_000))
        assertEquals("12 seconds", spokenClock(12_999))
        assertEquals("1 minute", spokenClock(60_000))
        assertEquals("1 minute 5 seconds", spokenClock(65_000))
        assertEquals("3 minutes", spokenClock(180_000))
        assertEquals("10 minutes 1 second", spokenClock(601_000))
    }

    @Test
    fun intervalsKeepTheirTimelineWhenSpoken() {
        assertEquals(
            "from 12 seconds to 18 seconds in the video",
            Interval(12_000, 18_000, Timebase.MEDIA).spokenLabel()
        )
        assertEquals(
            "from 1 minute to 1 minute 4 seconds after capture started, " +
                "not a time in the original video",
            Interval(60_000, 64_000, Timebase.CAPTURE).spokenLabel()
        )
        assertTrue(
            Interval(0, 1_000, Timebase.UNKNOWN).spokenLabel().endsWith(NOT_RECOGNISED)
        )
    }

    @Test
    fun aClaimIsReadClaimFirstThenAssessmentThenWhereAndWhen() {
        val investigation = InvestigationCodec.parseInvestigation(
            ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == "complete" }
                .investigationPayload()
        )
        val claim = reportView(investigation).claims.first()
        val spoken = claimDescription(claim)

        assertEquals(
            "Claim: ${claim.proposition}. Sources support this claim. Corrected by you. " +
                "Spoken, from 12 seconds to 18 seconds in the video. 2 sources",
            spoken
        )
        assertFalse("no clock digits", spoken.contains("0:12"))
        assertFalse("never the transcript wording", spoken.contains(claim.originalText))
    }

    @Test
    fun theRecordingTimerIsReadAgainstTheThreeMinuteLimit() {
        assertEquals("Recording, 12 seconds of 3 minutes", recordingDescription(12))
        assertEquals("Recording, 2 minutes 5 seconds of 3 minutes", recordingDescription(125))
    }
}
