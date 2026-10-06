package app.ovrly.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import app.ovrly.BuildConfig
import app.ovrly.contract.Investigation
import app.ovrly.data.ApiResult
import app.ovrly.data.ApiServices
import app.ovrly.data.VoiceApi
import app.ovrly.data.cachedInvestigation
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

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

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val services = ApiServices.get(applicationContext)
    private val targets = services?.let { VoiceTargets(it.jobs) }
    private val opened = MutableStateFlow<String?>(null)

    @Volatile
    private var checksState = JSONObject()

    /** Approval for `queue_cancel`, shown on screen and bound to one request and target. */
    internal val confirmations = VoiceConfirmations()

    /** The check the last accepted `open_check` opened; #34 routes this to its report screen. */
    internal val openedCheck: StateFlow<String?> = opened.asStateFlow()

    private val effects = object : VoiceEffects {
        override fun openCheck(investigationId: String) {
            opened.value = investigationId
        }

        override suspend fun refreshChecks() {
            services?.reconciler?.reconcile()
        }

        override suspend fun finished() = this@VoiceController.refreshChecks()
    }

    /** Runs a command, then refreshes the `checks` state before the assistant is told. */
    private val executor: VoiceCommandExecutor = services?.let { api ->
        VoiceBackend(
            VoiceApi(api.api),
            checkNotNull(targets),
            confirmations,
            effects,
            scope
        )
    } ?: VoiceCommandExecutor.UNAVAILABLE

    init {
        session.commands = VoiceCommands(executor, { checksState }, confirmations::discardAll)
    }

    val state: StateFlow<VoiceState> = session.state

    /** Recognized user text and the last action result, shown on screen only. */
    internal val activity: StateFlow<VoiceActivity> = session.activity

    /** Orb display level 0..1, at most 20 updates per second; 0 when not listening or speaking. */
    val level: StateFlow<Float> = session.level

    /** Lossy presentation cues; collectors that fall behind lose the oldest events. */
    val events: SharedFlow<VoiceOrbEvent> = session.events

    fun start() {
        scope.launch { refreshChecks() }
        session.start()
    }

    fun holdStart() {
        scope.launch { refreshChecks() }
        session.holdStart()
    }

    fun holdEnd() = session.holdEnd()

    fun interrupt() = session.interrupt()

    fun stop(reason: String = "Voice stopped.") {
        confirmations.discardAll()
        session.stop(reason)
    }

    /**
     * The typed alternative: the same allowlisted commands without a voice session, so a
     * denied microphone, a disconnect or an unsupported command never leaves the user stuck.
     * It never starts or reconnects voice and costs no provider session.
     */
    internal fun typed(text: String) {
        val command = VoiceCommandContract.parseTyped(text)
        if (command == null) {
            session.activityLog.result(
                VoiceResult("Typed command", VoiceCommandContract.TYPED_HINT, false, typed = true)
            )
            return
        }
        val label = command.action.label
        val sending = VoiceResult(label, "Sending...", true, typed = true, pending = true)
        session.activityLog.result(sending)
        executor.execute(command) { outcome ->
            val result = VoiceResult(label, outcome.message, outcome.success, true, outcome.code)
            session.activityLog.result(result)
        }
    }

    /** Clears the panel: recognized text, the last result and the opened check. */
    internal fun clearActivity() {
        opened.value = null
        session.activityLog.clear()
    }

    /** The opened check from the server, or its last stored read when the server is away. */
    internal suspend fun check(id: String): Investigation? {
        val api = services ?: return null
        return when (val result = api.investigations.refresh(id)) {
            is ApiResult.Success -> result.value
            is ApiResult.Failure -> api.jobs.cachedInvestigation(id)
        }
    }

    private suspend fun refreshChecks() {
        targets?.recent()?.let { checksState = VoiceTargets.state(it) }
    }

    override fun close() {
        confirmations.discardAll()
        session.close()
        scope.cancel()
    }
}
