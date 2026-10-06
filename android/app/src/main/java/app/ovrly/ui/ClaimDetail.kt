package app.ovrly.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/** Whether claim detail is open, and what it shows besides the claim itself. */
internal data class ClaimDetail(
    val expanded: Boolean,
    val changes: List<String>,
    val comparedWith: Int?,
    val canCorrect: Boolean
)

@Composable
internal fun ClaimCard(
    claim: ClaimView,
    detail: ClaimDetail,
    onToggle: () -> Unit,
    onCorrect: () -> Unit,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    val expanded = detail.expanded
    Panel(claim.assessment.tone, modifier) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(GAP)).clickable(
                role = Role.Button,
                onClickLabel = if (expanded) "Hide claim detail" else "Show claim detail",
                onClick = onToggle
            ),
            verticalArrangement = Arrangement.spacedBy(GAP)
        ) {
            Muted(listOf(claim.interval, claim.modality).joinToString(" / "))
            Text(
                claim.proposition,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium
            )
            Text(claim.assessment.text, style = MaterialTheme.typography.labelLarge)
            claim.correction?.let { Text(it, style = MaterialTheme.typography.labelMedium) }
            Text(
                if (expanded) "Less" else "Detail and ${sources(claim)}",
                style = MaterialTheme.typography.labelSmall,
                color = if (p.dark) p.accent else p.ink
            )
        }
        if (expanded) ClaimBody(claim, detail, onCorrect)
    }
}

private fun sources(claim: ClaimView): String {
    val count = claim.evidence.size
    val noun = if (count == 1) "source" else "sources"
    val against = if (claim.contradicting > 0) ", ${claim.contradicting} contradicting" else ""
    return "$count $noun$against"
}

@Composable
private fun ClaimBody(
    claim: ClaimView,
    detail: ClaimDetail,
    onCorrect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(GAP)) {
        Field("Original wording", "\"${claim.originalText}\"", italic = true)
        Field("Normalized meaning", claim.proposition)
        claim.supersededProposition?.let { Field("Meaning before the correction", it) }
        Field("When", claim.interval)
        Field("Where it appeared", claim.modality)
        claim.summary?.let { Field("Assessment", it) }
        if (detail.comparedWith != null) {
            Field(
                "Changes since version ${detail.comparedWith}",
                detail.changes.joinToString("\n")
            )
        }
        if (detail.canCorrect) {
            OutlinedButton(onCorrect) { Text("Correct the meaning") }
        }
        Text(
            "Sources (${sources(claim)})",
            Modifier.semantics { heading() },
            style = MaterialTheme.typography.titleSmall
        )
        if (claim.evidence.isEmpty()) Muted("No sources yet.")
        claim.evidence.forEach { EvidenceCard(it) }
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    italic: Boolean = false
) {
    Column(modifier) {
        Muted(label)
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontStyle = if (italic) FontStyle.Italic else FontStyle.Normal
        )
    }
}

/** One evidence item: relation and rationale, source identity, access, retraction, passage. */
@Composable
internal fun EvidenceCard(evidence: EvidenceView, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    val uri = LocalUriHandler.current
    Panel(evidence.relation.tone, modifier) {
        Text(evidence.relation.text, style = MaterialTheme.typography.labelLarge)
        evidence.rationale?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        evidence.retraction?.let {
            Text(
                it.text,
                style = MaterialTheme.typography.labelLarge,
                color = if (it.tone == Tone.WARNING) p.error else p.ink
            )
        }
        Text(evidence.title, style = MaterialTheme.typography.titleSmall)
        Muted(evidence.publisher)
        Muted(listOf(evidence.sourceType, evidence.access, evidence.relevance).joinToString(" / "))
        evidence.passage?.let { Field("Passage read", "\"$it\"", italic = true) }
        evidence.url?.let { url ->
            TextButton({ uri.openUri(url) }) { Text("Open source") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CorrectionSheet(
    claim: ClaimView,
    onSubmit: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    var text by rememberSaveable(claim.id) { mutableStateOf(claim.proposition) }
    val problem = correctionProblem(claim.proposition, text)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        modifier = modifier,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ) {
        SheetColumn {
            Text("Correct the meaning", style = MaterialTheme.typography.titleMedium)
            Field("Original wording (kept as it was)", "\"${claim.originalText}\"", italic = true)
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("What the claim means") },
                isError = problem != null && text != claim.proposition,
                supportingText = { problem?.let { Text(it) } }
            )
            Muted(
                "Saved as a new version marked \"Corrected by you\". The earlier version is " +
                    "kept, and only this claim is checked again."
            )
            Button({ onSubmit(text) }, Modifier.fillMaxWidth(), enabled = problem == null) {
                Text("Save correction")
            }
            TextButton(onDismiss, Modifier.fillMaxWidth()) { Text("Cancel") }
        }
    }
}

/**
 * Confirms that a later share is the full video of a captured clip. Nothing is matched
 * automatically: the user picks one check and confirms it is the same video.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FullVideoSheet(
    candidates: List<InboxItem>,
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    var chosen by rememberSaveable { mutableStateOf<String?>(null) }
    var confirmed by rememberSaveable { mutableStateOf(false) }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        modifier = modifier,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ) {
        SheetColumn {
            Text("Is this the same video?", style = MaterialTheme.typography.titleMedium)
            Muted(
                "Pick the full video you shared. ovrly does not match videos for you, and " +
                    "never combines clips from different videos. Captured times stay on the " +
                    "capture timeline; ovrly does not guess where they fall in the full video."
            )
            Column(Modifier.selectableGroup()) {
                candidates.forEach { item ->
                    Choice(item, item.serverId == chosen, { chosen = item.serverId })
                }
            }
            Row(
                Modifier.fillMaxWidth().toggleable(
                    value = confirmed,
                    role = Role.Checkbox,
                    onValueChange = { confirmed = it }
                ),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Checkbox(checked = confirmed, onCheckedChange = null)
                Text("I confirm this is the full video of the clip I captured.")
            }
            Muted("The current version is kept. Checking the full video adds a new version.")
            Button(
                { chosen?.let(onConfirm) },
                Modifier.fillMaxWidth(),
                enabled = chosen != null && confirmed
            ) { Text("Check the full video") }
            TextButton(onDismiss, Modifier.fillMaxWidth()) { Text("Cancel") }
        }
    }
}

@Composable
private fun Choice(
    item: InboxItem,
    selected: Boolean,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier
) {
    Row(
        modifier.fillMaxWidth().selectable(selected, role = Role.RadioButton, onClick = onSelect),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(selected = selected, onClick = null)
        Column {
            Text(item.title, style = MaterialTheme.typography.bodyMedium)
            Muted(item.age)
        }
    }
}

@Composable
private fun SheetColumn(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(
        modifier.fillMaxWidth().navigationBarsPadding()
            .padding(start = SHEET_PADDING, end = SHEET_PADDING, bottom = SHEET_PADDING),
        verticalArrangement = Arrangement.spacedBy(SHEET_GAP)
    ) { content() }
}

private val GAP = 8.dp
private val SHEET_PADDING = 24.dp
private val SHEET_GAP = 12.dp
