package app.ovrly.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
/** The Stop choice in its own card, under the pill. */
@Composable
internal fun StopChoicePrompt(
    onChoice: (Boolean) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
    higherOpacity: Boolean = false
) {
    CompositionLocalProvider(LocalContentColor provides LocalOvrlyPalette.current.ink) {
        Column(
            modifier
                .widthIn(max = 360.dp)
                .mockGlass(higherOpacity)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            StopChoiceContent(onChoice, onCancel)
        }
    }
}

/**
 * The Stop choice. Both answers stop examining; "Keep examining" dismisses the prompt. The
 * host passes the answer to `StopChoiceHandler.onStopChoice`.
 */
@Composable
internal fun ColumnScope.StopChoiceContent(onChoice: (Boolean) -> Unit, onCancel: () -> Unit) {
    val p = LocalOvrlyPalette.current
    Text(
        "Stop examining?",
        modifier = Modifier.semantics {
            heading()
            liveRegion = LiveRegionMode.Polite
        },
        style = MaterialTheme.typography.titleSmall
    )
    Text(
        "Capture stops either way. Claims not yet checked stay marked incomplete.",
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
    ) { Text(KEEP_EXAMINING_LABEL) }
}

internal const val KEEP_EXAMINING_LABEL = "Keep examining"
