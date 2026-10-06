package app.ovrly.ui

import android.os.Build
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isSelectable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.espresso.Espresso
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Your space (Inbox, Library, labeled samples), Explore and the tabs (RFC-D22, #34). */
@RunWith(AndroidJUnit4::class)
class AppShellTest {
    @get:Rule val compose = createComposeRule()

    private val commands = mutableListOf<CheckCommand>()
    private val destinations = mutableListOf<AppDestination>()

    /**
     * Your space and Explore draw the chrome wordmark, its glare and the sample artwork. On
     * API 31+ the CI emulators' software GPU (`-gpu swiftshader_indirect`) loses colour
     * buffers on these screens ("bad color buffer handle") and the emulator goes offline,
     * aborting the whole run, while API 29 renders them. These tests run on API 29 until the
     * emulator GPU can render them; the Inbox and Report screens are tested on both.
     */
    @Before fun onlyWhereTheEmulatorCanDrawTheChrome() {
        assumeTrue(Build.VERSION.SDK_INT < Build.VERSION_CODES.S)
    }

    private fun show(
        checks: ChecksUiState? = null,
        report: OpenReport? = null,
        activeSession: String? = null
    ) {
        compose.setContent {
            var destination by remember { mutableStateOf(AppDestination.SPACE) }
            // Liquid Chrome, the app's default appearance.
            OvrlyTheme(dark = true) {
                AppShell(
                    destination = destination,
                    onDestination = {
                        destinations += it
                        destination = it
                    },
                    activeSession = activeSession,
                    shell = ShellContent(
                        checks = checks?.let { state ->
                            ChecksShell(state, report) { commands += it }
                        }
                    )
                ) { Text("Settings slot") }
            }
        }
    }

    private fun tab(label: String) = compose.onNode(hasText(label) and isSelectable())

    private fun text(value: String) = compose.onNodeWithText(value).performScrollTo()

    @Test fun yourSpaceListsRealChecksAboveLabeledSamples() {
        val partial = ChecksFixtures.item(ChecksFixtures.investigation("partial"))
        val complete = ChecksFixtures.item(ChecksFixtures.investigation("complete"))
        show(ChecksUiState(loaded = true, inbox = listOf(partial), library = listOf(complete)))

        text("Inbox").assertIsDisplayed()
        text("Partial results ready / Finding sources").assertIsDisplayed()
        text("Library").assertIsDisplayed()
        text("Saved samples").assertIsDisplayed()
        text("Illustrative reports, not checks of real videos.").assertIsDisplayed()
        text("A moment outside").assertIsDisplayed()
        val libraryRow = hasText("2 claims", substring = true) and
            !hasText("assessed", substring = true)
        compose.onNode(libraryRow).performScrollTo().performClick()

        compose.runOnIdle {
            assertEquals(listOf(CheckCommand.Open(complete.serverId!!)), commands)
        }
    }

    @Test fun anOpenReportShowsInYourSpaceAndClosesWhenTheTabChanges() {
        show(
            ChecksUiState(loaded = true),
            report = OpenReport(reportView(ChecksFixtures.investigation("complete")))
        )

        compose.onNodeWithText("REPORT").assertIsDisplayed()
        tab("Explore").performClick()

        compose.onNodeWithText("REPORT").assertDoesNotExist()
        compose.onNodeWithText("Technology").assertIsDisplayed()
        compose.runOnIdle {
            assertTrue(commands.isNotEmpty())
            assertTrue(commands.all { it == CheckCommand.Close })
            assertEquals(listOf(AppDestination.EXPLORE), destinations)
        }
    }

    @Test fun exploreFiltersByTopicAndSearch() {
        show()
        tab("Explore").performClick()

        compose.onNodeWithText("Technology").performClick()
        text("The attention economy").assertIsDisplayed()
        compose.onNodeWithText("Urban shade").assertDoesNotExist()
        compose.onNodeWithContentDescription("Search samples").performClick()
        compose.onNode(hasSetTextAction()).performTextInput("nothing like this")
        text("Reset filters").performClick()
        text("Urban shade").assertIsDisplayed()
        compose.onNodeWithContentDescription("Close search").performClick()
        text("A moment outside").performClick()

        compose.onNodeWithText("SAMPLE REPORT").assertIsDisplayed()
    }

    @Test fun aSampleReportSavesUnsavesAndGoesBack() {
        show()
        text("A moment outside").performClick()

        compose.onNodeWithText("SAMPLE REPORT").assertIsDisplayed()
        compose.onNodeWithText("Illustrative content. No video analyzed or sources retrieved.")
            .assertIsDisplayed()
        compose.onNodeWithContentDescription("Remove sample from Your space").performClick()
        compose.onNodeWithContentDescription("Save sample to Your space").assertIsDisplayed()
        compose.onNodeWithText("Less").performClick()
        assertTrue(
            compose.onAllNodesWithText("Context").fetchSemanticsNodes().isNotEmpty()
        )
        compose.onNodeWithContentDescription("Back to samples").performClick()

        compose.onNodeWithText("SAMPLE REPORT").assertDoesNotExist()
        compose.onNodeWithText("A moment outside").assertDoesNotExist()
        text("Urban shade").assertIsDisplayed()
    }

    @Test fun yourSpaceSearchShowsNoMatchesAndClears() {
        show()
        compose.onNodeWithContentDescription("Search samples").performClick()
        compose.onNode(hasSetTextAction()).performTextInput("nothing like this")

        text("No matches").assertIsDisplayed()
        text("Clear search").performClick()
        text("A moment outside").assertIsDisplayed()
    }

    @Test fun theCaptureBarAndTabsReachSettingsAndBackReturnsToYourSpace() {
        show(activeSession = "Capture active")

        compose.onNodeWithText("Capture active / Open controls").performClick()
        compose.onNodeWithText("Settings slot").assertIsDisplayed()
        Espresso.pressBack()
        compose.onNodeWithText("Settings slot").assertDoesNotExist()
        tab("Settings").performClick()
        compose.onNodeWithText("Settings slot").assertIsDisplayed()

        compose.runOnIdle {
            assertEquals(
                listOf(AppDestination.SETTINGS, AppDestination.SPACE, AppDestination.SETTINGS),
                destinations
            )
        }
    }
}
