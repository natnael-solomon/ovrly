package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import app.ovrly.overlay.FixtureLiveResultsSource
import app.ovrly.overlay.LivePanelState
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveResultsFixture

/** Every fixture poll, then the session closed after the second poll with "Keep only available". */
private fun previewTimeline(): Pair<List<LiveResults>, LiveResults> {
    val source = FixtureLiveResultsSource()
    val steps = buildList {
        add(source.results.value)
        while (source.advance()) add(source.results.value)
    }
    val stopped = FixtureLiveResultsSource().apply { repeat(2) { advance() } }
    stopped.close(continueResearch = false)
    return steps to stopped.results.value
}

/**
 * Every live overlay form over a dark "video" backdrop, as it sits in the overlay window: the
 * pill, the pill with the Stop choice, the expanded panel (list, update notice, detail, Stop
 * choice, after Stop), the idle bubble and the saved pill.
 */
@Composable
private fun LiveOverlayPreviewStack(dark: Boolean) {
    val (steps, stopped) = remember { previewTimeline() }
    val updated = steps.last()
    val frame = LivePanelFrame(width = 320.dp, maxHeight = 380.dp, animate = false)
    OvrlyTheme(dark) {
        CompositionLocalProvider(LocalWindowBlur provides WindowGlass(overlay = true)) {
            val p = LocalOvrlyPalette.current
            Column(
                Modifier
                    .background(ChromePalette.paper)
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    "LIVE OVERLAY / FIXTURE, NOT LIVE",
                    style = MaterialTheme.typography.labelSmall,
                    color = p.muted
                )
                ExaminingPill(PillState(seconds = 27, claims = 0), null, {}, frame = frame)
                ExaminingPill(PillState(seconds = 72, claims = 2, unseen = true), {
                }, {}, frame = frame)
                StopChoicePrompt(onChoice = {}, onCancel = {})
                PreviewPanel(steps[1], LivePanelState(expanded = true), frame)
                PreviewPanel(
                    updated,
                    LivePanelState(
                        expanded = true,
                        notices = listOf(LiveResultsFixture.SPEECH_CLAIM)
                    ),
                    frame
                )
                PreviewPanel(
                    updated,
                    LivePanelState(
                        expanded = true,
                        detailClaimId = LiveResultsFixture.SPEECH_CLAIM
                    ),
                    frame
                )
                PreviewPanel(updated, LivePanelState(expanded = true, stopPrompt = true), frame)
                PreviewPanel(stopped, LivePanelState(expanded = true), frame, examining = null)
                IdleBubble(PillState(0, claims = 2, unseen = true), {}, {}, frame = frame)
                SavedPill(claims = 2)
                Spacer(Modifier.size(8.dp))
            }
        }
    }
}

@Composable
private fun PreviewPanel(
    results: LiveResults,
    panel: LivePanelState,
    frame: LivePanelFrame,
    modifier: Modifier = Modifier,
    examining: ExaminingState? = ExaminingState(seconds = 74)
) {
    LiveExpandedPanel(
        LiveOverlayModel(results, panel, FixtureLiveResultsSource.LABEL, examining),
        LivePanelActions.None,
        modifier = modifier,
        frame = frame
    )
}

@Suppress("UnusedPrivateMember")
@Preview(name = "Mock 1 / live overlay", widthDp = 390, heightDp = 3000)
@Composable
private fun LightLiveOverlayPreview() = LiveOverlayPreviewStack(dark = false)

@Suppress("UnusedPrivateMember")
@Preview(name = "Chrome / live overlay", widthDp = 390, heightDp = 3000)
@Composable
private fun DarkLiveOverlayPreview() = LiveOverlayPreviewStack(dark = true)

@Suppress("UnusedPrivateMember")
@Preview(name = "Live overlay / 200% text", widthDp = 320, heightDp = 4200, fontScale = 2f)
@Composable
private fun LargeTextLiveOverlayPreview() = LiveOverlayPreviewStack(dark = false)
