package app.ovrly.ui

import app.ovrly.contract.ContractError
import app.ovrly.contract.ContractErrorAction
import app.ovrly.contract.CorrectionAttribution
import app.ovrly.contract.Coverage
import app.ovrly.contract.CoverageStatus
import app.ovrly.contract.Interval
import app.ovrly.contract.InvestigationSource
import app.ovrly.contract.Modality
import app.ovrly.contract.OverallAssessment
import app.ovrly.contract.Relation
import app.ovrly.contract.RetractionStatus
import app.ovrly.contract.RetrievalRelevance
import app.ovrly.contract.SourceInspectionLevel
import app.ovrly.contract.SourceType
import app.ovrly.contract.Stage
import app.ovrly.contract.Timebase
import app.ovrly.data.ApiFailure
import app.ovrly.data.CheckStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Every contract value on the Inbox and Report screens has words (#34). Known values read
 * as themselves; an UNKNOWN one says it is not recognised and never reads as a verdict.
 */
class ReportLabelsTest {
    private val verdicts = setOf(Tone.SUPPORT, Tone.CHALLENGE, Tone.QUALIFY, Tone.MIXED)

    private fun assertDistinctAndNeutralWhenUnknown(labels: Map<out Enum<*>, String>) {
        labels.forEach { (value, text) ->
            assertTrue("$value has words", text.isNotBlank())
            assertEquals("$value", value.name == "UNKNOWN", text.contains(NOT_RECOGNISED))
        }
        assertEquals("labels are distinct", labels.size, labels.values.toSet().size)
    }

    @Test
    fun stagesModalitiesSourceTypesAccessAndRelevanceAllHaveDistinctWords() {
        assertDistinctAndNeutralWhenUnknown(Stage.entries.associateWith { it.label })
        assertDistinctAndNeutralWhenUnknown(Modality.entries.associateWith { it.label })
        assertDistinctAndNeutralWhenUnknown(SourceType.entries.associateWith { it.label })
        assertDistinctAndNeutralWhenUnknown(
            SourceInspectionLevel.entries.associateWith { it.label }
        )
        assertDistinctAndNeutralWhenUnknown(RetrievalRelevance.entries.associateWith { it.label })
        assertDistinctAndNeutralWhenUnknown(
            CorrectionAttribution.entries.associateWith { it.label }
        )
        assertEquals("Corrected by you", CorrectionAttribution.USER.label)
        assertEquals("Preprint, not peer reviewed", SourceType.PREPRINT.label)
        assertEquals("Title and metadata only", SourceInspectionLevel.METADATA_ONLY.label)
    }

    @Test
    fun assessmentsAndRelationsUseAVerdictToneOnlyForKnownFindings() {
        OverallAssessment.entries.forEach {
            val label = it.label
            when (it) {
                OverallAssessment.UNKNOWN -> assertEquals(Tone.NEUTRAL, label.tone)

                OverallAssessment.INSUFFICIENT_EVIDENCE ->
                    assertEquals(Tone.INSUFFICIENT, label.tone)

                else -> assertTrue("$it", label.tone in verdicts)
            }
        }
        Relation.entries.forEach {
            val label = it.label
            assertEquals("$it", it == Relation.UNKNOWN, label.tone == Tone.NEUTRAL)
        }
        assertEquals(Label("Mixed", Tone.MIXED), Relation.MIXED.label)
        assertEquals(Label("Sources disagree", Tone.MIXED), OverallAssessment.MIXED.label)
    }

    @Test
    fun contradictingEvidenceSortsFirstAndUnknownLast() {
        val sorted = (Relation.entries + listOf<Relation?>(null)).sortedBy { it.order }
        assertEquals(Relation.CHALLENGE, sorted.first())
        assertEquals(listOf(Relation.UNKNOWN, null), sorted.takeLast(2))
    }

    @Test
    fun onlyRetractedWithdrawnAndCorrectedSourcesWarn() {
        assertNull(RetractionStatus.NONE.warning)
        val warnings = RetractionStatus.entries.associateWith { it.warning }
        listOf(RetractionStatus.RETRACTED, RetractionStatus.WITHDRAWN, RetractionStatus.CORRECTED)
            .forEach { assertEquals("$it", Tone.WARNING, warnings[it]?.tone) }
        assertEquals(
            "Withdrawn: do not rely on this source",
            warnings[RetractionStatus.WITHDRAWN]?.text
        )
        assertEquals(Tone.NEUTRAL, warnings[RetractionStatus.UNDETERMINED]?.tone)
        assertEquals(Tone.NEUTRAL, warnings[RetractionStatus.UNKNOWN]?.tone)
        assertTrue(warnings[RetractionStatus.UNKNOWN]!!.text.endsWith(NOT_RECOGNISED))
    }

    @Test
    fun coverageNamesHowMuchWasCheckedAndNeverAVerdict() {
        val labels = CoverageStatus.entries.associateWith { Coverage(it).label() }
        assertEquals(
            "Checking has not reached the media yet",
            labels[CoverageStatus.NOT_STARTED]?.text
        )
        assertEquals(Tone.WARNING, labels[CoverageStatus.PARTIAL]?.tone)
        assertEquals(Label("Coverage $NOT_RECOGNISED"), labels[CoverageStatus.UNKNOWN])
        labels.values.forEach { assertFalse(it.tone in verdicts) }
        assertNull(Coverage(CoverageStatus.PARTIAL).amount())
        assertEquals("1:05 checked", Coverage(CoverageStatus.PARTIAL, coveredMs = 65_000).amount())
        assertEquals(
            "1:05 of 10:00 checked",
            Coverage(CoverageStatus.PARTIAL, 65_000, 600_000).amount()
        )
    }

    @Test
    fun intervalsSayWhichTimelineTheyAreOn() {
        assertEquals("0:01 to 1:02 in the video", Interval(1_000, 62_500, Timebase.MEDIA).label())
        val captured = Interval(0, 1_000, Timebase.CAPTURE).label()
        assertTrue(captured.contains("not a time in the original"))
        assertTrue(Interval(0, 1_000, Timebase.UNKNOWN).label().endsWith(NOT_RECOGNISED))
        assertEquals("10:00", clock(600_000))
    }

    @Test
    fun sourceTitlesUseTheSourceOrTheStoredKind() {
        assertEquals(
            "Link from video.example",
            sourceTitle(InvestigationSource.Url("https://www.video.example/a"), "url", null)
        )
        assertEquals("Shared link", sourceTitle(null, "url", null))
        assertEquals("Link from a shared link", sourceTitle(null, "url", "not a url"))
        assertEquals("Shared video file", sourceTitle(null, "upload", null))
        assertEquals("Captured clip", sourceTitle(null, "capture", null))
        assertTrue(sourceTitle(null, "future", null).endsWith(NOT_RECOGNISED))
        assertTrue(
            sourceTitle(InvestigationSource.Unknown("future"), "future", null)
                .endsWith(NOT_RECOGNISED)
        )
        assertEquals(
            "Captured clip",
            sourceTitle(
                InvestigationSource.Capture("00000000-0000-4000-8000-000000000301"),
                "capture",
                null
            )
        )
    }

    @Test
    fun onlyAFailedCheckWarns() {
        CheckStatus.entries.forEach {
            assertEquals("$it", it == CheckStatus.FAILED, it.tone == Tone.WARNING)
        }
        assertNull(epochMillis("not a time"))
        assertEquals(0L, epochMillis("1970-01-01T00:00:00Z"))
    }

    @Test
    fun failuresReadAsPlainNoticesNeverAsResults() {
        assertTrue(failureText(ApiFailure.Network("r1", "offline")).startsWith("You are offline"))
        assertTrue(
            failureText(ApiFailure.Incompatible("r2", 200, "bad")).startsWith("Update needed")
        )
        val server = ApiFailure.Server(
            409,
            ContractError(
                "REPORT_VERSION_STALE",
                "Reload first",
                false,
                ContractErrorAction.NONE,
                "r3"
            )
        )
        assertEquals("Reload first (REPORT_VERSION_STALE)", failureText(server))
    }

    @Test
    fun theScreenStateAndCommandsAreValues() {
        val state = ChecksUiState()
        assertTrue(state.available)
        assertFalse(state.loaded)
        assertEquals(state.copy(busy = setOf("a")), ChecksUiState(busy = setOf("a")))
        val commands = listOf(
            CheckCommand.Open("a"),
            CheckCommand.Cancel("a"),
            CheckCommand.Retry("a"),
            CheckCommand.Continue("a"),
            CheckCommand.ShowVersion(1),
            CheckCommand.Correct("c", "meaning"),
            CheckCommand.Expand("v"),
            CheckCommand.Close,
            CheckCommand.DismissNotice
        )
        assertEquals(commands.size, commands.toSet().size)
        assertEquals(CheckCommand.Correct("c", "meaning"), commands[5])
        val choice = VersionChoice(2, "Version 2 (latest)", fixture = false)
        assertEquals(choice, choice.copy())
        val shell = ChecksShell(state, null) {}
        assertNull(shell.report)
        assertNotEquals(ShellContent(), ShellContent(checks = shell))
    }
}
