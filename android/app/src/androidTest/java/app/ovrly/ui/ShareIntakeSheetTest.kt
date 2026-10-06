package app.ovrly.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.share.IntakeAction
import app.ovrly.share.IntakeState
import app.ovrly.share.ShareProblem
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The share sheet's rejection states, rendered in a real window on the device. */
@RunWith(AndroidJUnit4::class)
class ShareIntakeSheetTest {
    @get:Rule val compose = createComposeRule()

    private val actions = mutableListOf<IntakeAction>()

    private fun show(state: IntakeState) {
        compose.setContent {
            OvrlyTheme(dark = true) {
                ShareIntakeSheet(
                    state = state,
                    waking = false,
                    onAction = { actions += it },
                    onPickFile = {}
                )
            }
        }
    }

    @Test fun oversizeRejectionNamesTheLimitAndOffersAFile() {
        show(IntakeState.Rejected(ShareProblem.TOO_LARGE, maxBytes = 1_000_000))

        compose.onNodeWithText(ShareProblem.TOO_LARGE.title).assertIsDisplayed()
        compose.onNodeWithText("Limit: 1.0 MB.").assertIsDisplayed()
        compose.onNodeWithText("Choose a video file").assertIsDisplayed()
    }

    @Test fun malformedShareOffersNoFileAndCloses() {
        show(IntakeState.Rejected(ShareProblem.MALFORMED, maxBytes = 1_000_000))

        compose.onNodeWithText(ShareProblem.MALFORMED.message).assertIsDisplayed()
        compose.onNodeWithText("Choose a video file").assertDoesNotExist()
        compose.onNodeWithText("Close").performClick()

        compose.runOnIdle { assertEquals(listOf(IntakeAction.DISMISS), actions) }
    }
}
