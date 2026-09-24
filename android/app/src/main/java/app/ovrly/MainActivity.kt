package app.ovrly

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Resources
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.animation.PathInterpolator
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.SideEffect
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.splashscreen.SplashScreenViewProvider
import androidx.core.view.WindowCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureService
import app.ovrly.capture.CaptureState
import app.ovrly.capture.CaptureStore
import app.ovrly.capture.PermissionStep
import app.ovrly.capture.nextCapturePermission
import app.ovrly.overlay.OverlayService
import app.ovrly.overlay.OverlayStore
import app.ovrly.ui.CompanionScreen
import app.ovrly.ui.GalleryScreen
import app.ovrly.ui.OvrlyTheme
import app.ovrly.ui.AppearanceStore
import app.ovrly.ui.AppDestination
import app.ovrly.ui.AppShell
import app.ovrly.overlay.DemoEntry
import app.ovrly.overlay.demoEntry
import app.ovrly.voice.VoiceController
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

class MainActivity : ComponentActivity() {
    private val model: CompanionViewModel by viewModels()
    private lateinit var voice: VoiceController
    private var gallery by mutableStateOf(false)
    private var revealed by mutableStateOf(false)
    private var destination by mutableStateOf(AppDestination.SPACE)
    private var setup by mutableStateOf(false)
    private var overlayAllowed by mutableStateOf(false)
    private var notificationsAllowed by mutableStateOf(true)
    private var confirmDemo by mutableStateOf(false)
    private var openingDemo = false

    private val overlaySettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        overlayAllowed = Settings.canDrawOverlays(this)
        CaptureStore.message(if (overlayAllowed) "Overlay permission granted. Tap Show overlay to activate it manually."
            else "Overlay permission was not granted. Capture is still available from the companion.")
    }
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        notificationsAllowed = granted
        if (!granted) CaptureStore.message("Notifications are off. Stop capture from the companion or overlay; Android may show its own capture indicator.")
    }
    private val playbackPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) requestProjection() else {
            CaptureStore.message("Playback-audio permission denied. Nothing was captured; there is no microphone fallback.")
        }
    }
    private val voicePermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && !model.capture.value.busy && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
            closeDemoBeforeRealSession()
            voice.start()
        } else if (!granted) {
            CaptureStore.message("Microphone permission denied. Voice control did not start.")
        }
    }
    private val projectionConsent = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val consent = result.data
        if (result.resultCode != Activity.RESULT_OK || consent == null) {
            CaptureStore.message("Screen capture consent canceled. No media access was started.")
        } else if (!model.capture.value.busy && !model.storageBusy.value) {
            closeDemoBeforeRealSession()
            voice.stop("Voice stopped before playback capture.")
            CaptureStore.set(CaptureState(phase = CapturePhase.STARTING, message = "Starting your selected capture interval..."))
            try {
                ContextCompat.startForegroundService(this,
                    Intent(this, CaptureService::class.java).setAction(CaptureService.START)
                        .putExtra(CaptureService.CONSENT, consent)
                        .putExtra(CaptureService.RESULT_CODE, result.resultCode))
            } catch (error: IllegalStateException) {
                serviceError("Android did not allow capture to start. Keep the companion visible and request fresh consent.", error)
            } catch (error: SecurityException) {
                serviceError("Capture permission changed. Request fresh consent.", error)
            }
        } else {
            CaptureStore.message("Capture or local storage is busy. No new session started; request fresh consent after it finishes.")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen().setOnExitAnimationListener(::dismissSplash)
        AppearanceStore.load(this)
        registerSplashTheme(AppearanceStore.dark.value)
        setTheme(if (AppearanceStore.dark.value) R.style.Theme_Ovrly_Dark else R.style.Theme_Ovrly)
        super.onCreate(savedInstanceState)
        // A recreated Activity (rotation, process restore) has no splash to hand off from.
        revealed = savedInstanceState != null
        gallery = savedInstanceState?.getBoolean("gallery") ?: false
        setup = savedInstanceState?.getBoolean("setup") ?: false
        destination = AppDestination.entries.firstOrNull { it.name == savedInstanceState?.getString("destination") }
            ?: AppDestination.SPACE
        voice = VoiceController(this) { gallery = true }
        enableEdgeToEdge()
        handleIntent(intent, initial = savedInstanceState == null)
        setContent {
            val capture by model.capture.collectAsStateWithLifecycle()
            val share by model.sharedInput.collectAsStateWithLifecycle()
            val storageBusy by model.storageBusy.collectAsStateWithLifecycle()
            val overlayVisible by OverlayStore.visible.collectAsStateWithLifecycle()
            val higherOpacity by OverlayStore.higherOpacity.collectAsStateWithLifecycle()
            val voiceState by voice.state.collectAsStateWithLifecycle()
            val dark by AppearanceStore.dark.collectAsStateWithLifecycle()
            val demo by OverlayStore.demo.collectAsStateWithLifecycle()
            val blur by OverlayStore.blur.collectAsStateWithLifecycle()
            SideEffect {
                WindowCompat.getInsetsController(window, window.decorView).apply {
                    isAppearanceLightStatusBars = !dark
                    isAppearanceLightNavigationBars = !dark
                }
            }
            OvrlyTheme(dark) {
                // Cold-start handoff: content rises and fades in on the same clock as the splash exit.
                val reveal by animateFloatAsState(
                    targetValue = if (revealed) 1f else 0f,
                    animationSpec = tween(SPLASH_HANDOFF_MILLIS, easing = EmphasizedDecelerate),
                    label = "splashHandoff",
                )
                LaunchedEffect(Unit) {
                    // Safety net: if the platform never reports a splash exit, show content anyway.
                    delay(SPLASH_HANDOFF_TIMEOUT_MILLIS)
                    revealed = true
                }
                val rise = with(LocalDensity.current) { 24.dp.toPx() }
                Box(Modifier.fillMaxSize().graphicsLayer {
                    alpha = reveal
                    translationY = (1f - reveal) * rise
                }) {
                BackHandler(enabled = gallery) { gallery = false }
                if (gallery) {
                    GalleryScreen(onBack = { gallery = false }, onDemo = ::requestDemo)
                } else {
                    AppShell(
                        destination = destination,
                        onDestination = { destination = it },
                        activeSession = when {
                            capture.busy -> "Capture active"
                            voiceState.active -> "Microphone active"
                            else -> null
                        },
                    ) {
                    CompanionScreen(
                        capture = capture, share = share, storageBusy = storageBusy,
                        overlayAllowed = overlayAllowed, overlayVisible = overlayVisible,
                        notificationsAllowed = notificationsAllowed, higherOpacity = higherOpacity,
                        voiceActive = voiceState.active, voiceMessage = voiceState.message,
                        voiceConfigured = BuildConfig.VOXIDE_ENABLED && BuildConfig.VOXIDE_PUBLISHABLE_KEY.isNotBlank(),
                        showSetup = setup,
                        onDismissSetup = { setup = false },
                        onConfirmSetup = { setup = false; beginCaptureSetup() },
                        onStart = { setup = true },
                        onStop = { startService(Intent(this@MainActivity, CaptureService::class.java).setAction(CaptureService.STOP)) },
                        onOverlayPermission = {
                            overlaySettings.launch(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, "package:$packageName".toUri()))
                        },
                        onShowOverlay = { showOverlay(OverlayService.SHOW) },
                        onHideOverlay = ::hideOverlay,
                        onResetOverlay = { showOverlay(OverlayService.RESET) },
                        onOpacity = model::higherOpacity,
                        onDeleteCapture = model::deleteCapture,
                        onClearShare = model::clearShare,
                        onGallery = { gallery = true },
                        onNotificationPermission = {
                            if (Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                        },
                        onVoiceStart = {
                            if (!capture.busy) {
                                closeDemoBeforeRealSession()
                                if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) voice.start()
                                else voicePermission.launch(Manifest.permission.RECORD_AUDIO)
                            }
                        },
                        onVoiceStop = { voice.stop("Voice stopped by you.") },
                        dark = dark,
                        onDark = { AppearanceStore.setDark(this@MainActivity, it); registerSplashTheme(it) },
                        overlayStatus = (if (demo) "Demo / " else "") + blur.label,
                        demoActive = demo,
                        onDemo = ::requestDemo,
                    )
                    }
                }
                if (confirmDemo) {
                    AlertDialog(
                        onDismissRequest = { confirmDemo = false },
                        title = { Text("Stop your session and open the demo?") },
                        text = { Text("This stops real capture and companion voice before showing simulated claims. " +
                            "The demo does not record, upload or check evidence. Nothing restarts when it closes.") },
                        confirmButton = {
                            TextButton(onClick = { confirmDemo = false; openDemo(stopSessions = true) }) {
                                Text("Stop session and open demo")
                            }
                        },
                        dismissButton = { TextButton(onClick = { confirmDemo = false }) { Text("Keep my session") } },
                    )
                }
                }
            }
        }
    }

    /**
     * Runs once the first app frame exists, so it never delays content. The ring lifts and fades
     * while the splash surface dissolves; [revealed] starts the matching content rise underneath
     * on the same clock. Durations follow the system animator scale, so reduced-motion settings
     * collapse the whole handoff to an instant swap.
     */
    private fun dismissSplash(splash: SplashScreenViewProvider) {
        revealed = true
        val ease = PathInterpolator(0.05f, 0.7f, 0.1f, 1f) // Material emphasized decelerate
        splash.iconView.animate().scaleX(1.3f).scaleY(1.3f).alpha(0f)
            .setDuration(SPLASH_HANDOFF_MILLIS.toLong()).setInterpolator(ease).start()
        splash.view.animate().alpha(0f)
            .setStartDelay(SPLASH_SURFACE_DELAY_MILLIS).setDuration(SPLASH_HANDOFF_MILLIS - SPLASH_SURFACE_DELAY_MILLIS)
            .setInterpolator(ease).withEndAction(splash::remove).start()
    }

    /**
     * Android 12+ stores a splash theme per app for future launches, which is the only way the
     * system-drawn splash can follow the saved appearance. Dark is the manifest default, so it
     * clears the override; Android 10/11 always show the Chrome-black compat splash.
     */
    private fun registerSplashTheme(dark: Boolean) {
        if (Build.VERSION.SDK_INT >= 31) {
            splashScreen.setSplashScreenTheme(if (dark) Resources.ID_NULL else R.style.Theme_Ovrly_Starting_Light)
        }
    }

    private fun beginCaptureSetup() {
        if (model.storageBusy.value) return
        closeDemoBeforeRealSession()
        voice.stop("Voice stopped before capture setup.")
        when (nextCapturePermission(
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED,
            model.capture.value.busy,
        )) {
            PermissionStep.AUDIO -> playbackPermission.launch(Manifest.permission.RECORD_AUDIO)
            PermissionStep.PROJECTION -> requestProjection()
            PermissionStep.ALREADY_RUNNING -> CaptureStore.message("A capture is already running. Stop it before starting another.")
        }
    }

    private fun requestDemo() {
        gallery = false
        destination = AppDestination.SETTINGS
        if (openingDemo) return
        if (!Settings.canDrawOverlays(this)) {
            CaptureStore.message("Allow overlay access, then tap Open demo overlay again.")
            overlaySettings.launch(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, "package:$packageName".toUri()))
            return
        }
        when (demoEntry(model.capture.value.busy, voice.state.value.active, model.storageBusy.value)) {
            DemoEntry.WAIT_FOR_STORAGE -> CaptureStore.message("Wait for local storage before opening the demo.")
            DemoEntry.CONFIRM_STOP -> confirmDemo = true
            DemoEntry.READY -> openDemo(stopSessions = false)
        }
    }

    private fun openDemo(stopSessions: Boolean) {
        if (openingDemo) return
        openingDemo = true
        lifecycleScope.launch {
            try {
                val stoppingCapture = stopSessions && model.capture.value.busy
                if (stopSessions) {
                    voice.stop("Voice stopped with your confirmation before opening the demo.")
                    if (model.capture.value.busy) {
                        startService(Intent(this@MainActivity, CaptureService::class.java).setAction(CaptureService.STOP))
                    }
                }
                val stopped = withTimeoutOrNull(5000) { model.capture.first { !it.busy } }
                if (stopped == null || voice.state.value.active || model.storageBusy.value) {
                    CaptureStore.message("The session has not finished stopping. Demo was not opened; try again when idle.")
                } else if (stoppingCapture && stopped.phase == CapturePhase.ERROR) {
                    CaptureStore.message("Capture reported a cleanup issue. Demo was not opened. Check local capture before retrying.")
                } else if (!lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) {
                    CaptureStore.message("Demo was not opened because the companion left the foreground. Tap Try demo again.")
                } else {
                    setup = false
                    showOverlay(OverlayService.SHOW_DEMO)
                }
            } catch (error: IllegalStateException) {
                serviceError("Android could not stop the session or open the demo. Try again from the companion.", error)
            } catch (error: SecurityException) {
                serviceError("Permission changed before the demo could open. Check overlay access and try again.", error)
            } finally {
                openingDemo = false
            }
        }
    }

    private fun closeDemoBeforeRealSession() {
        if (OverlayStore.demo.value) hideOverlay()
    }

    private fun requestProjection() {
        if (!model.capture.value.busy) {
            projectionConsent.launch(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent())
        }
    }

    private fun showOverlay(action: String) {
        try {
            ContextCompat.startForegroundService(this, Intent(this, OverlayService::class.java).setAction(action))
        } catch (error: IllegalStateException) {
            serviceError("Android did not allow the overlay to start. Try again while the companion is visible.", error)
        } catch (error: SecurityException) {
            serviceError("Overlay permission changed. Enable it again in Android settings.", error)
        }
    }

    // Android stops services by component, not Intent object identity (lint false positive).
    @SuppressLint("ImplicitSamInstance")
    private fun hideOverlay() {
        stopService(Intent(this, OverlayService::class.java))
    }

    private fun serviceError(message: String, error: RuntimeException) {
        Log.e("OvrlyCompanion", message, error)
        CaptureStore.update { it.copy(phase = if (it.phase == CapturePhase.STARTING) CapturePhase.ERROR else it.phase, message = message) }
    }

    override fun onResume() {
        super.onResume()
        model.checkRetention()
        overlayAllowed = Settings.canDrawOverlays(this)
        notificationsAllowed = Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
    }

    override fun onStop() {
        voice.stop("Voice stopped because the companion left the foreground.")
        super.onStop()
    }

    override fun onDestroy() {
        voice.close()
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent, initial = true)
    }

    private fun handleIntent(intent: Intent, initial: Boolean) {
        if (!initial) return
        when (intent.action) {
            ACTION_SETUP -> {
                gallery = false
                destination = AppDestination.SETTINGS
                setup = !model.capture.value.busy
            }
            ACTION_DETAILS -> { gallery = false; destination = AppDestination.SETTINGS }
            Intent.ACTION_SEND -> {
                gallery = false
                destination = AppDestination.SETTINGS
                model.acceptShare(intent)
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("gallery", gallery)
        outState.putBoolean("setup", setup)
        outState.putString("destination", destination.name)
        super.onSaveInstanceState(outState)
    }

    companion object {
        const val ACTION_SETUP = "app.ovrly.OPEN_SETUP"
        const val ACTION_DETAILS = "app.ovrly.OPEN_DETAILS"
        private const val SPLASH_HANDOFF_MILLIS = 900
        private const val SPLASH_SURFACE_DELAY_MILLIS = 150L
        private const val SPLASH_HANDOFF_TIMEOUT_MILLIS = 2500L
        private val EmphasizedDecelerate = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
    }
}
