package app.ovrly.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The Inbox and Library of real checks (#34), built from the shared result fixtures. */
@RunWith(AndroidJUnit4::class)
class InboxScreenTest {
    @get:Rule val compose = createComposeRule()

    private val commands = mutableListOf<CheckCommand>()

    private fun show(state: ChecksUiState, dark: Boolean = false) {
        compose.setContent {
            OvrlyTheme(dark) {
                ChecksSections(
                    state,
                    { commands += it },
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                )
            }
        }
    }

    private fun text(value: String) = compose.onNodeWithText(value).performScrollTo()

    @Test fun emptyInboxAndLibrarySayWhatComesNext() {
        show(ChecksUiState(loaded = true))

        text("Inbox").assertIsDisplayed()
        text(
            "Nothing in progress. Share a video or link to ovrly from another app to " +
                "start a check."
        ).assertIsDisplayed()
        text("Library").assertIsDisplayed()
        text("Finished reports appear here.").assertIsDisplayed()
    }

    @Test fun aBuildWithoutTheServiceSaysSo() {
        show(ChecksUiState(available = false, loaded = true), dark = true)
        text("Checking is not set up in this build.").assertIsDisplayed()
    }

    @Test fun loadingOfflineWakingAndUpdateNotesAreShownAndANoticeDismisses() {
        show(
            ChecksUiState(
                loaded = false,
                offline = true,
                waking = true,
                updateNeeded = true,
                notice = "Cancelling. Results published so far are kept."
            )
        )

        text("Loading your checks...").assertIsDisplayed()
        text("Waking the ovrly service. This can take up to a minute.").assertIsDisplayed()
        text("Offline. Showing what this device last saw; status may be out of date.")
            .assertIsDisplayed()
        text("Update needed: this app cannot read the service's answer.").assertIsDisplayed()
        text("OK").performClick()

        compose.runOnIdle { assertEquals(listOf(CheckCommand.DismissNotice), commands) }
    }

    @Test fun rowsShowStageAndAgeAndSendTheirActions() {
        val partial = ChecksFixtures.item(ChecksFixtures.investigation("partial"))
        val failed = ChecksFixtures.item(
            ChecksFixtures.investigation(
                "failed",
                listOf("error", "retryable") to JsonPrimitive(true)
            )
        )
        val retried = ChecksFixtures.item(
            ChecksFixtures.investigation("cancelled"),
            retriedAs = "00000000-0000-4000-8000-000000000401"
        )
        val complete = ChecksFixtures.item(ChecksFixtures.investigation("complete"))
        show(
            ChecksUiState(
                loaded = true,
                inbox = listOf(partial, failed, retried),
                library = listOf(complete)
            )
        )

        text("Partial results ready / Finding sources").assertIsDisplayed()
        text("Could not be checked").assertIsDisplayed()
        text("Try again").performClick()
        text("Open the new check").performClick()
        text("Cancelled").assertIsDisplayed()
        text("Complete").assertIsDisplayed()

        compose.runOnIdle {
            assertEquals(
                listOf(
                    CheckCommand.Retry(failed.localId),
                    CheckCommand.Open("00000000-0000-4000-8000-000000000401")
                ),
                commands
            )
        }
    }

    @Test fun cancellingAsksFirstAndKeepCheckingSendsNothing() {
        val partial = ChecksFixtures.item(ChecksFixtures.investigation("partial"))
        show(ChecksUiState(loaded = true, inbox = listOf(partial)))
        val before = compose.idsWithText("Cancel check")

        text("Cancel check").performClick()
        compose.onNodeWithText("Cancel this check?").assertIsDisplayed()
        compose.onNodeWithText("Keep checking").performClick()
        compose.runOnIdle { assertTrue(commands.isEmpty()) }

        text("Cancel check").performClick()
        compose.newNodeWithText("Cancel check", before).performClick()

        compose.runOnIdle { assertEquals(listOf(CheckCommand.Cancel(partial.localId)), commands) }
    }

    @Test fun aLibraryReportOpensAndABusyRowCannotActTwice() {
        val complete = ChecksFixtures.item(ChecksFixtures.investigation("complete"))
        val cancelled = ChecksFixtures.item(
            ChecksFixtures.investigation(
                "complete",
                listOf("processing_status") to JsonPrimitive("cancelled"),
                listOf("state") to JsonPrimitive("cancelled"),
                listOf("job", "state") to JsonPrimitive("cancelled")
            )
        ).copy(localId = "local-cancelled")
        show(
            ChecksUiState(
                loaded = true,
                library = listOf(complete, cancelled),
                busy = setOf(cancelled.localId)
            )
        )

        text("Continue checking").assertIsNotEnabled()
        compose.onAllNodesWithText("2 claims", substring = true)[0]
            .performScrollTo()
            .performClick()

        compose.runOnIdle {
            assertEquals(listOf(CheckCommand.Open(complete.serverId!!)), commands)
        }
    }
}
