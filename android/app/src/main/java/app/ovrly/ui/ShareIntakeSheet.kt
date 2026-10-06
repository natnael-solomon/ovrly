package app.ovrly.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.ovrly.data.CheckStatus
import app.ovrly.share.IntakeAction
import app.ovrly.share.IntakeState
import app.ovrly.share.ShareProblem
import java.util.Locale

/**
 * Bottom sheet shown over the source app while a share becomes an investigation. It keeps
 * the three intake states apart (a private copy on this device, uploading, accepted by the
 * service) and explains every rejection, offering a permitted file instead.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ShareIntakeSheet(
    state: IntakeState,
    waking: Boolean,
    onAction: (IntakeAction) -> Unit,
    onPickFile: (Uri) -> Unit,
    modifier: Modifier = Modifier
) {
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) {
        it?.let(onPickFile)
    }
    if (state != IntakeState.Idle) {
        ModalBottomSheet(
            onDismissRequest = { onAction(IntakeAction.DISMISS) },
            modifier = modifier,
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ) {
            Column(
                Modifier.fillMaxWidth().navigationBarsPadding()
                    .padding(start = 24.dp, end = 24.dp, bottom = 24.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite },
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                if (waking) {
                    Text(
                        "Waking the ovrly service... The first request after a quiet period " +
                            "can take up to a minute.",
                        style = MaterialTheme.typography.bodySmall,
                        color = LocalOvrlyPalette.current.muted
                    )
                }
                IntakeBody(state, onAction) { picker.launch(arrayOf("video/*")) }
            }
        }
    }
}

@Composable
private fun IntakeBody(
    state: IntakeState,
    onAction: (IntakeAction) -> Unit,
    onChooseFile: () -> Unit
) {
    when (state) {
        IntakeState.Idle -> Unit

        IntakeState.Inspecting ->
            Progress("Checking the shared item", "Nothing has left this device.")

        is IntakeState.Staging -> Progress(
            "Saving a private copy",
            "Kept on this device only until it is uploaded, then deleted.",
            state.copiedBytes,
            state.totalBytes
        )

        is IntakeState.Uploading -> Progress(
            "Uploading to ovrly",
            "${megabytes(state.sentBytes)} of ${megabytes(state.totalBytes)}",
            state.sentBytes,
            state.totalBytes
        )

        IntakeState.Submitting -> Progress("Creating the check", "The upload is complete.")

        is IntakeState.Tracking -> Accepted(state, onAction)

        is IntakeState.Duplicate -> Duplicate(state, onAction)

        is IntakeState.Rejected -> Rejected(state, onAction, onChooseFile)

        is IntakeState.Failed -> Failed(state, onAction)
    }
}

/** One state of the sheet: a heading, its content and, when [onAction] is set, Close. */
@Composable
private fun Section(
    title: String,
    onAction: ((IntakeAction) -> Unit)?,
    error: Boolean = false,
    content: @Composable () -> Unit
) {
    val palette = LocalOvrlyPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = if (error) palette.error else palette.ink
        )
        content()
        if (onAction != null) {
            TextButton({ onAction(IntakeAction.DISMISS) }, Modifier.fillMaxWidth()) {
                Text("Close")
            }
        }
    }
}

/** Indeterminate unless [total] is known and positive. */
@Composable
private fun Progress(title: String, detail: String, done: Long = 0, total: Long? = null) {
    Section(title, onAction = null) {
        Text(detail, style = MaterialTheme.typography.bodyMedium)
        if (total == null || total <= 0) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
        } else {
            val fraction = (done.toFloat() / total).coerceIn(0f, 1f)
            LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth())
        }
    }
}

@Composable
private fun Accepted(state: IntakeState.Tracking, onAction: (IntakeAction) -> Unit) {
    Section("Check accepted", onAction) {
        Text(
            "The ovrly service has this share and keeps working if you close this sheet.",
            style = MaterialTheme.typography.bodyMedium
        )
        Text("Status: ${state.status.label}", style = MaterialTheme.typography.titleSmall)
        if (!state.status.terminal) LinearProgressIndicator(Modifier.fillMaxWidth())
        state.notice?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun Duplicate(state: IntakeState.Duplicate, onAction: (IntakeAction) -> Unit) {
    Section("Already shared", onAction) {
        Text(
            "You shared this before. That check is: ${state.status.label.lowercase()}.",
            style = MaterialTheme.typography.bodyMedium
        )
        Button({ onAction(IntakeAction.OPEN_EXISTING) }, Modifier.fillMaxWidth()) {
            Text("Open the existing check")
        }
        OutlinedButton({ onAction(IntakeAction.CHECK_AGAIN) }, Modifier.fillMaxWidth()) {
            Text("Check it again")
        }
    }
}

@Composable
private fun Rejected(
    state: IntakeState.Rejected,
    onAction: (IntakeAction) -> Unit,
    onChooseFile: () -> Unit
) {
    Section(state.problem.title, onAction, error = true) {
        Text(state.problem.message, style = MaterialTheme.typography.bodyMedium)
        if (state.problem == ShareProblem.TOO_LARGE) {
            Text(
                "Limit: ${megabytes(state.maxBytes)}.",
                style = MaterialTheme.typography.bodyMedium
            )
        }
        if (state.problem.offersFile) {
            Text(ShareProblem.FILE_ALTERNATIVE, style = MaterialTheme.typography.bodyMedium)
            Button(onChooseFile, Modifier.fillMaxWidth()) { Text("Choose a video file") }
        }
    }
}

@Composable
private fun Failed(state: IntakeState.Failed, onAction: (IntakeAction) -> Unit) {
    Section(state.title, onAction, error = true) {
        Text(state.message, style = MaterialTheme.typography.bodyMedium)
        state.requestId?.let {
            Text("Reference: $it", style = MaterialTheme.typography.bodySmall)
        }
        if (state.retryable) {
            Button({ onAction(IntakeAction.RETRY) }, Modifier.fillMaxWidth()) {
                Text("Try again")
            }
        }
    }
}

internal val CheckStatus.label: String
    get() = when (this) {
        CheckStatus.WAITING -> "Waiting to start"
        CheckStatus.CHECKING -> "Checking"
        CheckStatus.PARTIAL -> "Partial results ready"
        CheckStatus.COMPLETE -> "Complete"
        CheckStatus.FAILED -> "Could not be checked"
        CheckStatus.CANCELLED -> "Cancelled"
        CheckStatus.UNKNOWN -> "Status not recognised by this app version"
    }

private fun megabytes(bytes: Long): String =
    String.format(Locale.getDefault(), "%.1f MB", bytes / BYTES_PER_MEGABYTE)

private const val BYTES_PER_MEGABYTE = 1_000_000.0
