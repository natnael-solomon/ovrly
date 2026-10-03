package app.ovrly.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import app.ovrly.BuildConfig
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Every build without the explicit live opt-in, debug or release, runs the in-process
 * simulation: no microphone, network, credentials or provider sessions.
 */
internal fun voiceSimulated(live: Boolean): Boolean = !live

/**
 * Foreground-only, explicit-start voice. The host must stop on Activity.onStop and
 * before starting playback capture. No permissions or services are started here.
 *
 * VOXIDE_ENABLED is an explicit experimental opt-in, not a claim of native support.
 * See https://voxide.app/docs/frameworks for the provider's browser-only contract.
 */
class VoiceController(context: Context, navigator: VoiceNavigator) : AutoCloseable {
    private val applicationContext = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val diagnostics = VoiceDiagnostics.android()
    private val configuration = VoiceConfiguration(
        BuildConfig.VOXIDE_ENABLED,
        BuildConfig.VOXIDE_BASE_URL,
        BuildConfig.VOXIDE_PUBLISHABLE_KEY,
        mock = voiceSimulated(BuildConfig.VOXIDE_LIVE)
    )
    val requiresMicrophone: Boolean = !configuration.mock
    val configured: Boolean = configuration.unavailableState() == null
    private val session = VoiceSession(
        configuration = configuration,
        transportFactory = {
            if (configuration.mock) {
                MockVoiceTransport()
            } else {
                VoxideTransport(configuration, diagnostics = diagnostics)
            }
        },
        audioFactory = {
            if (configuration.mock) {
                MockVoiceAudio()
            } else {
                AndroidVoiceAudio(applicationContext, diagnostics)
            }
        },
        permissionGranted = {
            !requiresMicrophone ||
                applicationContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        },
        dispatch = { action ->
            // Always post: bounded inbox batches must yield to rendering and Stop callbacks.
            main.post { action() }
        },
        schedule = { milliseconds, action ->
            val runnable = Runnable { action() }
            main.postDelayed(runnable, milliseconds)
            VoiceCancellation { main.removeCallbacks(runnable) }
        },
        navigator = navigator,
        diagnostics = diagnostics,
        presentation = VoicePresentation(SystemClock::elapsedRealtime)
    )

    val state: StateFlow<VoiceState> = session.state

    /** Orb display level 0..1, at most 20 updates per second; 0 when not listening or speaking. */
    val level: StateFlow<Float> = session.level

    /** Lossy presentation cues; collectors that fall behind lose the oldest events. */
    val events: SharedFlow<VoiceOrbEvent> = session.events

    fun start() = session.start()

    fun holdStart() = session.holdStart()

    fun holdEnd() = session.holdEnd()

    fun interrupt() = session.interrupt()

    fun stop(reason: String = "Voice stopped.") = session.stop(reason)

    override fun close() = session.close()
}
