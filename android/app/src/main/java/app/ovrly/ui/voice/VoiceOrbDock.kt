package app.ovrly.ui.voice

import android.os.Build
import android.view.HapticFeedbackConstants
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.EaseOutCubic
import androidx.compose.animation.core.tween
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.MeasurePolicy
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ovrly.ui.LocalOvrlyPalette
import app.ovrly.ui.chromeGlare
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

private object OrbDockRef {
    const val HERO_BRAND_GAP_DP = 2
    const val HERO_TEXT_BLOCK_DP = 40
    const val OPEN_MS = 380
    const val GLARE_FOLLOW_MS = 1500
    const val CLOSE_MS = 320
    const val HERO_CANVAS_SCALE = 1.6f
    const val HERO_TEXT_GAP_DP = 12
    const val HERO_LINE_GAP_DP = 2
    const val HOLD_PROGRESS_SECONDS = 1.2f
    const val HOLD_FRAME_MS = 16L
    const val ACCESSIBILITY_HOLD_MS = 3_000L
}

/**
 * Dock state shared by every screen that hosts the orb, so switching tabs keeps the session's
 * phase and the collapsed/expanded decision. One instance lives in the app shell.
 *
 * Interaction contract (user-approved):
 * - Collapsed orb (44 dp, 48 dp touch target, trailing slot of the brand row): tap only → expands. No start/stop/hold.
 * - Idle hero: "Tap to talk". Auto-collapses after 6 s untouched, on scroll, socket tap, or back.
 *   Tap → start. Hold → start in push-to-talk; release → continuous listening.
 * - Live hero tap: SPEAKING → interrupt; otherwise stop and fly home. Hold → push-to-talk
 *   (ignored while SPEAKING). Collapsing never stops voice; the small orb shows the live phase.
 * - Error: message for 4 s, then collapse; no lingering glyph.
 * - Stop while away from these screens lives in Settings.
 */
@Stable
class VoiceOrbDockState(private val source: VoiceOrbSource) {
    internal val motion = OrbMotion()
    var expanded by mutableStateOf(false)
        private set
    val progress = Animatable(0f)

    /** True while a scroll drag owns the travel; [dragValue] then replaces [progress]. */
    internal var dragging by mutableStateOf(false)
    internal var dragValue by mutableFloatStateOf(0f)

    /** Bumped to re-run the settle animation when [expanded] itself did not change. */
    internal var settleRequest by mutableIntStateOf(0)
    internal var collapseRangePx = 0f
    internal val scrollCollapse = DockScrollCollapse(this)

    /** Open/close travel, 0..1 (a spring may briefly overshoot). Read in layout/draw only. */
    internal val visualProgress: Float get() = if (dragging) dragValue else progress.value
    var line by mutableStateOf("Tap to talk")
        private set
    var microphoneDenied by mutableStateOf(false)
    var permanentlyDenied by mutableStateOf(false)
    var requestPermission: () -> Unit = {}
    var openAppSettings: () -> Unit = {}
    private var collapseJob: Job? = null

    /** Scope of the last idle timer, so a drag that springs the hero back open can restart it. */
    internal var idleScope: kotlinx.coroutines.CoroutineScope? = null

    internal val phase: OrbPhase get() = motion.phase

    /** Open but unconfigured: touches give no press feedback because nothing can start. */
    internal val inert: Boolean
        get() = expanded && motion.phase == OrbPhase.MUTED && !source.configured
    val status: String get() = source.state.value.status

    suspend fun bind() {
        // Permission denial is UI state, so it must re-derive the phase without an engine change.
        combine(source.state, snapshotFlow { microphoneDenied to permanentlyDenied }) { s, _ -> s }
            .collectLatest { s ->
                val muted = !source.configured || microphoneDenied
                motion.transitionTo(s.phase.toOrbPhase(muted))
                line = lineFor(motion.phase, s, source, permanentlyDenied)
                if (motion.phase == OrbPhase.ERROR) {
                    // The engine stays in ERROR until the next start; the orb shows it briefly,
                    // then returns to plain idle with no lingering glyph.
                    delay(OrbSpec.ERROR_HERO_TIMEOUT_MS)
                    motion.transitionTo(OrbPhase.IDLE)
                    line = lineFor(OrbPhase.IDLE, s, source, permanentlyDenied)
                }
            }
    }

    suspend fun bindLevel() {
        source.level.collectLatest { motion.level = it }
    }
    suspend fun bindEvents() {
        source.events.collect { motion.onEvent(it) }
    }

    fun open(scope: kotlinx.coroutines.CoroutineScope) {
        expanded = true
        scheduleIdleCollapse(scope)
    }

    fun close() {
        expanded = false
        collapseJob?.cancel()
    }

    /** Idle hero waits 6 s for a tap, then goes home. Never while live. */
    fun scheduleIdleCollapse(scope: kotlinx.coroutines.CoroutineScope) {
        idleScope = scope
        collapseJob?.cancel()
        if (motion.phase.isLive()) return
        collapseJob = scope.launch {
            delay(
                if (motion.phase ==
                    OrbPhase.ERROR
                ) {
                    OrbSpec.ERROR_HERO_TIMEOUT_MS
                } else {
                    OrbSpec.IDLE_HERO_TIMEOUT_MS
                }
            )
            // A drag in progress owns the hero; its release decides (and restarts this timer).
            if (!motion.phase.isLive() && !dragging) close()
        }
    }

    fun onTap(scope: kotlinx.coroutines.CoroutineScope, haptic: (Int) -> Unit) {
        // An open, unconfigured orb has nothing to do on tap: no squish, just keep it open.
        if (inert) {
            scheduleIdleCollapse(scope)
            return
        }
        motion.fire(OrbMotion.Transient.Kind.TAP)
        haptic(HapticFeedbackConstants.CONTEXT_CLICK)
        if (!expanded) {
            open(scope)
            return
        }
        when (motion.phase) {
            OrbPhase.IDLE -> {
                collapseJob?.cancel()
                source.start()
            }

            OrbPhase.MUTED -> if (permanentlyDenied) {
                openAppSettings()
            } else {
                requestPermission()
            }

            OrbPhase.ERROR -> {
                collapseJob?.cancel()
                source.start()
            }

            OrbPhase.SPEAKING -> source.interrupt()

            OrbPhase.CONNECTING, OrbPhase.LISTENING, OrbPhase.THINKING, OrbPhase.FINISHING -> {
                source.stop()
                motion.fire(OrbMotion.Transient.Kind.SNAP)
                scope.launch {
                    delay(OrbSpec.PHASE_TRANSITION_MS.toLong())
                    close()
                }
            }
        }
    }

    fun onHoldStart(haptic: (Int) -> Unit): Boolean {
        if (!expanded || motion.phase == OrbPhase.SPEAKING ||
            motion.phase == OrbPhase.MUTED
        ) {
            return false
        }
        collapseJob?.cancel()
        haptic(HapticFeedbackConstants.LONG_PRESS)
        source.holdStart()
        return true
    }

    fun onHoldEnd(haptic: (Int) -> Unit) {
        motion.holdFill = 0f
        haptic(HapticFeedbackConstants.CONTEXT_CLICK)
        source.holdEnd()
        motion.fire(OrbMotion.Transient.Kind.SNAP)
    }

    /** Called by the host when the engine reports an error so the hero shows it, then collapses. */
    fun onPhaseForCollapse(scope: kotlinx.coroutines.CoroutineScope) {
        if (!expanded) return
        when {
            motion.phase == OrbPhase.ERROR -> scheduleIdleCollapse(scope)

            // A session that ended on its own (silence, time limit) goes home with it.
            motion.phase == OrbPhase.IDLE && motion.previousPhase.isLive() -> close()
        }
    }
}

/** The hero's state line; simulated builds prefix every line with "Demo". */
private fun lineFor(
    phase: OrbPhase,
    state: VoiceOrbState,
    source: VoiceOrbSource,
    permanentlyDenied: Boolean
): String {
    val configured = source.configured
    val line = when (phase) {
        OrbPhase.IDLE -> "Tap to talk"

        OrbPhase.CONNECTING -> "Connecting…"

        OrbPhase.LISTENING -> "Listening · tap to stop"

        OrbPhase.THINKING -> "Thinking"

        OrbPhase.SPEAKING -> "Speaking · tap to interrupt"

        OrbPhase.FINISHING -> "Finishing reply · microphone off"

        OrbPhase.MUTED -> when {
            !configured -> "Not configured"
            permanentlyDenied -> "Microphone blocked · open Settings"
            else -> "Microphone needed · tap to allow"
        }

        // Short and plain on the orb; the full diagnostic stays in Settings and the log.
        OrbPhase.ERROR -> "${errorTitle(state.status)} \u00b7 tap to retry"
    }
    return if (source.requiresMicrophone) line else "Demo \u00b7 $line"
}

private fun errorTitle(status: String) = when (status) {
    "Voice unavailable" -> "Couldn\u2019t connect"
    "Connection timed out" -> "Connection timed out"
    "Audio unavailable" -> "Microphone unavailable"
    "Protocol error", "Message limit reached", "Action limit reached" -> "Voice stopped"
    else -> status.ifBlank { "Voice stopped" }
}

/**
 * The brand row with the orb docked in its trailing slot. [brand] is the wordmark lockup; this
 * composable lays it out, overlays the orb/socket/text, and animates between the collapsed row and
 * the hero header. Place it as the first item of a scrolling list that uses
 * [voiceDockScroll], and feed [scrolledPastTop] as a fallback for programmatic scrolls.
 */
@Composable
internal fun VoiceOrbDock(
    state: VoiceOrbDockState,
    scrolledPastTop: Boolean,
    modifier: Modifier = Modifier,
    brand: @Composable () -> Unit
) {
    val palette = LocalOvrlyPalette.current
    val scope = rememberCoroutineScope()
    val view = LocalView.current
    val density = LocalDensity.current
    val haptic: (Int) -> Unit = { view.performHapticFeedback(it) }
    DockEffects(state, scrolledPastTop, scope, haptic)

    val hero = heroSize(with(density) { LocalWindowInfo.current.containerSize.width.toDp() })
    val geometry = remember(hero, density) { DockGeometry(hero, density) }
    SideEffect { state.collapseRangePx = geometry.collapseRangePx }
    // Animation frames are read only through this lambda in layout and draw, never in composition.
    val q = { state.visualProgress }

    Layout(
        modifier = modifier.fillMaxWidth(),
        content = {
            Box(Modifier.height(OrbSpec.wordmarkHeight), contentAlignment = Alignment.CenterStart) {
                brand()
            }
            Socket(state, visible = {
                DockFade.fade(q(), DockFade.SOCKET_START, DockFade.SOCKET_RANGE)
            }) {
                state.close()
            }
            Box(
                Modifier
                    .orbGestures(
                        state,
                        scope,
                        haptic,
                        canvasPx = { geometry.canvasPx(q()) },
                        bodyPx = { maxOf(geometry.bodyPx(q()), geometry.minTouchPx) }
                    )
                    .orbSemantics(state, scope, haptic)
            ) {
                // The idle docked orb catches the wordmark's sweep just after it leaves the
                // lettering, so the shine reads as one pass along the header.
                val shine = !state.expanded && state.phase == OrbPhase.IDLE
                VoiceOrb(
                    state.motion,
                    bodyPx = { geometry.bodyPx(q()) },
                    light = !palette.dark,
                    modifier = Modifier.fillMaxSize().chromeGlare(
                        strength = glareStrength(shine, palette.dark),
                        startDelayMillis = OrbDockRef.GLARE_FOLLOW_MS,
                        bandFraction = GLARE_BAND
                    )
                )
            }
            val textAlpha = { DockFade.fade(q(), DockFade.TEXT_START, DockFade.TEXT_RANGE) }
            HeroText("Ask about your space", textAlpha, MaterialTheme.typography.bodyLarge)
            HeroText(
                state.line,
                textAlpha,
                MaterialTheme.typography.labelMedium.copy(letterSpacing = .6.sp),
                palette.muted
            )
        },
        measurePolicy = dockMeasurePolicy(q, geometry)
    )
}

@Composable
private fun DockEffects(
    state: VoiceOrbDockState,
    scrolledPastTop: Boolean,
    scope: CoroutineScope,
    haptic: (Int) -> Unit
) {
    val currentHaptic by rememberUpdatedState(haptic)
    LaunchedEffect(state) { state.bind() }
    LaunchedEffect(state) { state.bindLevel() }
    LaunchedEffect(state) { state.bindEvents() }
    // Drives the open/close travel from composition, so an animation cut off when the header
    // scrolls out of a lazy list resumes and settles when it comes back.
    LaunchedEffect(state, state.expanded, state.settleRequest) {
        // Continue from wherever a drag left the orb; never jump back to the pre-drag value.
        if (state.dragging) {
            state.progress.snapTo(state.dragValue)
            state.dragging = false
        }
        // Both directions are calm eased glides, no bounce, so they mirror each other.
        if (state.expanded) {
            state.progress.animateTo(1f, tween(OrbDockRef.OPEN_MS, easing = EaseOutCubic))
        } else {
            state.progress.animateTo(0f, tween(OrbDockRef.CLOSE_MS, easing = EaseOutCubic))
        }
    }
    LaunchedEffect(state.phase) {
        state.onPhaseForCollapse(scope)
        if (state.phase == OrbPhase.LISTENING && state.expanded) {
            // CONFIRM is API 30+; Android 10 gets the plain click used elsewhere.
            currentHaptic(
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    HapticFeedbackConstants.CONFIRM
                } else {
                    HapticFeedbackConstants.CONTEXT_CLICK
                }
            )
        }
    }
    LaunchedEffect(scrolledPastTop) {
        if (scrolledPastTop && state.expanded) state.close()
    }
    BackHandler(enabled = state.expanded) { state.close() }
}

@Composable
private fun HeroText(
    text: String,
    alpha: () -> Float,
    style: TextStyle,
    color: Color = Color.Unspecified
) {
    Box(Modifier.graphicsLayer { this.alpha = alpha() }, contentAlignment = Alignment.Center) {
        Text(text, style = style, color = color, textAlign = TextAlign.Center)
    }
}

/** Fixed pixel geometry for one hero size; only the progress varies per frame. */
private class DockGeometry(hero: Dp, density: Density) {
    val dockPx = with(density) { OrbSpec.dockSize.toPx() }
    val minTouchPx = with(density) { OrbSpec.minTouch.toPx() }
    val heroPx = with(density) { hero.toPx() }
    val brandPx = with(density) { OrbSpec.wordmarkHeight.toPx() }
    val reachPx = heroPx / 2f * OrbSpec.HERO_REACH
    val brandGapPx = with(density) { OrbDockRef.HERO_BRAND_GAP_DP.dp.toPx() }
    val textGapPx = with(density) { OrbDockRef.HERO_TEXT_GAP_DP.dp.toPx() }
    val lineGapPx = with(density) { OrbDockRef.HERO_LINE_GAP_DP.dp.toPx() }
    val arcBowPx = with(density) { OrbSpec.ARC_BOW_DP.dp.toPx() }
    val textBlockPx = with(density) { OrbDockRef.HERO_TEXT_BLOCK_DP.dp.toPx() }
    private val expandedRowPx = brandPx + brandGapPx + reachPx * 2 + textGapPx + textBlockPx
    val collapseRangePx = expandedRowPx - brandPx

    /** Body diameter; size lags position (q^1.3) so the orb arrives, then fills out. */
    fun bodyPx(q: Float) =
        dockPx + (heroPx - dockPx) * q.coerceAtLeast(0f).pow(OrbSpec.SIZE_LAG_EXPONENT)

    fun canvasPx(q: Float) = bodyPx(q) * OrbDockRef.HERO_CANVAS_SCALE // ring + glow overhang

    fun rowPx(q: Float) = brandPx + collapseRangePx * q.coerceIn(0f, 1f)
}

/** Places brand, socket, orb (on a quadratic arc from the slot to the hero) and the hero text. */
private fun dockMeasurePolicy(progress: () -> Float, g: DockGeometry) =
    MeasurePolicy { measurables, constraints ->
        val q = progress()
        val p = q.coerceIn(0f, 1f)
        val w = constraints.maxWidth
        val loose = constraints.copy(minWidth = 0, minHeight = 0)
        val canvas = g.canvasPx(q).roundToInt()
        val brandPl = measurables[0].measure(loose)
        val socketPl = measurables[1].measure(loose)
        val orbPl = measurables[2].measure(Constraints.fixed(canvas, canvas))
        // the two hero text lines are the last children
        val ctaPl = measurables[measurables.lastIndex - 1].measure(constraints.copy(minHeight = 0))
        val linePl = measurables.last().measure(constraints.copy(minHeight = 0))
        // home: centred on the trailing slot, vertically centred on the brand row
        val x0 = w - g.dockPx / 2f
        val y0 = g.brandPx / 2f
        // hero: centred horizontally, below the brand row
        val x1 = w / 2f
        val y1 = g.brandPx + g.brandGapPx + g.reachPx
        // quadratic arc bowing toward the leading edge so the orb swings in
        val cxp = (x0 + x1) / 2f - g.arcBowPx
        val cyp = (y0 + y1) / 2f
        val bx = (1 - p) * (1 - p) * x0 + 2 * (1 - p) * p * cxp + p * p * x1
        val by = (1 - p) * (1 - p) * y0 + 2 * (1 - p) * p * cyp + p * p * y1
        // Text hangs from the bottom of the row: below the ring when open, sliding up (and fading)
        // with the row as it collapses so it never overlaps the content underneath.
        val rowPx = g.rowPx(q)
        val textTop = (rowPx - g.textBlockPx).roundToInt()
        layout(w, rowPx.roundToInt()) {
            brandPl.place(0, 0)
            // above the orb, whose large gesture box would otherwise swallow socket taps
            socketPl.place(
                (x0 - socketPl.width / 2f).roundToInt(),
                (y0 - socketPl.height / 2f).roundToInt(),
                zIndex = 1f
            )
            orbPl.place(
                (bx - orbPl.width / 2f).roundToInt(),
                (by - orbPl.height / 2f).roundToInt()
            )
            ctaPl.place((w - ctaPl.width) / 2, textTop)
            linePl.place((w - linePl.width) / 2, textTop + ctaPl.height + g.lineGapPx.roundToInt())
        }
    }

// A quiet echo of the wordmark glare (0.6 / 0.34 in BrandLockup): fainter and narrower.
private const val GLARE_DARK = 0.22f
private const val GLARE_LIGHT = 0.14f
private const val GLARE_BAND = 0.12f // of the 1.6x canvas, about a fifth of the body

private fun glareStrength(shine: Boolean, dark: Boolean) = when {
    !shine -> 0f
    dark -> GLARE_DARK
    else -> GLARE_LIGHT
}

private object DockFade {
    const val SOCKET_START = .25f
    const val SOCKET_RANGE = .45f
    const val TEXT_START = .6f
    const val TEXT_RANGE = .4f

    fun fade(q: Float, start: Float, range: Float) = ((q - start) / range).coerceIn(0f, 1f)
}

private fun Modifier.orbGestures(
    state: VoiceOrbDockState,
    scope: kotlinx.coroutines.CoroutineScope,
    haptic: (Int) -> Unit,
    canvasPx: () -> Float,
    bodyPx: () -> Float
) = pointerInput(state) {
    awaitEachGesture {
        val down = awaitFirstDown()
        val centre = Offset(canvasPx() / 2f, canvasPx() / 2f)
        if (!orbHit(down.position, centre, bodyPx() / 2f)) return@awaitEachGesture
        down.consume()
        if (!state.inert) state.motion.fire(OrbMotion.Transient.Kind.TAP)
        var holding = false
        val holdJob = scope.launch {
            delay(OrbSpec.HOLD_THRESHOLD_MS)
            if (state.onHoldStart(haptic)) {
                holding = true
                // fill ring over the hold; purely visual
                val start = state.motion.time
                while (true) {
                    state.motion.holdFill =
                        ((state.motion.time - start) / OrbDockRef.HOLD_PROGRESS_SECONDS).coerceIn(
                            0f,
                            1f
                        )
                    delay(OrbDockRef.HOLD_FRAME_MS)
                }
            }
        }
        val up = waitForUpOrCancellation()
        holdJob.cancel()
        when {
            holding -> state.onHoldEnd(haptic)
            up != null -> state.onTap(scope, haptic)
        }
    }
}

private fun Modifier.orbSemantics(
    state: VoiceOrbDockState,
    scope: kotlinx.coroutines.CoroutineScope,
    haptic: (Int) -> Unit
) = semantics {
    role = Role.Button
    contentDescription = "Voice"
    stateDescription = state.line
    customActions = buildList {
        if (state.expanded && state.phase != OrbPhase.SPEAKING && state.phase != OrbPhase.MUTED) {
            add(
                CustomAccessibilityAction("Push to talk") {
                    if (state.onHoldStart(haptic)) {
                        scope.launch {
                            delay(OrbDockRef.ACCESSIBILITY_HOLD_MS)
                            state.onHoldEnd(haptic)
                        }
                    }
                    true
                }
            )
        }
    }
}

/** Hero body: the spec size, capped to a share of narrow screens. */
private fun heroSize(screenWidth: Dp): Dp =
    minOf(OrbSpec.heroSize, screenWidth * OrbSpec.HERO_WIDTH_FRACTION)
