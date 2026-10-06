package app.ovrly.overlay

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.ContextCompat

/**
 * Debug builds only: shows the compact overlay with [FixtureLiveResultsSource] for device
 * screenshots, without recording or a network request. Start it with
 * `adb shell am start -n app.ovrly/.overlay.LiveFixtureActivity`. Release builds have neither
 * this activity nor the fixture action.
 */
class LiveFixtureActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Settings.canDrawOverlays(this)) {
            ContextCompat.startForegroundService(
                this,
                Intent(this, OverlayService::class.java).setAction(OverlayService.SHOW_LIVE_FIXTURE)
            )
        }
        finish()
    }
}
