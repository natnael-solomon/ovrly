package app.ovrly.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasScrollToIndexAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.contract.InvestigationCodec
import app.ovrly.data.SIGN_IN_UNAVAILABLE
import app.ovrly.data.SavedReportEntry
import app.ovrly.data.StoredSave
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Saved reports and the optional account (AN-10, #36) on the device, in the dark theme: the
 * explicit Save and Remove controls of a report, the saved copy, the Library's Saved reports
 * and the account section with this build's sign-in state and the 0003 disclosure.
 */
@RunWith(AndroidJUnit4::class)
class SavedReportsScreenTest {
    @get:Rule val compose = createComposeRule()

    private val commands = mutableListOf<CheckCommand>()
    private val complete get() = ChecksFixtures.investigation("complete")
    private val report get() = checkNotNull(complete.report)

    private fun showReport(report: OpenReport) {
        compose.setContent { OvrlyTheme(true) { CheckReportScreen(report, { commands += it }) } }
    }

    private fun showSections(state: ChecksUiState) {
        compose.setContent {
            OvrlyTheme(true) {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ChecksSections(state, { commands += it })
                }
            }
        }
    }

    private fun reveal(text: String) = compose.run {
        onNode(hasScrollToIndexAction()).performScrollToNode(hasText(text))
        onNodeWithText(text)
    }

    private fun stored(): StoredSave = StoredSave(
        SavedReportEntry(
            reportId = report.id,
            investigationId = report.investigationId,
            version = report.version,
            savedAt = "2026-10-07T00:00:00Z",
            json = InvestigationCodec.encodeReportVersion(report),
            storedAt = ChecksFixtures.NOW
        ),
        report
    )

    @Test
    fun anUnsavedReportIsSavedOnlyByTheExplicitButton() {
        val view = reportView(complete)
        showReport(OpenReport(view, save = saveState(view, emptyList())))
        reveal("Save this report").assertIsDisplayed()
        compose.onNodeWithText("NOT SAVED").assertIsDisplayed()
        assertEquals(emptyList<CheckCommand>(), commands)
        reveal("Save report").assertIsEnabled().performClick()
        assertEquals(listOf<CheckCommand>(CheckCommand.Save(report.id)), commands)
    }

    @Test
    fun aSavedReportOffersRemovalAndWaitsWhileBusy() {
        val view = reportView(complete)
        showReport(OpenReport(view, busy = true, save = saveState(view, listOf(stored()))))
        reveal("You saved version 2 of this report.").assertIsDisplayed()
        reveal("Remove from saved").assertIsNotEnabled()
    }

    @Test
    fun aSavedCopyIsReadOnlyAndCanBeRemoved() {
        val view = checkNotNull(savedCopyView(stored()))
        showReport(OpenReport(view, save = saveState(view, listOf(stored()), copy = true)))
        compose.onNodeWithText(SAVED_COPY).assertIsDisplayed()
        reveal("This is the copy of version 2 you saved.").assertIsDisplayed()
        reveal("Remove from saved").performClick()
        assertEquals(listOf<CheckCommand>(CheckCommand.Unsave(report.id)), commands)
    }

    @Test
    fun theLibraryListsSavesAndTheAccountSectionDisclosesItsLimits() {
        val items = savedItems(listOf(stored()), emptyList(), ChecksFixtures.NOW)
        showSections(ChecksUiState(loaded = true, saved = items))

        compose.onNodeWithText("Saved reports").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(SAVED_SCOPE).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Opens the copy you saved.").performScrollTo().performClick()
        assertEquals(listOf<CheckCommand>(CheckCommand.OpenSaved(report.id)), commands)

        compose.onNodeWithText("Sign in with Google").performScrollTo().assertIsNotEnabled()
        compose.onNodeWithText(SIGN_IN_UNAVAILABLE).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(RECOVERY_DISCLOSURE).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun anEmptyLibraryExplainsHowToSaveAndALinkedAccountSaysSo() {
        showSections(
            ChecksUiState(
                loaded = true,
                account = AccountUiState(available = true, linked = true, notice = "Linked.")
            )
        )
        compose.onNodeWithText(
            "Open a finished report and tap Save report to keep a copy here."
        ).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(
            "Linked to a Google account. Reports you save are kept for that account."
        ).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Linked.").performScrollTo().assertIsDisplayed()
        val signIn = compose.onAllNodes(hasText("Sign in with Google")).fetchSemanticsNodes()
        assertEquals(0, signIn.size)
    }

    @Test
    fun signInStartsTheLinkWhenTheBuildCanSignIn() {
        showSections(ChecksUiState(loaded = true, account = AccountUiState(available = true)))
        compose.onNodeWithText("Sign in with Google").performScrollTo().assertIsEnabled()
            .performClick()
        val link = commands.single() as CheckCommand.LinkAccount
        // The picker needs the screen it is shown over.
        assertNotNull(link.activity)
    }
}
