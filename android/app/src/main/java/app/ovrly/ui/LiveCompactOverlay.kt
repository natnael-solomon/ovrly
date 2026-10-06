package app.ovrly.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.Button
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.ovrly.overlay.LivePanelState
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase

/** What the compact overlay shows: live results, panel interaction state and source label. */
@Immutable
internal data class LiveOverlayModel(
    val results: LiveResults,
    val panel: LivePanelState = LivePanelState(),
    /** Non-null for fixture sources, so their content is never mistaken for research. */
    val sourceLabel: String? = null
)

/** Window-level appearance for the live overlay: glass opacity and the panel height cap. */
@Immutable
internal data class LiveOverlayLayout(
    val higherOpacity: Boolean = false,
    val maxHeight: Dp = Dp.Unspecified
)

/**
 * Panel callbacks, wired by the host to `LivePanelController`. Only [onStopChoice] may stop
 * capture, and only after the user picked one of the two Stop answers.
 */
@Immutable
internal data class LivePanelActions(
    val onHide: () -> Unit = {},
    val onShow: () -> Unit = {},
    val onOpenClaim: (String) -> Unit = {},
    val onCloseClaim: () -> Unit = {},
    val onDismissNotice: (String) -> Unit = {},
    val onRequestStop: () -> Unit = {},
    val onStopChoice: (Boolean) -> Unit = {},
    val onCancelStop: () -> Unit = {},
    /** Window y of the scrollable list, so the host drags only above it. */
    val onListTop: (Int) -> Unit = {}
) {
    companion object {
        val None = LivePanelActions()
    }
}

/**
 * The compact overlay: the recording [pill] (when the host has one), then either the Stop
 * choice or the live results panel (or its "Show" button). The Stop choice replaces the
 * panel while open so the two never stack. Nothing beyond the pill renders while no live
 * source is connected, apart from the Stop choice. [LiveOverlayLayout.maxHeight] caps the
 * panel, whose claim list scrolls in the remaining space.
 */
@Composable
internal fun LiveCompactOverlay(
    model: LiveOverlayModel,
    actions: LivePanelActions,
    modifier: Modifier = Modifier,
    layout: LiveOverlayLayout = LiveOverlayLayout(),
    pill: (@Composable () -> Unit)? = null
) {
    val higherOpacity = layout.higherOpacity
    val results = model.results
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        pill?.invoke()
        if (model.panel.stopPrompt) {
            StopChoicePrompt(
                onChoice = actions.onStopChoice,
                onCancel = actions.onCancelStop,
                higherOpacity = higherOpacity
            )
        }
        if (!model.panel.stopPrompt && results.phase != LiveSessionPhase.NOT_CONNECTED) {
            if (model.panel.panelVisible) {
                LiveResultsPanel(
                    model,
                    actions,
                    layout = layout,
                    showStop = pill == null && results.phase == LiveSessionPhase.CAPTURING &&
                        model.panel.stopChoice == null
                )
            } else {
                LiveResultsShowButton(
                    claims = results.claims.size,
                    updates = model.panel.notices.size,
                    onShow = actions.onShow,
                    higherOpacity = higherOpacity
                )
            }
        }
    }
}

/** Shown while the panel is hidden; capture keeps running and Stop stays in the notification. */
@Composable
internal fun LiveResultsShowButton(
    claims: Int,
    updates: Int,
    onShow: () -> Unit,
    modifier: Modifier = Modifier,
    higherOpacity: Boolean = false
) {
    val label = buildString {
        append("Show live results")
        if (claims > 0) append(" (${claimLabel(claims)})")
        if (updates > 0) append(", $updates updated")
    }
    Box(
        modifier
            .mockGlass(higherOpacity)
            .sizeIn(minHeight = 48.dp)
            .clickable(role = Role.Button, onClickLabel = label, onClick = onShow)
            .semantics { contentDescription = label }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            label,
            modifier = Modifier.clearAndSetSemantics { },
            style = MaterialTheme.typography.labelLarge,
            color = LocalOvrlyPalette.current.ink
        )
    }
}

/**
 * The Stop choice. Both answers stop recording; "Keep recording" dismisses the prompt. The
 * host passes the answer to `StopChoiceHandler.onStopChoice`.
 */
@Composable
internal fun StopChoicePrompt(
    onChoice: (Boolean) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
    higherOpacity: Boolean = false
) {
    val p = LocalOvrlyPalette.current
    CompositionLocalProvider(LocalContentColor provides p.ink) {
        Column(
            modifier
                .widthIn(max = 360.dp)
                .mockGlass(higherOpacity)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text(
                "Stop capture?",
                modifier = Modifier.semantics {
                    heading()
                    liveRegion = LiveRegionMode.Polite
                },
                style = MaterialTheme.typography.titleSmall
            )
            Text(
                "Recording stops either way. Claims not yet checked stay marked incomplete.",
                style = MaterialTheme.typography.bodySmall,
                color = p.muted
            )
            Button(
                onClick = { onChoice(true) },
                modifier = Modifier.fillMaxWidth().sizeIn(minHeight = 48.dp)
            ) { Text(CONTINUE_RESEARCH_LABEL) }
            OutlinedButton(
                onClick = { onChoice(false) },
                modifier = Modifier.fillMaxWidth().sizeIn(minHeight = 48.dp)
            ) { Text(KEEP_AVAILABLE_LABEL) }
            TextButton(
                onClick = onCancel,
                modifier = Modifier.fillMaxWidth().sizeIn(minHeight = 48.dp)
            ) { Text("Keep recording") }
        }
    }
}
