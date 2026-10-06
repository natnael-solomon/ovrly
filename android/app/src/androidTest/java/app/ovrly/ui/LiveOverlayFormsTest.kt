package app.ovrly.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.overlay.FixtureLiveResultsSource
import app.ovrly.overlay.LivePanelController
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase
import app.ovrly.overlay.liveOverlayForm
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The live overlay's forms rendered on a device, driven by a real [LivePanelController]: the
 * pill expands and collapses, the expanded panel grows only up to its cap, Stop leads to the
 * bubble or the saved pill, and the bubble's dismiss action works without a drag. Dark theme
 * only, with plain glass ([WindowGlass.plain]): the CI emulators' software renderer crashes on
 * the gradient glass (#114).
 */
@RunWith(AndroidJUnit4::class)
class LiveOverlayFormsTest {
    @get:Rule val compose = createComposeRule()

    private val choices = mutableListOf<Boolean>()
    private val controller = LivePanelController { choices += it }
    private val results = mutableStateOf(steps()[2])
    private val examining = mutableStateOf(true)
    private var dismissed = 0

    private fun steps(): List<LiveResults> {
        val source = FixtureLiveResultsSource()
        return buildList {
            add(source.results.value)
            while (source.advance()) add(source.results.value)
        }
    }

    private fun show() {
        compose.setContent { Harness() }
    }

    @Composable
    private fun Harness() {
        val panel by controller.state.collectAsState()
        val live = results.value
        val form = liveOverlayForm(panel, live, examining.value) ?: return
        OvrlyTheme(dark = true) {
            CompositionLocalProvider(
                LocalWindowBlur provides WindowGlass(overlay = true, plain = true)
            ) {
                LiveOverlay(
                    form = form,
                    model = LiveOverlayModel(
                        live,
                        panel,
                        FixtureLiveResultsSource.LABEL,
                        ExaminingState(seconds = 27).takeIf { examining.value }
                    ),
                    actions = LivePanelActions(
                        onExpand = { controller.setExpanded(true) },
                        onCollapse = { controller.setExpanded(false) },
                        onDismiss = { dismissed += 1 },
                        onOpenClaim = controller::openClaim,
                        onCloseClaim = { controller.openClaim(null) },
                        onDismissNotice = controller::dismissNotice,
                        onRequestStop = { controller.setStopPrompt(true) },
                        onStopChoice = controller::chooseStop,
                        onCancelStop = { controller.setStopPrompt(false) }
                    ),
                    modifier = Modifier.testTag(TAG),
                    frame = LivePanelFrame(width = 320.dp, maxHeight = CAP, animate = false)
                )
            }
        }
    }
    private fun pill() = compose.onNodeWithContentDescription(
        pillDescription(27, results.value.claims.size, unseen = false)
    )

    @Test fun thePillExpandsOnTapAndCollapsesBack() {
        show()
        pill().assertIsDisplayed().performClick()
        compose.onNodeWithContentDescription("Collapse live results").assertIsDisplayed()
        compose.onNodeWithText("Examining 0:27").assertIsDisplayed()
        compose.onNodeWithText("Drag header to move").assertIsDisplayed()
        compose.onNodeWithContentDescription("Collapse live results").performClick()
        pill().assertIsDisplayed()
        assertTrue("collapsing is not Stop", choices.isEmpty())
    }

    @Test fun thePillDoesNotExpandBeforeResultsAreConnected() {
        results.value = LiveResults.NotConnected
        show()
        pill().performClick()
        compose.onNodeWithContentDescription("Collapse live results").assertDoesNotExist()
    }

    @Test fun theFirstClaimsExpandThePanelOnce() {
        results.value = LiveResults.NotConnected
        show()
        val steps = steps()
        compose.runOnIdle {
            controller.onResults(steps[0])
            controller.onResults(steps[1])
            results.value = steps[1]
        }
        compose.onNodeWithContentDescription("Collapse live results").assertIsDisplayed()
    }

    @Test fun theExpandedPanelGrowsWithItsContentUpToTheCap() {
        controller.setExpanded(true)
        show()
        val few = compose.onNodeWithTag(TAG).getUnclippedBoundsInRoot().height
        assertTrue("a short list stays short: $few", few < CAP)
        val claims = results.value.claims
        compose.runOnIdle {
            results.value = results.value.copy(
                claims = List(MANY) { i -> claims[i % claims.size].copy(id = "claim-$i") }
            )
        }
        val many = compose.onNodeWithTag(TAG).getUnclippedBoundsInRoot().height
        assertEquals(CAP.value, many.value, 1f)
    }

    @Test fun stopFromThePillOffersKeepExaminingThenContinuingLeavesTheBubble() {
        show()
        compose.onNodeWithContentDescription("Stop examining").performClick()
        compose.onNodeWithText(KEEP_EXAMINING_LABEL).assertIsDisplayed().performClick()
        assertTrue(choices.isEmpty())
        compose.onNodeWithContentDescription("Stop examining").performClick()
        compose.onNodeWithText(CONTINUE_RESEARCH_LABEL).performClick()
        compose.runOnIdle {
            assertEquals(listOf(true), choices)
            examining.value = false
            results.value = results.value.copy(phase = LiveSessionPhase.CONTINUING)
        }
        val bubble = compose.onNodeWithContentDescription("Research continues", substring = true)
        bubble.assertIsDisplayed()
        val dismiss = bubble.fetchSemanticsNode().config[SemanticsActions.CustomActions]
            .single { it.label == "Dismiss overlay" }
        compose.runOnIdle { dismiss.action() }
        compose.runOnIdle { assertEquals(1, dismissed) }
        bubble.performClick()
        compose.onNodeWithContentDescription("Dismiss overlay. Research continues")
            .assertIsDisplayed()
    }

    @Test fun keepingOnlyAvailableResultsShowsSavedToInbox() {
        controller.setExpanded(true)
        show()
        compose.onNodeWithContentDescription("Stop examining").performClick()
        compose.onNodeWithText(KEEP_AVAILABLE_LABEL).performClick()
        val claims = results.value.claims.size
        compose.onNodeWithText("Saved to Inbox · ${claimLabel(claims)}").assertIsDisplayed()
        compose.runOnIdle { assertEquals(listOf(false), choices) }
    }

    private companion object {
        const val TAG = "liveOverlay"
        const val MANY = 24
        val CAP = 400.dp
    }
}
