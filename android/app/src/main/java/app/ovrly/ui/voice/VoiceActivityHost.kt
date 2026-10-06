package app.ovrly.ui.voice

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.ovrly.contract.Investigation
import app.ovrly.data.checkStatus
import app.ovrly.ui.LocalOvrlyPalette
import app.ovrly.ui.label
import app.ovrly.voice.PendingConfirmation
import app.ovrly.voice.VoiceActivity
import app.ovrly.voice.VoiceCommandContract
import app.ovrly.voice.VoiceController
import app.ovrly.voice.VoiceInteractionPhase as EnginePhase
import app.ovrly.voice.VoiceResult
import app.ovrly.voice.VoiceState

/**
 * The voice results panel over the app's tabs: what the user said, the result of the last
 * action, the check `open_check` opened, and the typed alternative. It appears while voice is
 * live, after any result, and on a denied microphone or a voice error, and it hosts the
 * on-screen confirmation for cancelling a check. Direct controls elsewhere are unchanged.
 */
@Composable
internal fun VoiceActivityHost(
    voice: VoiceController,
    dock: VoiceOrbDockState,
    modifier: Modifier = Modifier
) {
    val state by voice.state.collectAsStateWithLifecycle()
    val activity by voice.activity.collectAsStateWithLifecycle()
    val opened by voice.openedCheck.collectAsStateWithLifecycle()
    val pending by voice.confirmations.pending.collectAsStateWithLifecycle()
    var dismissed by remember(state) { mutableStateOf(false) }
    val failed = state.phase == EnginePhase.ERROR || dock.microphoneDenied
    val visible = !dismissed && (
        state.active || failed || activity.heard != null || activity.result != null ||
            opened != null
        )
    Box(modifier) {
        if (visible) {
            VoicePanel(
                content = PanelContent(state, activity, dock.microphoneDenied, failed, opened),
                loadCheck = voice::check,
                onType = voice::typed,
                onClose = {
                    dismissed = true
                    voice.clearActivity()
                }
            )
        }
        pending?.let { request ->
            CancelConfirmation(
                request,
                onAnswer = { approved -> voice.confirmations.answer(request.id, approved) }
            )
        }
    }
}

/** What the panel shows; grouped so the panel takes its data as one value. */
private data class PanelContent(
    val state: VoiceState,
    val activity: VoiceActivity,
    val microphoneDenied: Boolean,
    val failed: Boolean,
    val openedCheck: String?
)

@Composable
private fun VoicePanel(
    content: PanelContent,
    loadCheck: suspend (String) -> Investigation?,
    onType: (String) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier
) {
    val palette = LocalOvrlyPalette.current
    Surface(
        modifier = modifier.fillMaxWidth().navigationBarsPadding()
            .padding(start = 16.dp, end = 16.dp, bottom = 96.dp),
        shape = RoundedCornerShape(20.dp),
        color = palette.surface,
        border = BorderStroke(1.dp, palette.rule)
    ) {
        Column(
            Modifier.padding(16.dp).semantics { liveRegion = LiveRegionMode.Polite },
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Voice",
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f)
                )
                TextButton(onClick = onClose) { Text("Close") }
            }
            if (content.failed) Problem(content.state, content.microphoneDenied)
            content.activity.heard?.let { Line("You said", "\u201c$it\u201d") }
            content.activity.result?.let { Outcome(it) }
            content.openedCheck?.let { CheckCard(it, loadCheck) }
            TypedCommand(
                expanded = content.failed || content.activity.result?.success == false,
                onType = onType
            )
        }
    }
}

@Composable
private fun Problem(state: VoiceState, microphoneDenied: Boolean) {
    val message = if (microphoneDenied) {
        "Voice needs the microphone. You can type a command instead."
    } else {
        "${state.status}: ${state.message} You can type a command instead."
    }
    Text(
        message,
        style = MaterialTheme.typography.bodyMedium,
        color = LocalOvrlyPalette.current.error
    )
}

@Composable
private fun Line(label: String, text: String) {
    val muted = LocalOvrlyPalette.current.muted
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = muted)
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Outcome(result: VoiceResult) {
    val palette = LocalOvrlyPalette.current
    val label = if (result.typed) "${result.label} (typed)" else result.label
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = palette.muted)
        Text(
            result.message,
            style = MaterialTheme.typography.bodyMedium,
            color = if (result.success || result.pending) palette.ink else palette.error
        )
    }
}

/** Minimal check view until the report screens (#34) exist. */
@Composable
private fun CheckCard(id: String, load: suspend (String) -> Investigation?) {
    var loaded by remember(id) { mutableStateOf<Investigation?>(null) }
    var finished by remember(id) { mutableStateOf(false) }
    val currentLoad by rememberUpdatedState(load)
    LaunchedEffect(id) {
        loaded = currentLoad(id)
        finished = true
    }
    val investigation = loaded
    val text = when {
        investigation != null -> summary(investigation)
        finished -> "This check could not be loaded."
        else -> "Loading the check..."
    }
    Line("Opened check", text)
}

private fun summary(investigation: Investigation): String {
    val report = investigation.report
    val claims = report?.claims?.size
    val detail = when {
        report == null -> "No report yet."
        report.fixture -> "Development fixture report, not a check of this video."
        claims == 0 -> "No checkable claims were found."
        report.provisional -> "$claims claims so far (provisional)."
        else -> "$claims claims."
    }
    return "${investigation.checkStatus.label}. $detail"
}

@Composable
private fun TypedCommand(
    expanded: Boolean,
    onType: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    var open by rememberSaveable { mutableStateOf(false) }
    var text by rememberSaveable { mutableStateOf("") }
    val send = {
        if (text.isNotBlank()) {
            onType(text)
            text = ""
        }
    }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (!expanded && !open) {
            TextButton(onClick = { open = true }) { Text("Type a command instead") }
        } else {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it.take(MAX_TYPED) },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Typed command") },
                supportingText = { Text(VoiceCommandContract.TYPED_HINT) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() })
            )
            Button(
                onClick = send,
                enabled = text.isNotBlank(),
                modifier = Modifier.fillMaxWidth()
            ) { Text("Send") }
        }
    }
}

@Composable
private fun CancelConfirmation(
    request: PendingConfirmation,
    onAnswer: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    AlertDialog(
        onDismissRequest = { onAnswer(false) },
        modifier = modifier,
        title = { Text("Cancel this check?") },
        text = {
            Text(
                "Voice asked to cancel the running check " +
                    "(job ${request.target.take(SHORT_ID)}). " +
                    "Nothing changes unless you confirm here."
            )
        },
        confirmButton = { TextButton(onClick = { onAnswer(true) }) { Text("Cancel check") } },
        dismissButton = { TextButton(onClick = { onAnswer(false) }) { Text("Keep it running") } }
    )
}

private const val MAX_TYPED = 160
private const val SHORT_ID = 8
