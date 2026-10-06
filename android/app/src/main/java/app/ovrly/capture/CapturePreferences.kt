package app.ovrly.capture

import android.content.Context
import androidx.core.content.edit

/** The user's capture upload preference. */
object CapturePreferences {
    private const val FILE = "capture"
    private const val WIFI_ONLY = "wifiOnly"

    /** True when chunks may only be sent over an unmetered network such as Wi-Fi. */
    fun wifiOnly(context: Context): Boolean =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean(WIFI_ONLY, false)

    /** Saves the preference and reschedules the current capture's upload with it. */
    fun setWifiOnly(context: Context, wifiOnly: Boolean) {
        val app = context.applicationContext
        app.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit {
            putBoolean(WIFI_ONLY, wifiOnly)
        }
        val root = CaptureFiles.rootOf(app)
        val manifest = CaptureFiles.manifestOrNull(root) ?: return
        val ledger = CaptureLedger(root)
        if (!ledger.uploadStopped) CaptureUploads.schedule(app, manifest.sessionId, replace = true)
        CaptureStore.upload(
            UploadProgress.of(
                manifest,
                ledger,
                CaptureApis.isTestServer,
                wifiOnly = wifiOnly
            )
        )
    }
}
