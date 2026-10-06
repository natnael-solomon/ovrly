package app.ovrly.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import app.ovrly.capture.CapturePreferences

/** The Wi-Fi-only upload preference for capture chunks. */
@Composable
fun CaptureUploadSettings(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var wifiOnly by rememberSaveable { mutableStateOf(CapturePreferences.wifiOnly(context)) }
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Upload on Wi-Fi only", style = MaterialTheme.typography.titleSmall)
            Text(
                "Chunks stay on this device until Wi-Fi is available.",
                style = MaterialTheme.typography.bodySmall
            )
        }
        Switch(
            checked = wifiOnly,
            onCheckedChange = {
                wifiOnly = it
                CapturePreferences.setWifiOnly(context, it)
            }
        )
    }
}
