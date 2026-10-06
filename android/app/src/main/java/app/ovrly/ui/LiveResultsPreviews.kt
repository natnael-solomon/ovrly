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

@Composable
private fun LiveResultsPreviewStack(dark: Boolean) {
    val (steps, stopped) = remember { previewTimeline() }
    val updated = steps.last()
    OvrlyTheme(dark) {
        val p = LocalOvrlyPalette.current
        Column(
            Modifier
                .background(p.paper)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                "LIVE RESULTS / FIXTURE, NOT LIVE",
                style = MaterialTheme.typography.labelSmall,
                color = p.muted
            )
            steps.dropLast(1).forEach { results ->
                PreviewPanel(results, LivePanelState())
            }
            PreviewPanel(updated, LivePanelState(notices = listOf(LiveResultsFixture.SPEECH_CLAIM)))
            PreviewPanel(updated, LivePanelState(detailClaimId = LiveResultsFixture.SPEECH_CLAIM))
            StopChoicePrompt(onChoice = {}, onCancel = {})
            PreviewPanel(stopped, LivePanelState())
            LiveResultsShowButton(claims = 2, updates = 1, onShow = {})
            Spacer(Modifier.size(8.dp))
        }
    }
}

@Composable
private fun PreviewPanel(
    results: LiveResults,
    panel: LivePanelState,
    modifier: Modifier = Modifier
) {
    LiveResultsPanel(
        LiveOverlayModel(results, panel, FixtureLiveResultsSource.LABEL),
        LivePanelActions.None,
        modifier = modifier
    )
}

@Suppress("UnusedPrivateMember")
@Preview(name = "Mock 1 / live results", widthDp = 390, heightDp = 2400)
@Composable
private fun LightLiveResultsPreview() = LiveResultsPreviewStack(dark = false)

@Suppress("UnusedPrivateMember")
@Preview(name = "Chrome / live results", widthDp = 390, heightDp = 2400)
@Composable
private fun DarkLiveResultsPreview() = LiveResultsPreviewStack(dark = true)

@Suppress("UnusedPrivateMember")
@Preview(name = "Live results / 200% text", widthDp = 320, heightDp = 3200, fontScale = 2f)
@Composable
private fun LargeTextLiveResultsPreview() = LiveResultsPreviewStack(dark = false)
