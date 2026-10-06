package app.ovrly.ui

import android.content.Intent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties.StateDescription
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTouchHeightIsAtLeast
import androidx.compose.ui.test.assertTouchWidthIsAtLeast
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasScrollToIndexAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The Report screen of a real check (#34): work, coverage, versions, claims and sheets. */
@RunWith(AndroidJUnit4::class)
class ReportScreenTest {
    @get:Rule val compose = createComposeRule()

    private val commands = mutableListOf<CheckCommand>()
    private val exports = mutableListOf<ReportExport>()

    private fun show(report: OpenReport, dark: Boolean = false, fontScale: Float = 1f) {
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(
                LocalDensity provides Density(density.density, fontScale)
            ) {
                OvrlyTheme(dark) {
                    CheckReportScreen(report, { commands += it }, sharer = { exports += it })
                }
            }
        }
    }

    /** Scrolls the report list to [text] (composing it if needed) and returns the node. */
    private fun reveal(text: String, substring: Boolean = false) = compose.run {
        onNode(hasScrollToIndexAction())
            .performScrollToNode(hasText(text, substring = substring))
        onNodeWithText(text, substring = substring)
    }

    private val complete get() = ChecksFixtures.investigation("complete")

    @Test fun aCompleteReportShowsWorkCoverageVersionsAndTheFirstClaimInDetail() {
        show(
            OpenReport(
                view = reportView(complete),
                versions = listOf(
                    VersionChoice(1, "Version 1", fixture = false),
                    VersionChoice(2, "Version 2 (latest)", fixture = false)
                ),
                comparedWith = 1,
                changes = mapOf(
                    "clm_synthetic_0001" to listOf("Meaning in version 1: \"Country buses.\"")
                ),
                notice = "Correction saved as a new version. The earlier version is kept."
            )
        )

        compose.onNodeWithText("Complete").assertIsDisplayed()
        compose.onNodeWithText("All of the media was checked").assertIsDisplayed()
        compose.onNodeWithText("3:05 of 3:05 checked").assertIsDisplayed()
        compose.onNodeWithText("OK").performClick()
        reveal("Version 2 of 2").assertIsDisplayed()
        compose.onNodeWithText("Version 1").performClick()
        reveal("Original wording").assertIsDisplayed()
        reveal("Corrected by you").assertIsDisplayed()
        reveal("Meaning before the correction").assertIsDisplayed()
        reveal("Changes since version 1").assertIsDisplayed()
        reveal("Synthetic source 1").assertIsDisplayed()
        assertEquals(2, compose.onAllNodesWithText("Supports the claim").fetchSemanticsNodes().size)

        reveal("The bus fleet had no diesel vehicles by 2024.").performClick()
        reveal("Qualifies the claim").assertIsDisplayed()
        reveal("Synthetic source 3").assertIsDisplayed()
        compose.onNode(hasScrollToIndexAction()).performScrollToIndex(0)
        compose.onNodeWithContentDescription("Back to Your space").performClick()

        compose.runOnIdle {
            assertEquals(
                listOf(
                    CheckCommand.DismissNotice,
                    CheckCommand.ShowVersion(1),
                    CheckCommand.Close
                ),
                commands
            )
        }
    }

    @Test fun aCorrectionKeepsTheOriginalWordingAndSendsTheNewMeaning() {
        show(OpenReport(reportView(complete)))
        reveal("Correct the meaning").performClick()

        compose.awaitDisplayed("Original wording (kept as it was)")
        compose.onNodeWithText("Save correction").assertIsNotEnabled()
        compose.onNodeWithText("This is the same meaning as now.").assertIsDisplayed()
        compose.onNode(hasSetTextAction()).performTextReplacement("   ")
        compose.onNodeWithText("Write what the claim means.").assertIsDisplayed()
        compose.onNode(hasSetTextAction()).performTextReplacement("City buses only.")
        compose.onNodeWithText("Save correction").assertIsEnabled().performClick()

        compose.onNodeWithText("Original wording (kept as it was)").assertDoesNotExist()
        compose.runOnIdle {
            assertEquals(
                listOf(CheckCommand.Correct("clm_synthetic_0001", "City buses only.")),
                commands
            )
        }
    }

    @Test fun cancellingACorrectionSendsNothing() {
        show(OpenReport(reportView(complete)), dark = true)
        reveal("Correct the meaning").performClick()
        compose.onNodeWithText("Cancel").performClick()

        compose.onNodeWithText("Save correction").assertDoesNotExist()
        compose.runOnIdle { assertTrue(commands.isEmpty()) }
    }

    @Test fun aCapturedClipExpandsOnlyAfterTheUserPicksAndConfirmsTheFullVideo() {
        val partial = ChecksFixtures.investigation("partial")
        val candidate = ChecksFixtures.item(complete)
        show(OpenReport(reportView(partial), candidates = listOf(candidate)))

        compose.onNodeWithText("Still checking. More claims and sources may follow.")
            .assertIsDisplayed()
        compose.onNodeWithText("Provisional: results may change.").assertIsDisplayed()
        reveal("Sources challenge this claim (provisional)").assertIsDisplayed()
        reveal("Not assessed yet").assertIsDisplayed()
        val captureTimes = compose.onAllNodesWithText("after capture started", substring = true)
        assertTrue(captureTimes.fetchSemanticsNodes().isNotEmpty())
        reveal("Shared the full video?").assertIsDisplayed()
        val before = compose.idsWithText("Check the full video")
        // The card's title can be on screen while its button is still below the fold.
        compose.onNodeWithText("Check the full video").performScrollTo().performClick()

        compose.awaitDisplayed("Is this the same video?")
        val confirm = compose.newNodeWithText("Check the full video", before)
        confirm.assertIsNotEnabled()
        compose.onNodeWithText(candidate.title).performClick()
        confirm.assertIsNotEnabled()
        compose.onNodeWithText("I confirm this is the full video of the clip I captured.")
            .performClick()
        confirm.assertIsEnabled().performClick()

        compose.runOnIdle {
            assertEquals(listOf(CheckCommand.Expand(candidate.serverId!!)), commands)
        }
    }

    @Test fun anEarlierStaleFixtureVersionSaysAllOfThat() {
        val view = reportView(complete, flags = ShownFlags(stale = true, fixture = true))
            .copy(latestVersion = 3)
        show(OpenReport(view, versionsNote = "Earlier versions need a connection."))

        compose.onNodeWithText(
            "May be out of date: this version could not be confirmed with ovrly recently."
        ).assertIsDisplayed()
        compose.onNodeWithText("Development fixture: not a check of this media.")
            .assertIsDisplayed()
        reveal("Version 2 of 3").assertIsDisplayed()
        reveal("You are viewing an earlier version. It is kept unchanged.").assertIsDisplayed()
        reveal("Earlier versions need a connection.").assertIsDisplayed()
    }

    @Test fun aFailedCheckShowsItsErrorAndNoFindings() {
        show(OpenReport(reportView(ChecksFixtures.investigation("failed"))))

        compose.onNodeWithText("Could not be checked").assertIsDisplayed()
        compose.onNodeWithText("Processing failed (MEDIA_UNSUPPORTED).").assertIsDisplayed()
        compose.onNodeWithText("No results: the check could not be completed.").assertIsDisplayed()
        compose.onNodeWithText("COVERAGE").assertDoesNotExist()
    }

    @Test fun noClaimsIsNeverAVerdictOnTheVideo() {
        show(OpenReport(reportView(ChecksFixtures.investigation("no-claims"))))

        reveal("not a finding that the video is accurate", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Correct the meaning").assertDoesNotExist()
    }

    @Test fun retractedAndShallowSourcesAreFlaggedOnTheirCards() {
        show(OpenReport(reportView(ChecksFixtures.investigation("insufficient-evidence"))), true)

        reveal("Not enough sound evidence").assertIsDisplayed()
        reveal("Retracted: do not rely on this source").assertIsDisplayed()
        reveal("Abstract only", substring = true).assertIsDisplayed()
        reveal("Title and metadata only", substring = true).assertIsDisplayed()
        assertEquals(
            2,
            compose.onAllNodesWithText("Not enough to decide").fetchSemanticsNodes().size
        )
    }

    @Test fun shareHandsTheShownVersionToTheShareSheet() {
        show(OpenReport(reportView(complete), retrievedAt = ChecksFixtures.NOW), dark = true)

        compose.onNodeWithContentDescription("Share report")
            .assertTouchHeightIsAtLeast(MIN_TARGET).performClick()

        compose.runOnIdle {
            val export = exports.single()
            assertEquals("ovrly report: Link from video.example (version 2)", export.subject)
            assertTrue(export.text.contains("Retrieved by this device:"))
            assertTrue(export.text.contains("https://sources.example/synthetic/0001"))
            assertTrue(commands.isEmpty())
        }
    }

    @Test fun aCheckWithoutAVersionOffersNothingToShare() {
        show(OpenReport(reportView(ChecksFixtures.investigation("failed"))), dark = true)

        compose.onNodeWithContentDescription("Share report").assertDoesNotExist()
    }

    @Test fun theShareSheetGetsPlainTextOnly() {
        val export = reportExport(OpenReport(reportView(complete)))!!
        val chooser = export.chooser()
        @Suppress("DEPRECATION")
        val send = chooser.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)!!

        assertEquals(Intent.ACTION_CHOOSER, chooser.action)
        assertEquals(Intent.ACTION_SEND, send.action)
        assertEquals("text/plain", send.type)
        assertEquals(export.subject, send.getStringExtra(Intent.EXTRA_SUBJECT))
        assertEquals(export.text, send.getStringExtra(Intent.EXTRA_TEXT))
        assertNull("no attachment", send.clipData)
        assertTrue(send.extras!!.keySet().none { it == Intent.EXTRA_STREAM })
    }

    @Test fun talkBackReadsEachClaimFirstWithItsTimeInWords() {
        val partial = ChecksFixtures.investigation("partial")
        show(OpenReport(reportView(partial)), dark = true)
        val claim = reportView(partial).claims.first()
        val spoken = claimDescription(claim)
        val card = hasContentDescription(spoken)

        compose.onNode(hasScrollToIndexAction()).performScrollToNode(card)
        compose.onNode(card)
            .assert(hasText(claim.proposition))
            .assert(SemanticsMatcher.expectValue(StateDescription, "Detail shown"))
            .performClick()
        compose.onNode(card)
            .assert(SemanticsMatcher.expectValue(StateDescription, "Detail hidden"))
        assertTrue(spoken.startsWith("Claim: ${claim.proposition}"))
        assertTrue(spoken.contains("after capture started, not a time in the original video"))
    }

    @Test fun claimDetailReadsTheTimeAndEachSourceAsOneStop() {
        show(OpenReport(reportView(complete)), dark = true)
        val claim = reportView(complete).claims.first()

        reveal("When").assert(hasContentDescription("When: ${claim.spokenInterval}"))
        reveal("Synthetic source 1")
            .assert(hasText("Supports the claim"))
            .assert(hasText("Synthetic Publisher"))
        compose.onAllNodesWithContentDescription("Open source: Synthetic source 1")
            .onFirst().assertTouchHeightIsAtLeast(MIN_TARGET)
    }

    @Test fun everyReportControlHasA48DpTarget() {
        val partial = ChecksFixtures.investigation("partial")
        val candidate = ChecksFixtures.item(complete)
        show(
            OpenReport(
                reportView(partial),
                versions = listOf(
                    VersionChoice(1, "Version 1", fixture = false),
                    VersionChoice(2, "Version 2 (latest)", fixture = false)
                ),
                candidates = listOf(candidate),
                notice = "Correction saved as a new version. The earlier version is kept."
            ),
            dark = true
        )
        assertClickTargets()
        reveal("Shared the full video?")
        assertClickTargets()
        compose.onNodeWithText("Check the full video").performScrollTo().performClick()
        compose.awaitDisplayed("Is this the same video?")
        listOf(
            candidate.title,
            "I confirm this is the full video of the clip I captured.",
            "Cancel"
        ).forEach {
            compose.onNode(hasText(it) and hasClickAction())
                .assertTouchHeightIsAtLeast(MIN_TARGET)
        }
    }

    @Test fun at200PercentTextNoReportOrEvidenceTextIsClipped() {
        show(
            OpenReport(reportView(ChecksFixtures.investigation("insufficient-evidence"))),
            dark = true,
            fontScale = 2f
        )
        assertNoClippedText()
        reveal("Retracted: do not rely on this source").assertIsDisplayed()
        assertNoClippedText()
        reveal("Title and metadata only", substring = true).assertIsDisplayed()
        assertNoClippedText()
    }

    /** Every clickable node on screen is at least 48 by 48 dp to touch. */
    private fun assertClickTargets() {
        val ids = compose.onAllNodes(hasClickAction()).fetchSemanticsNodes().map { it.id }
        assertTrue(ids.isNotEmpty())
        ids.forEach { id ->
            compose.onNode(SemanticsMatcher("node $id") { it.id == id })
                .assertTouchHeightIsAtLeast(MIN_TARGET)
                .assertTouchWidthIsAtLeast(MIN_TARGET)
        }
    }

    /** No composed text is cut off by a fixed height or width. */
    private fun assertNoClippedText() {
        val nodes = compose.onAllNodes(
            SemanticsMatcher.keyIsDefined(SemanticsActions.GetTextLayoutResult),
            useUnmergedTree = true
        ).fetchSemanticsNodes()
        assertTrue(nodes.isNotEmpty())
        compose.runOnIdle {
            nodes.forEach { node ->
                val layouts = mutableListOf<TextLayoutResult>()
                node.config[SemanticsActions.GetTextLayoutResult].action?.invoke(layouts)
                val layout = layouts.single()
                assertFalse(
                    "\"${layout.layoutInput.text}\" is clipped",
                    layout.hasVisualOverflow
                )
            }
        }
    }

    private companion object {
        val MIN_TARGET = 48.dp
    }
}
