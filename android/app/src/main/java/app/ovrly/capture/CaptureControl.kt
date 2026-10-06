package app.ovrly.capture

import android.content.Context
import android.content.Intent
import java.io.IOException

/** Stop controls for the overlay and the companion. */
object CaptureControl {
    /** Stops recording and releases media access; the continuation choice stays open. */
    fun stop(context: Context) {
        val app = context.applicationContext
        app.startService(Intent(app, CaptureService::class.java).setAction(CaptureService.STOP))
    }

    /**
     * Stops recording if it is still running and records whether research continues. With
     * `true` the remaining chunks are sent and the server session is closed with
     * `continue_research: true`; with `false` no further chunk is sent and the session is
     * closed with `continue_research: false`. Calling it again with the same value is harmless.
     * Returns false when there is no local capture or a different choice was already recorded.
     * Reads and writes two small private files on the calling thread.
     */
    fun stop(context: Context, continueResearch: Boolean): Boolean {
        val accepted = recordChoice(context, continueResearch)
        if (accepted && CaptureStore.state.value.busy) stop(context)
        return accepted
    }

    /**
     * Records the continuation choice without stopping anything, publishes the upload state and
     * schedules the upload that closes the session. The first choice wins.
     */
    internal fun recordChoice(context: Context, continueResearch: Boolean): Boolean {
        val app = context.applicationContext
        val root = CaptureFiles.rootOf(app)
        val manifest = CaptureFiles.manifestOrNull(root)
        val ledger = CaptureLedger(root, manifest?.sessionId)
        val accepted = manifest != null && try {
            ledger.recordChoice(continueResearch)
        } catch (_: IOException) {
            false
        }
        if (manifest != null && accepted) {
            CaptureStore.upload(
                UploadProgress.of(
                    manifest,
                    ledger,
                    CaptureApis.current.isTestServer,
                    wifiOnly = CapturePreferences.wifiOnly(app)
                )
            )
            CaptureUploads.schedule(app, manifest.sessionId)
        }
        return accepted
    }

    /** The choice carried by a Stop intent, or null for a Stop without one. */
    internal fun stopChoice(hasChoice: Boolean, continueResearch: Boolean): Boolean? =
        if (hasChoice) continueResearch else null
}
