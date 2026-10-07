package app.ovrly.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/**
 * The real part of Your space (RFC-D22): the Inbox of checks in progress or needing
 * attention, then the Library of finished reports. Sample reports stay below, labeled.
 */
@Composable
internal fun ChecksSections(
    state: ChecksUiState,
    onCommand: (CheckCommand) -> Unit,
    modifier: Modifier = Modifier
) {
    var confirmCancel by rememberSaveable { mutableStateOf<String?>(null) }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(SECTION_GAP)) {
        SectionHeading("Inbox")
        ConnectionNotes(state)
        state.notice?.let { Notice(it, { onCommand(CheckCommand.DismissNotice) }) }
        when {
            !state.available -> Muted("Checking is not set up in this build.")

            !state.loaded -> Muted("Loading your checks...")

            state.inbox.isEmpty() -> Muted(
                "Nothing in progress. Share a video or link to ovrly from another app to " +
                    "start a check."
            )
        }
        state.inbox.forEach { item ->
            CheckRow(item, item.localId in state.busy, { action ->
                when (action) {
                    InboxAction.CANCEL -> {
                        confirmCancel = item.localId
                    }

                    else -> onCommand(action.command(item))
                }
            })
        }
        SectionHeading("Library")
        if (state.loaded && state.library.isEmpty()) {
            Muted("Finished reports appear here.")
        }
        state.library.forEach { item ->
            CheckRow(item, item.localId in state.busy, { onCommand(it.command(item)) })
        }
        if (state.available) {
            SavedSection(state.saved, state.loaded, onCommand)
            AccountSection(state.account, onCommand)
        }
        confirmCancel?.let { localId ->
            CancelDialog(
                onConfirm = {
                    confirmCancel = null
                    onCommand(CheckCommand.Cancel(localId))
                },
                onDismiss = { confirmCancel = null }
            )
        }
    }
}

private fun InboxAction.command(item: InboxItem): CheckCommand = when (this) {
    InboxAction.OPEN -> CheckCommand.Open(item.serverId.orEmpty())
    InboxAction.CANCEL -> CheckCommand.Cancel(item.localId)
    InboxAction.RETRY -> CheckCommand.Retry(item.localId)
    InboxAction.CONTINUE -> CheckCommand.Continue(item.localId)
    InboxAction.OPEN_RETRY -> CheckCommand.Open(item.retriedAs.orEmpty())
}

@Composable
private fun SectionHeading(text: String) {
    Text(text, Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
}

@Composable
internal fun Muted(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier,
        style = MaterialTheme.typography.bodySmall,
        color = LocalOvrlyPalette.current.muted
    )
}

@Composable
private fun ConnectionNotes(state: ChecksUiState) {
    Column(Modifier.semantics { liveRegion = LiveRegionMode.Polite }) {
        if (state.waking) Muted("Waking the ovrly service. This can take up to a minute.")
        if (state.offline) {
            Muted("Offline. Showing what this device last saw; status may be out of date.")
        }
        if (state.updateNeeded) Muted("Update needed: this app cannot read the service's answer.")
    }
}

@Composable
internal fun Notice(text: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    Surface(
        modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
        shape = RoundedCornerShape(CARD_RADIUS),
        color = p.surface,
        border = BorderStroke(1.dp, p.rule)
    ) {
        Row(Modifier.padding(start = CARD_PADDING)) {
            Text(
                text,
                Modifier.weight(1f).padding(vertical = CARD_PADDING),
                style = MaterialTheme.typography.bodyMedium
            )
            TextButton(onDismiss) { Text("OK") }
        }
    }
}

@Composable
private fun CheckRow(
    item: InboxItem,
    busy: Boolean,
    onAction: (InboxAction) -> Unit,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    val opens = InboxAction.OPEN in item.actions
    Surface(
        onClick = { onAction(InboxAction.OPEN) },
        modifier = modifier,
        enabled = opens,
        shape = RoundedCornerShape(CARD_RADIUS),
        color = p.surface,
        border = BorderStroke(1.dp, p.rule.copy(alpha = RULE_ALPHA))
    ) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            ToneBar(item.tone)
            Column(
                Modifier.weight(1f).padding(CARD_PADDING),
                verticalArrangement = Arrangement.spacedBy(ROW_GAP)
            ) {
                Text(item.title, style = MaterialTheme.typography.titleSmall)
                Text(
                    listOfNotNull(item.status, item.stage).joinToString(" / "),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (item.tone == Tone.WARNING) p.error else p.ink
                )
                Muted(listOfNotNull(item.age, item.detail).joinToString(". "))
                ActionRow(item.actions - InboxAction.OPEN, busy, onAction)
            }
        }
    }
}

@Composable
private fun ActionRow(
    actions: List<InboxAction>,
    busy: Boolean,
    onAction: (InboxAction) -> Unit,
    modifier: Modifier = Modifier
) {
    if (actions.isEmpty()) return
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(ROW_GAP)) {
        actions.forEach { action ->
            OutlinedButton({ onAction(action) }, enabled = !busy) {
                Text(
                    when (action) {
                        InboxAction.OPEN -> "Open"
                        InboxAction.CANCEL -> "Cancel check"
                        InboxAction.RETRY -> "Try again"
                        InboxAction.CONTINUE -> "Continue checking"
                        InboxAction.OPEN_RETRY -> "Open the new check"
                    }
                )
            }
        }
    }
}

/** A color cue next to text that always states the same thing in words. */
@Composable
internal fun ToneBar(tone: Tone, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    val color = when (tone) {
        Tone.SUPPORT -> p.accent
        Tone.CHALLENGE, Tone.WARNING -> p.error
        Tone.QUALIFY, Tone.MIXED -> p.muted
        Tone.INSUFFICIENT -> p.rule
        Tone.NEUTRAL -> Color.Transparent
    }
    Box(modifier.width(TONE_BAR).fillMaxHeight().background(color))
}

@Composable
private fun CancelDialog(
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = modifier,
        confirmButton = { TextButton(onConfirm) { Text("Cancel check") } },
        dismissButton = { TextButton(onDismiss) { Text("Keep checking") } },
        title = { Text("Cancel this check?") },
        text = {
            Text(
                "Checking stops. Results published so far are kept. You can start a new " +
                    "check of the same video later."
            )
        }
    )
}

internal val CARD_RADIUS = 16.dp
internal val CARD_PADDING = 16.dp
private val SECTION_GAP = 12.dp
private val ROW_GAP = 8.dp
private val TONE_BAR = 4.dp
private const val RULE_ALPHA = 0.7f
