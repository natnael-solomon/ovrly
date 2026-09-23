package app.ovrly.overlay

import android.annotation.SuppressLint
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.WindowInsets
import android.widget.FrameLayout
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import app.ovrly.AppNotifications
import app.ovrly.MainActivity
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureService
import app.ovrly.capture.CaptureStore
import app.ovrly.ui.GlassOverlay
import app.ovrly.ui.OverlayAppearance
import app.ovrly.ui.OverlayVisual
import app.ovrly.ui.AppearanceStore
import app.ovrly.ui.DemoOverlayPanel
import app.ovrly.ui.LocalWindowBlur
import app.ovrly.ui.OvrlyTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.combine
import kotlin.math.abs
import kotlin.math.roundToInt

object OverlayStore {
    private val mutable = MutableStateFlow(false)
    val visible = mutable.asStateFlow()
    internal fun visible(value: Boolean) { mutable.value = value }
    val higherOpacity = MutableStateFlow(OverlayAppearance.DEFAULT_HIGHER_OPACITY)
    private val mutableDemo = MutableStateFlow(false)
    val demo = mutableDemo.asStateFlow()
    internal fun demo(value: Boolean) { mutableDemo.value = value }
    internal val blur = MutableStateFlow(BlurMode.FALLBACK)
}

class OverlayService : LifecycleService(), SavedStateRegistryOwner, ViewModelStoreOwner {
    private val saved = SavedStateRegistryController.create(this)
    override val savedStateRegistry get() = saved.savedStateRegistry
    override val viewModelStore = ViewModelStore()
    private var root: DragSurface? = null
    private var compose: ComposeView? = null
    private var overlayWindow: OverlayWindow? = null
    private lateinit var wm: WindowManager
    private var usableSize by mutableStateOf(IntSize(1, 1))
    private var demoDragHeight = 0
    private var compactPosition = 24 to 180
    private var pendingCompactPosition: Pair<Int, Int>? = null
    // Drag coordinates are physical screen coordinates, independent of text direction.
    @SuppressLint("RtlHardcoded")
    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.LEFT
        x = 24
        y = 180
        title = "ovrly capture controls"
    }

    override fun onCreate() {
        saved.performAttach()
        saved.performRestore(null)
        super.onCreate()
        wm = getSystemService(WindowManager::class.java)
        if (Build.VERSION.SDK_INT >= 30) {
            params.setFitInsetsTypes(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
        }
        usableSize = availableWindowSize()
        AppearanceStore.load(this)
        lifecycleScope.launch {
            combine(AppearanceStore.dark, OverlayStore.higherOpacity) { dark, opaque -> dark to opaque }
                .collect { (dark, opaque) -> overlayWindow?.appearance(dark, opaque) }
        }
        lifecycleScope.launch {
            CaptureStore.state.collect { capture ->
                if (capture.busy && OverlayStore.demo.value) {
                    CaptureStore.message("Demo closed because a real capture started.")
                    stopSelf()
                }
            }
        }
    }

    override fun onBind(intent: Intent): IBinder? {
        super.onBind(intent)
        return null
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == HIDE || intent == null) {
            stopSelf()
            return Service.START_NOT_STICKY
        }
        if (!Settings.canDrawOverlays(this)) {
            CaptureStore.message("Overlay permission is off. Enable it in Android settings; capture consent is separate.")
            stopSelf()
            return Service.START_NOT_STICKY
        }
        val demo = intent.action == SHOW_DEMO
        if (demo && CaptureStore.state.value.busy) {
            CaptureStore.message("Stop capture with confirmation before opening the demo.")
            if (root == null) stopSelf()
            return Service.START_NOT_STICKY
        }
        try {
            val modeChanged = intent.action != RESET && OverlayStore.demo.value != demo
            if (modeChanged && demo) compactPosition = params.x to params.y
            if (modeChanged && !demo) pendingCompactPosition = compactPosition
            if (intent.action != RESET) OverlayStore.demo(demo)
            ServiceCompat.startForeground(this, 102,
                AppNotifications.build(this,
                    if (OverlayStore.demo.value) "ovrly demo / simulated content" else "ovrly overlay is visible",
                    if (OverlayStore.demo.value) "No recording, research or microphone. Tap Close demo to dismiss."
                    else "Idle overlay does not record. Hiding does not stop an active capture.",
                    HIDE, OverlayService::class.java,
                    actionLabel = if (OverlayStore.demo.value) "Close demo" else "Hide overlay (capture continues)"),
                if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0)
            if (root == null) attachOverlay()
            if (intent.action == RESET && !OverlayStore.demo.value) compactPosition = 24 to 180
            configureWindow(resetPosition = modeChanged || intent.action == RESET)
        } catch (error: SecurityException) {
            overlayError(error)
        } catch (error: WindowManager.BadTokenException) {
            overlayError(error)
        } catch (error: IllegalStateException) {
            overlayError(error)
        }
        return Service.START_NOT_STICKY
    }

    private fun attachOverlay() {
        val frame = DragSurface(this, onMove = { x, y -> moveTo(x, y) },
            currentPosition = { params.x to params.y },
            canDrag = { y -> !OverlayStore.demo.value || y < demoDragHeight })
        frame.setViewTreeLifecycleOwner(this)
        frame.setViewTreeSavedStateRegistryOwner(this)
        frame.setViewTreeViewModelStoreOwner(this)
        val content = ComposeView(this)
        compose = content
        content.setContent {
            val capture by CaptureStore.state.collectAsState()
            val opaque by OverlayStore.higherOpacity.collectAsState()
            val dark by AppearanceStore.dark.collectAsState()
            val demo by OverlayStore.demo.collectAsState()
            val blur by OverlayStore.blur.collectAsState()
            val visual = when (capture.phase) {
                CapturePhase.RECORDING -> OverlayVisual.Recording(capture.seconds)
                CapturePhase.FINISHED, CapturePhase.ERROR -> OverlayVisual.Captured
                else -> OverlayVisual.Idle
            }
            OvrlyTheme(dark) {
                CompositionLocalProvider(LocalWindowBlur provides (blur == BlurMode.NATIVE)) {
                    val density = resources.displayMetrics.density
                    val geometry = demoPanelGeometry(usableSize.width, usableSize.height, density)
                    val maxWidth = (usableSize.width / density - 24).coerceAtLeast(48f)
                    val moving = Modifier.widthIn(max = maxWidth.dp).semantics {
                        customActions = listOf(
                            CustomAccessibilityAction("Move overlay left") { moveBy(-48, 0); true },
                            CustomAccessibilityAction("Move overlay right") { moveBy(48, 0); true },
                            CustomAccessibilityAction("Move overlay up") { moveBy(0, -48); true },
                            CustomAccessibilityAction("Move overlay down") { moveBy(0, 48); true },
                        )
                    }
                    if (demo) {
                        DemoOverlayPanel(
                            blurLabel = blur.label,
                            onClose = { stopSelf() },
                            modifier = Modifier.semantics {
                                customActions = listOf(
                                    CustomAccessibilityAction("Move demo up") { moveBy(0, -48); true },
                                    CustomAccessibilityAction("Move demo down") { moveBy(0, 48); true },
                                )
                            },
                            panelWidth = (geometry.width / density).dp,
                            maxHeight = (geometry.height / density).dp,
                            onHeaderHeight = { demoDragHeight = it },
                        )
                    } else GlassOverlay(
                        state = visual, higherOpacity = opaque,
                        onSetup = { openCompanion(MainActivity.ACTION_SETUP) },
                        onStopCapture = { startService(Intent(this@OverlayService, CaptureService::class.java).setAction(CaptureService.STOP)) },
                        onCancelResearch = { CaptureStore.message("Research is not connected; there is no research task to cancel.") },
                        onDetails = { openCompanion(MainActivity.ACTION_DETAILS) },
                        modifier = moving,
                    )
                }
            }
        }
        frame.addView(content)
        root = frame
        frame.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            usableSize = availableWindowSize()
            pendingCompactPosition?.let { (x, y) ->
                params.x = x
                params.y = y
                pendingCompactPosition = null
            }
            configureWindow()
        }
        configureWindow(resetPosition = true)
        val host = OverlayWindow(this, wm, params) { OverlayStore.blur.value = it }
        overlayWindow = host
        host.appearance(AppearanceStore.dark.value, OverlayStore.higherOpacity.value)
        host.show(frame)
        OverlayStore.visible(true)
        lifecycleScope.launch {
            while (root != null) {
                delay(1000)
                if (!Settings.canDrawOverlays(this@OverlayService)) {
                    CaptureStore.message("Overlay permission was revoked. Any capture continues until stopped in the companion or notification.")
                    stopSelf()
                    break
                }
            }
        }
    }

    private fun openCompanion(action: String) {
        startActivity(Intent(this, MainActivity::class.java).setAction(action)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
    }

    private fun moveBy(xDp: Int, yDp: Int) {
        val density = resources.displayMetrics.density
        moveTo(params.x + (xDp * density).roundToInt(), params.y + (yDp * density).roundToInt())
    }

    private fun moveTo(x: Int, y: Int) {
        val view = root
        val demo = OverlayStore.demo.value
        val geometry = demoPanelGeometry(usableSize.width, usableSize.height, resources.displayMetrics.density)
        val margin = if (demo) geometry.margin else 0
        val width = if (demo) geometry.width else view?.width ?: 0
        val height = if (demo) geometry.height else view?.height ?: 0
        val newX = if (demo) margin else x.coerceIn(0, (usableSize.width - width).coerceAtLeast(0))
        val newY = y.coerceIn(margin, (usableSize.height - height - margin).coerceAtLeast(margin))
        params.x = newX
        params.y = newY
        if (view?.isAttachedToWindow == true) {
            try { overlayWindow?.layout(params.width, params.height, newX, newY) }
            catch (error: SecurityException) { overlayError(error) }
            catch (error: IllegalArgumentException) { overlayError(error) }
        }
    }

    private fun configureWindow(resetPosition: Boolean = false) {
        val demo = OverlayStore.demo.value
        val geometry = demoPanelGeometry(usableSize.width, usableSize.height, resources.displayMetrics.density)
        params.width = if (demo) geometry.width else WindowManager.LayoutParams.WRAP_CONTENT
        if (resetPosition) {
            params.x = if (demo) geometry.margin else compactPosition.first
            params.y = if (demo) geometry.bottomY(usableSize.height) else compactPosition.second
        }
        moveTo(params.x, params.y)
    }

    private fun availableWindowSize(): IntSize {
        if (Build.VERSION.SDK_INT >= 30) {
            val metrics = wm.currentWindowMetrics
            val insets = metrics.windowInsets.getInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout(),
            )
            return IntSize(
                (metrics.bounds.width() - insets.left - insets.right).coerceAtLeast(1),
                (metrics.bounds.height() - insets.top - insets.bottom).coerceAtLeast(1),
            )
        }
        val frame = android.graphics.Rect()
        root?.getWindowVisibleDisplayFrame(frame)
        return if (!frame.isEmpty) IntSize(frame.width(), frame.height()) else {
            val metrics = resources.displayMetrics
            IntSize(metrics.widthPixels, metrics.heightPixels)
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        usableSize = availableWindowSize()
        root?.post { configureWindow(resetPosition = true) }
    }

    private fun overlayError(error: RuntimeException) {
        Log.e("OvrlyOverlay", "Overlay unavailable", error)
        CaptureStore.message("The overlay could not be displayed. Check overlay permission. Capture can still be stopped in the companion or notification.")
        stopSelf()
    }

    override fun onDestroy() {
        compose?.disposeComposition()
        overlayWindow?.close()
        overlayWindow = null
        root = null
        compose = null
        viewModelStore.clear()
        OverlayStore.visible(false)
        OverlayStore.demo(false)
        OverlayStore.blur.value = BlurMode.FALLBACK
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    companion object {
        const val SHOW = "show"
        const val SHOW_DEMO = "show_demo"
        const val HIDE = "hide"
        const val RESET = "reset_position"
    }
}

// Constructed only by the service. Compose children own taps and accessibility actions.
@SuppressLint("ViewConstructor", "ClickableViewAccessibility")
private class DragSurface(
    context: Context,
    private val onMove: (Int, Int) -> Unit,
    private val currentPosition: () -> Pair<Int, Int>,
    private val canDrag: (Float) -> Boolean,
) : FrameLayout(context) {
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var downX = 0f
    private var downY = 0f
    private var origin = 0 to 0
    private var dragging = false
    private var dragAllowed = false

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.rawX
                downY = event.rawY
                origin = currentPosition()
                dragging = false
                dragAllowed = canDrag(event.y)
            }
            MotionEvent.ACTION_MOVE -> {
                if (!dragAllowed) return false
                dragging = dragging || abs(event.rawX - downX) > slop || abs(event.rawY - downY) > slop
                return dragging
            }
            MotionEvent.ACTION_CANCEL, MotionEvent.ACTION_UP -> dragging = false
        }
        return false
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        // Non-clickable header space must own DOWN to receive the rest of a drag.
        if (!dragAllowed) return super.onTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_MOVE) {
            dragging = dragging || abs(event.rawX - downX) > slop || abs(event.rawY - downY) > slop
            if (dragging) onMove(
                origin.first + (event.rawX - downX).roundToInt(),
                origin.second + (event.rawY - downY).roundToInt(),
            )
        }
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) {
            dragging = false
            dragAllowed = false
        }
        return true
    }
}
