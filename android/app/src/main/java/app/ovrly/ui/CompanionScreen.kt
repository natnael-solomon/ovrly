package app.ovrly.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureState
import app.ovrly.share.SharedInput

@Composable
@OptIn(ExperimentalLayoutApi::class)
fun CompanionScreen(
    capture: CaptureState,
    share: SharedInput?,
    storageBusy: Boolean,
    overlayAllowed: Boolean,
    overlayVisible: Boolean,
    notificationsAllowed: Boolean,
    higherOpacity: Boolean,
    voiceActive: Boolean,
    voiceMessage: String,
    voiceConfigured: Boolean,
    showSetup: Boolean,
    onDismissSetup: () -> Unit,
    onConfirmSetup: () -> Unit,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onOverlayPermission: () -> Unit,
    onShowOverlay: () -> Unit,
    onHideOverlay: () -> Unit,
    onResetOverlay: () -> Unit,
    onOpacity: (Boolean) -> Unit,
    onDeleteCapture: () -> Unit,
    onClearShare: () -> Unit,
    onGallery: () -> Unit,
    onNotificationPermission: () -> Unit,
    onVoiceStart: () -> Unit,
    onVoiceStop: () -> Unit,
    dark: Boolean,
    onDark: (Boolean) -> Unit,
    overlayStatus: String,
    demoActive: Boolean,
    onDemo: () -> Unit,
) {
    val palette = LocalOvrlyPalette.current
    val Graphite = palette.ink
    val Coral = palette.error
    Scaffold { insets ->
        Column(Modifier.fillMaxSize().padding(insets).verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 20.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Settings", style = MaterialTheme.typography.titleMedium)
            }
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Appearance", style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.padding(vertical = 12.dp))
                ThemeChoice(dark, onDark)
            }
            HorizontalDivider()
            SectionTitle("Capture", "Up to 3 min / 32 MiB / stored locally")
            Surface(shape = RoundedCornerShape(24.dp), color = palette.surface,
                border = BorderStroke(1.dp, palette.rule)) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (capture.phase == CapturePhase.RECORDING) {
                            Spacer(Modifier.size(8.dp).background(Coral, RoundedCornerShape(4.dp)))
                        }
                        Text(when (capture.phase) {
                            CapturePhase.RECORDING -> "REC  %d:%02d / 3:00".format(capture.seconds / 60, capture.seconds % 60)
                            CapturePhase.STARTING -> "Starting capture"
                            CapturePhase.FINISHED -> "Interval captured locally"
                            CapturePhase.ERROR -> "Capture needs attention"
                            CapturePhase.IDLE -> "No active capture"
                        }, style = MaterialTheme.typography.titleMedium)
                    }
                    Text(capture.message, style = MaterialTheme.typography.bodyMedium)
                    if (capture.hasLocalCapture || capture.busy) {
                        Text("${capture.frames} screen samples / ${capture.bytes / 1024} KiB local",
                            style = MaterialTheme.typography.labelMedium)
                    }
                    if (capture.busy) {
                        Button(onClick = onStop, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = Coral,
                                contentColor = MaterialTheme.colorScheme.onError)) {
                            Text("Stop capture")
                        }
                    } else {
                        Button(onClick = onStart, enabled = !storageBusy && !voiceActive,
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(12.dp)) {
                            Text(if (storageBusy) "Preparing storage..." else "Set up capture")
                        }
                    }
                }
            }
            SettingsDisclosure("Capture details",
                "Playback audio and a screen image about every 5 seconds. No microphone fallback, transcription, research or upload. Android consent is required each time.")
            HorizontalDivider()
            SectionTitle("Overlay", if (overlayVisible) "Visible" else "Off")
            if (!overlayAllowed) {
                OutlinedButton(onClick = onOverlayPermission, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Allow overlay access") }
            } else if (!overlayVisible) {
                OutlinedButton(onClick = onShowOverlay, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Show overlay") }
            } else {
                OutlinedButton(onClick = onHideOverlay, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text(if (demoActive) "Close demo overlay" else "Hide overlay only") }
                TextButton(onClick = onResetOverlay) { Text("Reset position") }
            }
            Text("Hiding the overlay does not stop capture.",
                style = MaterialTheme.typography.bodySmall)
            if (overlayVisible) Text(overlayStatus, style = MaterialTheme.typography.labelMedium, color = palette.muted)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Solid glass", style = MaterialTheme.typography.titleSmall)
                    Text("Higher contrast", style = MaterialTheme.typography.bodySmall)
                }
                Switch(checked = higherOpacity, onCheckedChange = onOpacity)
            }
            if (!notificationsAllowed) {
                OutlinedButton(onClick = onNotificationPermission, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Allow notifications") }
            }
            HorizontalDivider()
            SectionTitle("Shared video", "References only / not analyzed")
            if (share == null) {
                Text("Share a video or link to ovrly from another app.",
                    style = MaterialTheme.typography.bodyMedium)
                SettingsDisclosure("Supported input", "Video references up to 10 minutes, or one web URL. Nothing is downloaded, copied or queued.")
            } else {
                Text(share.title, style = MaterialTheme.typography.titleSmall,
                    color = if (share.accepted) Graphite else Coral)
                Text(share.detail, style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = onClearShare) { Text("Clear shared reference") }
            }
            HorizontalDivider()
            SectionTitle("Voice", if (voiceConfigured) "Experimental / Voxide" else "Not configured")
            Text(voiceMessage, style = MaterialTheme.typography.bodyMedium)
            if (voiceActive) {
                OutlinedButton(onClick = onVoiceStop, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Stop microphone") }
            } else {
                OutlinedButton(onClick = onVoiceStart, enabled = voiceConfigured && !capture.busy && !storageBusy,
                    modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Start companion voice") }
            }
            if (voiceConfigured) Text("Sends microphone audio to Voxide. Stops when you leave the app.",
                style = MaterialTheme.typography.bodySmall)
            SettingsDisclosure("Voice details",
                "Native authorization is unverified. Voice can open the gallery only; it cannot start capture or the overlay, or discuss evidence. Playback capture is separate.")
            HorizontalDivider()
            SectionTitle("Storage", "Private / temporary")
            SettingsDisclosure("Retention details",
                "One capture at a time, up to 32 MiB. A new capture replaces it. Interrupted data and captures older than 24 hours are removed next time the app opens. No upload or account sync.")
            OutlinedButton(onClick = onDeleteCapture, enabled = !capture.busy && !storageBusy,
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) { Text("Delete local capture") }
            HorizontalDivider()
            SectionTitle("Previews", "Simulated content")
            TextButton(onClick = onGallery, modifier = Modifier.fillMaxWidth()) { Text("Design gallery") }
            OutlinedButton(onClick = onDemo, enabled = !storageBusy, modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp)) { Text("Open demo overlay") }
            Text("Replaces compact controls. Nothing records or restarts.",
                style = MaterialTheme.typography.bodySmall, color = palette.muted)
            Spacer(Modifier.height(8.dp))
        }
    }
    if (showSetup) {
        AlertDialog(
            onDismissRequest = onDismissSetup,
            title = { Text("Capture only your chosen interval") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("After Android's consent, capture starts immediately for up to 3 minutes. Return to your video and use the overlay or notification to stop.")
                    Text("Stores eligible playback audio (not your microphone) and a screen image about every 5 seconds. This is not a full video recording and cannot cover unseen content.")
                    Text("Android's audio permission is required for playback capture. Audio may include other eligible apps in your profile, even when you select one app's screen. Source apps may block audio or protect pixels.")
                    Text("Previous local capture will be replaced. No research or upload starts. Locking the screen, revoking projection, or stopping capture releases media access.")
                }
            },
            confirmButton = { TextButton(onClick = onConfirmSetup, enabled = !capture.busy && !storageBusy) { Text("Continue to Android consent") } },
            dismissButton = { TextButton(onClick = onDismissSetup) { Text("Not now") } },
        )
    }
}

@Composable
private fun SectionTitle(title: String, detail: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(detail, style = MaterialTheme.typography.labelMedium, color = LocalOvrlyPalette.current.muted)
    }
}

@Composable
private fun SettingsDisclosure(label: String, detail: String) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = { expanded = !expanded }) {
            Text(if (expanded) "$label / Less" else "$label / More")
        }
        if (expanded) Text(detail, style = MaterialTheme.typography.bodySmall,
            color = LocalOvrlyPalette.current.muted)
    }
}
