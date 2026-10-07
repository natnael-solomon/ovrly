package app.ovrly.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/*
 * Saved reports (AN-10, #36): the save control of an open report and the Saved reports list
 * in the Library. Saving is always an explicit tap; the copy says what is and is not kept.
 */

/** Save or remove the shown version, and say whether it is saved. */
@Composable
internal fun SaveCard(
    save: SaveState,
    busy: Boolean,
    onCommand: (CheckCommand) -> Unit,
    modifier: Modifier = Modifier
) {
    val version = save.version?.let { "version $it" } ?: "this version"
    Panel(Tone.NEUTRAL, modifier.semantics { liveRegion = LiveRegionMode.Polite }) {
        Text(
            if (save.saved) "SAVED" else "NOT SAVED",
            style = MaterialTheme.typography.labelSmall,
            color = LocalOvrlyPalette.current.muted
        )
        Text(
            when {
                save.copy && save.saved -> "This is the copy of $version you saved."
                save.saved -> "You saved $version of this report."
                else -> "Save this report"
            },
            style = MaterialTheme.typography.titleSmall
        )
        Text(
            if (save.saved) {
                "A copy is kept for you on the ovrly service. It does not change when the " +
                    "check gets a new version."
            } else {
                "Saving keeps a copy of $version for you on the ovrly service. Nothing is " +
                    "saved unless you choose to."
            },
            style = MaterialTheme.typography.bodyMedium
        )
        save.otherSavedVersion?.let { Muted("You saved version $it of this check.") }
        if (save.saved) {
            OutlinedButton({ onCommand(CheckCommand.Unsave(save.reportId)) }, enabled = !busy) {
                Text("Remove from saved")
            }
        } else {
            OutlinedButton({ onCommand(CheckCommand.Save(save.reportId)) }, enabled = !busy) {
                Text("Save report")
            }
        }
    }
}

/** The Library's Saved reports: the owner's explicit saves, newest first. */
@Composable
internal fun SavedSection(
    items: List<SavedItem>,
    loaded: Boolean,
    onCommand: (CheckCommand) -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(SMALL_GAP)) {
        Text(
            "Saved reports",
            Modifier.semantics { heading() },
            style = MaterialTheme.typography.titleMedium
        )
        Muted(SAVED_SCOPE)
        if (loaded && items.isEmpty()) {
            Muted("Open a finished report and tap Save report to keep a copy here.")
        }
        items.forEach { item ->
            SavedRow(item, { onCommand(CheckCommand.OpenSaved(item.reportId)) })
        }
    }
}

@Composable
private fun SavedRow(item: SavedItem, onOpen: () -> Unit, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    Surface(
        onClick = onOpen,
        modifier = modifier.fillMaxWidth(),
        enabled = item.readable,
        shape = RoundedCornerShape(CARD_RADIUS),
        color = p.surface,
        border = BorderStroke(1.dp, p.rule)
    ) {
        Column(Modifier.padding(CARD_PADDING), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(item.title, style = MaterialTheme.typography.titleSmall)
            Muted(item.detail)
            if (!item.onDevice && item.readable) Muted("Opens the copy you saved.")
        }
    }
}
