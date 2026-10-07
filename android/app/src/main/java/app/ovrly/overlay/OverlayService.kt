package app.ovrly.overlay

import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityManager.TouchExplorationStateChangeListener
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import app.ovrly.AppNotifications
import app.ovrly.BuildConfig
import app.ovrly.MainActivity
import app.ovrly.capture.CapturePhase
import app.ovrly.capture.CaptureState
import app.ovrly.capture.CaptureStore
import app.ovrly.ui.AppearanceStore
import app.ovrly.ui.DemoOverlayPanel
import app.ovrly.ui.ExaminingState
import app.ovrly.ui.GlassOverlay
import app.ovrly.ui.LiveOverlay
import app.ovrly.ui.LiveOverlayModel
import app.ovrly.ui.LivePanelActions
import app.ovrly.ui.LivePanelFrame
import app.ovrly.ui.LocalWindowBlur
import app.ovrly.ui.OverlayAppearance
import app.ovrly.ui.OverlayVisual
import app.ovrly.ui.OvrlyTheme
import app.ovrly.ui.WindowGlass
import kotlin.math.roundToInt
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

object OverlayStore {
    private val mutable = MutableStateFlow(false)
    val visible = mutable.asStateFlow()
    internal fun visible(value: Boolean) {
        mutable.value = value
    }
    val higherOpacity = MutableStateFlow(OverlayAppearance.DEFAULT_HIGHER_OPACITY)
    private val mutableDemo = MutableStateFlow(false)
    val demo = mutableDemo.asStateFlow()
    internal fun demo(value: Boolean) {
        mutableDemo.value = value
    }
    internal val blur = MutableStateFlow(BlurMode.FALLBACK)

    /**
     * Live results for the overlay. [NotConnectedLiveResultsSource] until the polling
     * adapter over the #18 client is installed; never a fixture.
     */
    internal val liveSource = MutableStateFlow<LiveResultsSource>(NotConnectedLiveResultsSource)

    /** Survives the overlay service, so re-showing the overlay mid-capture never re-expands. */
    internal val autoExpand = AutoExpandMemory()
}

class OverlayService :
    LifecycleService(),
    SavedStateRegistryOwner,
    ViewModelStoreOwner {
    private val saved = SavedStateRegistryController.create(this)
    override val savedStateRegistry get() = saved.savedStateRegistry
    override val viewModelStore = ViewModelStore()
    private var root: OverlayDragSurface? = null
    private var compose: ComposeView? = null
    private var overlayWindow: OverlayWindow? = null
    private lateinit var wm: WindowManager
    private var usableSize by mutableStateOf(IntSize(1, 1))
    private var panelHeaderHeight = 0
    private val fixture = MutableStateFlow<FixtureLiveResultsSource?>(null)
    private var fixtureJob: Job? = null
    private val livePanel = LivePanelController(OverlayStore.autoExpand) { continueResearch ->
        val preview = fixture.value
        if (preview != null) {
            preview.close(continueResearch)
        } else {
            CaptureServiceStopChoice(this).onStopChoice(continueResearch)
        }
    }

    /** The live form on screen; null for the demo and the idle controls. */
    private var form: LiveOverlayForm? = null
    private var compactPosition = 24 to 180
    private var pendingCompactPosition: Pair<Int, Int>? = null

    /** The pill's screen x while the panel is open below it; the window then spans the panel. */
    private val pillX = MutableStateFlow(compactPosition.first)
    private var pillWidth = 0

    /**
     * Where the expanded panel's top edge wants to be: the pill's top when it expanded, or
     * where the user dragged the panel. The panel opens there and moves up only as far as
     * it must to stay on screen.
     */
    private var panelTop = 0
    private var savedJob: Job? = null
    private val touches = MutableStateFlow(0)
    private val touchExploration = MutableStateFlow(false)
    private var explorationListener: TouchExplorationStateChangeListener? = null
    private var dismissTarget: DismissTarget? = null

    // Drag coordinates are physical screen coordinates, independent of text direction.
    @SuppressLint("RtlHardcoded")
    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
        PixelFormat.TRANSLUCENT
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
            params.setFitInsetsTypes(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout()
            )
        }
        usableSize = availableWindowSize()
        AppearanceStore.load(this)
        observeTouchExploration()
        lifecycleScope.launch {
            combine(AppearanceStore.dark, OverlayStore.higherOpacity) { dark, opaque ->
                dark to
                    opaque
            }
                .collect { (dark, opaque) -> overlayWindow?.appearance(dark, opaque) }
        }
        lifecycleScope.launch {
            CaptureStore.state.collect { capture ->
                if (capture.busy && OverlayStore.demo.value) {
                    CaptureStore.message("Demo closed because a real capture started.")
                    stopSelf()
                }
                if (capture.busy) closeFixture()
                livePanel.onCaptureRunning(capture.busy)
            }
        }
        lifecycleScope.launch {
            combine(fixture, OverlayStore.liveSource) { preview, live -> preview ?: live }
                .collectLatest { source ->
                    val key = (source as? PollingLiveResultsSource)?.sessionId
                    source.results.collect { livePanel.onResults(it, key) }
                }
        }
        lifecycleScope.launch {
            runAutoCollapse(livePanel, touchExploration = { touchExploration.value })
        }
    }

    private fun observeTouchExploration() {
        val accessibility = getSystemService(AccessibilityManager::class.java) ?: return
        touchExploration.value = accessibility.isTouchExplorationEnabled
        val listener = TouchExplorationStateChangeListener {
            touchExploration.value = it
        }
        explorationListener = listener
        accessibility.addTouchExplorationStateChangeListener(listener)
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
            CaptureStore.message(
                "Overlay permission is off. Enable it in Android settings; " +
                    "capture consent is separate."
            )
            stopSelf()
            return Service.START_NOT_STICKY
        }
        val demo = intent.action == SHOW_DEMO
        if (demo && CaptureStore.state.value.busy) {
            CaptureStore.message("Stop capture with confirmation before opening the demo.")
            if (root == null) stopSelf()
            return Service.START_NOT_STICKY
        }
        val preview = intent.action == SHOW_LIVE_FIXTURE
        if (preview && (!BuildConfig.DEBUG || CaptureStore.state.value.busy)) {
            if (root == null) stopSelf()
            return Service.START_NOT_STICKY
        }
        when {
            preview -> openFixture()
            intent.action != RESET -> closeFixture()
        }
        try {
            val modeChanged = intent.action != RESET && OverlayStore.demo.value != demo
            if (modeChanged && demo) compactPosition = params.x to params.y
            if (modeChanged && !demo) pendingCompactPosition = compactPosition
            if (intent.action != RESET) OverlayStore.demo(demo)
            startForegroundNotice()
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

    private fun startForegroundNotice() {
        val label = if (OverlayStore.demo.value) "Close demo" else HIDE_LABEL
        val notice = AppNotifications.build(
            this,
            notificationTitle(),
            notificationText(),
            HIDE,
            OverlayService::class.java,
            actionLabel = label
        )
        val type = if (Build.VERSION.SDK_INT >=
            34
        ) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        ServiceCompat.startForeground(this, 102, notice, type)
    }

    private fun attachOverlay() {
        val frame = OverlayDragSurface(this, dragCallbacks())
        frame.setViewTreeLifecycleOwner(this)
        frame.setViewTreeSavedStateRegistryOwner(this)
        frame.setViewTreeViewModelStoreOwner(this)
        val content = ComposeView(this)
        compose = content
        content.setContent { OverlayContent() }
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
                    CaptureStore.message(
                        "Overlay permission was revoked. Any capture continues until " +
                            "stopped in the companion or notification."
                    )
                    stopSelf()
                    break
                }
            }
        }
    }

    @Composable
    private fun OverlayContent() {
        val capture by CaptureStore.state.collectAsState()
        val opaque by OverlayStore.higherOpacity.collectAsState()
        val dark by AppearanceStore.dark.collectAsState()
        val demo by OverlayStore.demo.collectAsState()
        val blur by OverlayStore.blur.collectAsState()
        val preview by fixture.collectAsState()
        val live by OverlayStore.liveSource.collectAsState()
        val source = preview ?: live
        val results by source.results.collectAsState()
        val panel by livePanel.state.collectAsState()
        val examining = capture.busy ||
            (preview != null && results.phase == LiveSessionPhase.CAPTURING)
        val shown = if (demo) null else liveOverlayForm(panel, results, examining)
        SideEffect { onForm(shown) }
        OvrlyTheme(dark) {
            CompositionLocalProvider(
                LocalWindowBlur provides
                    WindowGlass(blurred = blur == BlurMode.NATIVE, overlay = true)
            ) {
                when {
                    demo -> DemoWindow(blur)

                    shown != null -> {
                        val density = resources.displayMetrics.density
                        val geometry =
                            livePanelGeometry(usableSize.width, usableSize.height, density)
                        val pillAt by pillX.collectAsState()
                        LiveWindow(
                            shown,
                            LiveOverlayModel(
                                results,
                                panel,
                                source.label,
                                examiningState(
                                    capture,
                                    results,
                                    examining,
                                    panel.stopChoice == null
                                )
                            ),
                            LivePanelFrame(
                                (geometry.width / density).dp,
                                (geometry.height / density).dp,
                                opaque,
                                pillOffset =
                                    ((pillAt - geometry.margin).coerceAtLeast(0) / density).dp
                            )
                        )
                    }

                    else -> IdleControls(capture, opaque)
                }
            }
        }
    }

    @Composable
    private fun DemoWindow(blur: BlurMode) {
        val density = resources.displayMetrics.density
        val geometry = demoPanelGeometry(usableSize.width, usableSize.height, density)
        DemoOverlayPanel(
            blurLabel = blur.label,
            onClose = { stopSelf() },
            modifier = Modifier.semantics { customActions = verticalMoves("demo") },
            panelWidth = (geometry.width / density).dp,
            maxHeight = (geometry.height / density).dp,
            onHeaderHeight = { panelHeaderHeight = it }
        )
    }

    /** No capture and no connected session: the setup ring, or "Research offline" after one. */
    @Composable
    private fun IdleControls(capture: CaptureState, opaque: Boolean) {
        GlassOverlay(
            state = when (capture.phase) {
                CapturePhase.FINISHED, CapturePhase.ERROR -> OverlayVisual.Captured
                else -> OverlayVisual.Idle
            },
            higherOpacity = opaque,
            onSetup = { openCompanion(MainActivity.ACTION_SETUP) },
            onStopCapture = { livePanel.setStopPrompt(true) },
            onCancelResearch = { CaptureStore.message(NO_RESEARCH_TO_CANCEL) },
            onDetails = { openCompanion(MainActivity.ACTION_DETAILS) },
            modifier = Modifier.compactMoves(resources.displayMetrics.density)
        )
    }

    /**
     * The live overlay with its idle fade: the pill or bubble fades to [FADED_ALPHA] after
     * [IDLE_FADE_MS] without a touch or a new result, unless TalkBack is on or the phone's
     * animations are off. The expanded panel never fades; it collapses to the pill first.
     */
    @Composable
    private fun LiveWindow(shown: LiveOverlayForm, model: LiveOverlayModel, frame: LivePanelFrame) {
        val touchCount by touches.collectAsState()
        val reader by touchExploration.collectAsState()
        val animate = ValueAnimator.areAnimatorsEnabled()
        var faded by remember { mutableStateOf(false) }
        val compact = shown == LiveOverlayForm.PILL || shown == LiveOverlayForm.BUBBLE
        val fadeAllowed = compact && animate && !reader && !model.panel.stopPrompt
        LaunchedEffect(shown, touchCount, model.results.claims, fadeAllowed) {
            faded = false
            if (fadeAllowed) {
                delay(IDLE_FADE_MS)
                faded = true
            }
        }
        val alpha by animateFloatAsState(
            if (faded) FADED_ALPHA else 1f,
            tween(if (animate) FADE_MS else 0),
            label = "idleFade"
        )
        val density = resources.displayMetrics.density
        LiveOverlay(
            form = shown,
            model = model,
            actions = liveActions(),
            modifier = (
                if (shown == LiveOverlayForm.EXPANDED) {
                    Modifier.semantics { customActions = verticalMoves("live results") }
                } else {
                    Modifier.compactMoves(density)
                }
                ).graphicsLayer { this.alpha = alpha },
            frame = frame.copy(animate = animate)
        )
    }

    /** The pill's timer: the capture's, or for the fixture the captured length it reports. */
    private fun examiningState(
        capture: CaptureState,
        results: LiveResults,
        examining: Boolean,
        canStop: Boolean
    ): ExaminingState? = when {
        !examining -> null

        capture.busy -> ExaminingState(capture.seconds, canStop)

        else -> ExaminingState(
            ((results.coverage?.capturedMs ?: 0) / MS_PER_SECOND).toInt(),
            canStop
        )
    }

    private fun Modifier.compactMoves(density: Float): Modifier {
        val maxWidth = (usableSize.width / density - 24).coerceAtLeast(48f)
        return widthIn(max = maxWidth.dp).semantics {
            customActions = listOf(
                CustomAccessibilityAction("Move overlay left") {
                    moveBy(-48, 0)
                    true
                },
                CustomAccessibilityAction("Move overlay right") {
                    moveBy(48, 0)
                    true
                },
                CustomAccessibilityAction("Move overlay up") {
                    moveBy(0, -48)
                    true
                },
                CustomAccessibilityAction("Move overlay down") {
                    moveBy(0, 48)
                    true
                }
            )
        }
    }

    private fun verticalMoves(name: String) = listOf(
        CustomAccessibilityAction("Move $name up") {
            moveBy(0, -48)
            true
        },
        CustomAccessibilityAction("Move $name down") {
            moveBy(0, 48)
            true
        }
    )

    /**
     * Window placement for a new live form: the panel opens where the pill is, and the pill
     * comes back at the panel's top when it collapses.
     */
    private fun onForm(next: LiveOverlayForm?) {
        val previous = form
        if (next == previous) return
        form = next
        when {
            next == LiveOverlayForm.EXPANDED -> {
                if (previous != null) compactPosition = params.x to params.y
                pillX.value = compactPosition.first
                panelTop = compactPosition.second
                configureWindow()
            }

            // The bubble's size is known, so its edge is computed now rather than from a
            // window that may still have the panel's or the pill's width.
            next == LiveOverlayForm.BUBBLE -> {
                val x = if (previous ==
                    LiveOverlayForm.EXPANDED
                ) {
                    compactPosition.first
                } else {
                    params.x
                }
                val y = if (previous == LiveOverlayForm.EXPANDED) panelTop else params.y
                pendingCompactPosition = bubbleSnapX(x, usableSize.width, density()) to y
                configureWindow()
            }

            previous == LiveOverlayForm.EXPANDED -> {
                pendingCompactPosition = compactPosition.first to panelTop
                configureWindow()
            }

            else -> configureWindow()
        }
        if (next == LiveOverlayForm.SAVED) {
            savedJob?.cancel()
            savedJob = lifecycleScope.launch {
                delay(SAVED_MS)
                stopSelf()
            }
        }
    }

    private fun dragCallbacks() = object : DragCallbacks {
        override fun canDrag(y: Float): Boolean = when {
            OverlayStore.demo.value || form == LiveOverlayForm.EXPANDED -> y < panelHeaderHeight
            else -> true
        }

        override fun longPressDrag(): Boolean = form == LiveOverlayForm.BUBBLE

        override fun currentPosition() =
            if (form == LiveOverlayForm.EXPANDED) pillX.value to params.y else params.x to params.y

        override fun onMove(x: Int, y: Int) {
            if (form == LiveOverlayForm.EXPANDED) {
                // The window spans the panel and moves only vertically; the pill moves inside.
                val left = params.x
                val right = left + (params.width - pillWidth).coerceAtLeast(0)
                val pill = x.coerceIn(left, right)
                pillX.value = pill
                compactPosition = pill to compactPosition.second
                moveTo(params.x, y)
                panelTop = params.y
            } else {
                moveTo(x, y)
            }
            dismissTarget?.highlight(bubbleOverTarget())
        }

        override fun onTouch() {
            touches.value += 1
            livePanel.touched()
        }

        override fun onLongPress() {
            val target =
                dismissTarget ?: DismissTarget(this@OverlayService, wm).also { dismissTarget = it }
            runCatching {
                target.show(AppearanceStore.dark.value, usableSize.width, usableSize.height)
            }
        }

        override fun onRelease(longPressed: Boolean) {
            val dismiss = longPressed && bubbleOverTarget()
            dismissTarget?.hide()
            when {
                dismiss -> stopSelf()

                form == LiveOverlayForm.BUBBLE ->
                    moveTo(bubbleSnapX(params.x, usableSize.width, density()), params.y)
            }
        }
    }

    private fun bubbleOverTarget(): Boolean {
        val view = root ?: return false
        return dismissTarget?.contains(params.x + view.width / 2, params.y + view.height / 2) ==
            true
    }

    private fun density() = resources.displayMetrics.density

    private fun notificationTitle() = when {
        OverlayStore.demo.value -> "ovrly demo / simulated content"
        fixture.value != null -> "ovrly overlay / fixture results"
        else -> "ovrly overlay is visible"
    }

    private fun notificationText() = when {
        OverlayStore.demo.value -> DEMO_NOTIFICATION_TEXT
        fixture.value != null -> "Synthetic results. No capture, research or network request."
        else -> "Idle overlay does not capture. Hiding does not stop an active capture."
    }

    private fun liveActions() = LivePanelActions(
        onExpand = { livePanel.setExpanded(true) },
        onCollapse = { livePanel.setExpanded(false) },
        onOpenPill = { livePanel.setExpanded(false) },
        onDismiss = { stopSelf() },
        onOpenClaim = livePanel::openClaim,
        onCloseClaim = { livePanel.openClaim(null) },
        onDismissNotice = livePanel::dismissNotice,
        onRequestStop = { livePanel.setStopPrompt(true) },
        onStopChoice = livePanel::chooseStop,
        onCancelStop = { livePanel.setStopPrompt(false) },
        onPillSize = {
            panelHeaderHeight = it.height
            pillWidth = it.width
        }
    )

    /** Debug builds only: replaces the live source with the labelled fixture timeline. */
    private fun openFixture() {
        closeFixture()
        val source = FixtureLiveResultsSource()
        livePanel.reset()
        fixture.value = source
        fixtureJob = lifecycleScope.launch {
            while (true) {
                delay(FIXTURE_STEP_MS)
                if (!source.advance()) break
            }
        }
    }

    private fun closeFixture() {
        if (fixture.value == null && fixtureJob == null) return
        fixtureJob?.cancel()
        fixtureJob = null
        fixture.value = null
        livePanel.reset()
    }

    private fun openCompanion(action: String) {
        startActivity(
            Intent(this, MainActivity::class.java).setAction(action)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
    }

    private fun moveBy(xDp: Int, yDp: Int) {
        val density = resources.displayMetrics.density
        moveTo(params.x + (xDp * density).roundToInt(), params.y + (yDp * density).roundToInt())
    }

    /** The demo or expanded live frame, or null for the compact forms. */
    private fun panelGeometry(): DemoPanelGeometry? {
        val density = resources.displayMetrics.density
        return when {
            OverlayStore.demo.value -> demoPanelGeometry(
                usableSize.width,
                usableSize.height,
                density
            )

            form == LiveOverlayForm.EXPANDED ->
                livePanelGeometry(usableSize.width, usableSize.height, density)

            else -> null
        }
    }

    private fun moveTo(x: Int, y: Int) {
        val view = root
        val geometry = panelGeometry()
        val demo = OverlayStore.demo.value
        val margin = geometry?.margin ?: 0
        val width = when {
            geometry != null -> geometry.width
            form == LiveOverlayForm.BUBBLE -> bubbleWidth(density())
            else -> view?.width ?: 0
        }
        // The demo is always its full height; the live panel is as tall as its content.
        val height = if (demo && geometry != null) geometry.height else view?.height ?: 0
        val newX = if (geometry !=
            null
        ) {
            margin
        } else {
            x.coerceIn(0, (usableSize.width - width).coerceAtLeast(0))
        }
        val newY = y.coerceIn(margin, (usableSize.height - height - margin).coerceAtLeast(margin))
        params.x = newX
        params.y = newY

        if (view?.isAttachedToWindow == true) {
            try {
                overlayWindow?.layout(params.width, params.height, newX, newY)
            } catch (
                error: SecurityException
            ) {
                overlayError(error)
            } catch (
                error: IllegalArgumentException
            ) {
                overlayError(error)
            }
        }
    }

    private fun configureWindow(resetPosition: Boolean = false) {
        val geometry = panelGeometry()
        val demo = OverlayStore.demo.value
        params.width = geometry?.width ?: WindowManager.LayoutParams.WRAP_CONTENT
        when {
            geometry != null && demo && resetPosition -> {
                params.x = geometry.margin
                params.y = geometry.bottomY(usableSize.height)
            }

            // The live panel keeps its top where the pill was; moveTo lifts it to stay on screen.
            geometry != null && !demo -> {
                params.x = geometry.margin
                params.y = panelTop
            }

            resetPosition -> {
                params.x = compactPosition.first
                params.y = compactPosition.second
            }
        }
        moveTo(params.x, params.y)
    }

    private fun availableWindowSize(): IntSize {
        if (Build.VERSION.SDK_INT >= 30) {
            val metrics = wm.currentWindowMetrics
            val insets = metrics.windowInsets.getInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout()
            )
            return IntSize(
                (metrics.bounds.width() - insets.left - insets.right).coerceAtLeast(1),
                (metrics.bounds.height() - insets.top - insets.bottom).coerceAtLeast(1)
            )
        }
        val frame = android.graphics.Rect()
        root?.getWindowVisibleDisplayFrame(frame)
        return if (!frame.isEmpty) {
            IntSize(frame.width(), frame.height())
        } else {
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
        CaptureStore.message(
            "The overlay could not be displayed. Check overlay permission. " +
                "Capture can still be stopped in the companion or notification."
        )
        stopSelf()
    }

    override fun onDestroy() {
        closeFixture()
        savedJob?.cancel()
        dismissTarget?.hide()
        dismissTarget = null
        explorationListener?.let {
            getSystemService(
                AccessibilityManager::class.java
            )?.removeTouchExplorationStateChangeListener(it)
        }
        explorationListener = null
        compose?.disposeComposition()
        overlayWindow?.close()
        overlayWindow = null
        root = null
        compose = null
        form = null
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

        /** Debug builds only: show the live overlay with [FixtureLiveResultsSource]. */
        const val SHOW_LIVE_FIXTURE = "show_live_fixture"
        private const val FIXTURE_STEP_MS = 5_000L
        private const val HIDE_LABEL = "Hide overlay (capture continues)"
        internal const val IDLE_FADE_MS = 4_000L
        internal const val SAVED_MS = 3_000L
        private const val FADE_MS = 300
        private const val FADED_ALPHA = 0.7f
        private const val MS_PER_SECOND = 1000
        private const val DEMO_NOTIFICATION_TEXT =
            "No recording, research or microphone. Tap Close demo to dismiss."
        private const val NO_RESEARCH_TO_CANCEL =
            "Research is not connected; there is no research task to cancel."
    }
}
