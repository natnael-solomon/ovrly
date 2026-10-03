package app.ovrly.ui.voice

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.ovrly.ui.LocalOvrlyPalette

/**
 * Design-gallery study: the dock with a scripted fake engine so the orb can be tuned on a device
 * without a microphone, network or Voxide key. Mirrors `orb_dock.html` from the design playground.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun VoiceOrbStudy(brand: @Composable () -> Unit) {
    val scope = rememberCoroutineScope()
    val fake = remember { FakeVoiceOrbSource(scope) }
    val dock = remember { VoiceOrbDockState(fake) }
    val muted = LocalOvrlyPalette.current.muted
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            "Voice orb",
            style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.semantics {
                heading()
            }
        )
        Text(
            "Tap the orb to open it; tap again to run a scripted turn. Hold for push-to-talk. " +
                "Scroll or press back to send it home.",
            style = MaterialTheme.typography.bodySmall,
            color = muted
        )
        VoiceOrbDock(dock, scrolledPastTop = false, brand = brand)
        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(onClick = { fake.start() }) { Text("Start turn") }
            TextButton(onClick = { fake.interrupt() }) { Text("Interrupt") }
            TextButton(onClick = { fake.fail() }) { Text("Fail") }
            TextButton(onClick = { fake.stop() }) { Text("Stop") }
        }
    }
}
