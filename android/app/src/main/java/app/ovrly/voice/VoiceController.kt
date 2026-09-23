package app.ovrly.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import app.ovrly.BuildConfig
import kotlinx.coroutines.flow.StateFlow

/**
 * Foreground-only, explicit-start voice. The host must stop on Activity.onStop and
 * before starting playback capture. No permissions or services are started here.
 *
 * VOXIDE_ENABLED is an explicit experimental opt-in, not a claim of native support.
 * See https://voxide.app/docs/frameworks for the provider's browser-only contract.
 */
class VoiceController(context: Context, onOpenGallery: () -> Unit) : AutoCloseable {
    private val applicationContext = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val diagnostics = VoiceDiagnostics.android()
    private val configuration = VoiceConfiguration(
        BuildConfig.VOXIDE_ENABLED,
        BuildConfig.VOXIDE_BASE_URL,
        BuildConfig.VOXIDE_PUBLISHABLE_KEY,
    )
    private val session = VoiceSession(
        configuration = configuration,
        transportFactory = { VoxideTransport(configuration, diagnostics = diagnostics) },
        audioFactory = { AndroidVoiceAudio(applicationContext, diagnostics) },
        permissionGranted = {
            applicationContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        },
        dispatch = { action ->
            if (Looper.myLooper() == Looper.getMainLooper()) action() else main.post { action() }
        },
        schedule = { milliseconds, action ->
            val runnable = Runnable { action() }
            main.postDelayed(runnable, milliseconds)
            VoiceCancellation { main.removeCallbacks(runnable) }
        },
        openGallery = onOpenGallery,
        diagnostics = diagnostics,
    )

    val state: StateFlow<VoiceState> = session.state

    fun start() = session.start()

    fun stop(reason: String = "Voice stopped.") = session.stop(reason)

    override fun close() = session.close()
}
