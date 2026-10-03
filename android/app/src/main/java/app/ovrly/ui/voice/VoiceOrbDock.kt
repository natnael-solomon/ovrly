package app.ovrly.ui.voice

import android.os.Build
import android.view.HapticFeedbackConstants
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
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
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.MeasurePolicy
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ovrly.ui.LocalOvrlyPalette
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

private object OrbDockRef {
    const val HERO_BRAND_GAP_DP = 8
    const val HERO_TEXT_BLOCK_DP = 56
    const val HERO_CANVAS_SCALE = 1.6f
    const val SOCKET_VISIBLE_START = .25f
    const val SOCKET_VISIBLE_RANGE = .45f
    const val HERO_TEXT_VISIBLE_START = .6f
    const val HERO_TEXT_VISIBLE_RANGE = .4f
    const val HERO_TEXT_OVERLAP_DP = 6
    const val HERO_LINE_GAP_DP = 2
    const val HOLD_PROGRESS_SECONDS = 1.2f
    const val HOLD_FRAME_MS = 16L
    const val ACCESSIBILITY_HOLD_MS = 3_000L
    const val EASE_HALF = .5f
    const val EASE_CUBIC = 4
    const val EASE_EXPONENT = 3
}

/**
 * Dock state shared by every screen that hosts the orb, so switching tabs keeps the session's
 * phase and the collapsed/expanded decision. One instance lives in the app shell.
 *
 * Interaction contract (user-approved):
 * - Collapsed orb (48 dp, trailing slot of the brand row): tap only → expands. No start/stop/hold.
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
    var line by mutableStateOf("Tap to talk")
        private set
    var microphoneDenied by mutableStateOf(false)
    var permanentlyDenied by mutableStateOf(false)
    var requestPermission: () -> Unit = {}
    var openAppSettings: () -> Unit = {}
    private var collapseJob: Job? = null

    internal val phase: OrbPhase get() = motion.phase
    val status: String get() = source.state.value.status

    suspend fun bind() {
        // Permission denial is UI state, so it must re-derive the phase without an engine change.
        combine(source.state, snapshotFlow { microphoneDenied to permanentlyDenied }) { s, _ -> s }
            .collectLatest { s ->
                val muted = !source.configured || microphoneDenied
                motion.transitionTo(s.phase.toOrbPhase(muted))
                line = lineFor(motion.phase, s, source.configured, permanentlyDenied)
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
        scope.launch {
            progress.animateTo(1f, spring(OrbSpec.OPEN_DAMPING, Spring.StiffnessMediumLow))
        }
        scheduleIdleCollapse(scope)
    }

    fun close(scope: kotlinx.coroutines.CoroutineScope) {
        expanded = false
        collapseJob?.cancel()
        scope.launch {
            progress.animateTo(0f, spring(OrbSpec.CLOSE_DAMPING, Spring.StiffnessMediumLow))
        }
    }

    /** Idle hero waits 6 s for a tap, then goes home. Never while live. */
    fun scheduleIdleCollapse(scope: kotlinx.coroutines.CoroutineScope) {
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
            if (!motion.phase.isLive()) close(this)
        }
    }

    fun onTap(scope: kotlinx.coroutines.CoroutineScope, haptic: (Int) -> Unit) {
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

            OrbPhase.MUTED -> if (!source.configured) {
                scheduleIdleCollapse(scope)
            } else if (permanentlyDenied) {
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
                    close(this)
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
        if (motion.phase == OrbPhase.ERROR && expanded) scheduleIdleCollapse(scope)
    }
}

private fun lineFor(
    phase: OrbPhase,
    state: VoiceOrbState,
    configured: Boolean,
    permanentlyDenied: Boolean
) = when (phase) {
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

    OrbPhase.ERROR -> state.message.ifBlank { state.status }
}

/**
 * The brand row with the orb docked in its trailing slot. [brand] is the wordmark lockup; this
 * composable lays it out, overlays the orb/socket/text, and animates between the collapsed row and
 * the hero header. Place it as the first item of a scrolling list and feed [scrolledPastTop] so the
 * hero collapses when the user scrolls.
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

    val p = easeInOut(state.progress.value)
    val body = lerp(OrbSpec.dockSize, OrbSpec.heroSize, p.pow(OrbSpec.SIZE_LAG_EXPONENT))
    val canvas = body * OrbDockRef.HERO_CANVAS_SCALE // room for ring + glow overhang
    val brandH = OrbSpec.wordmarkHeight
    val textBlock = OrbDockRef.HERO_TEXT_BLOCK_DP.dp // cta + state line under the hero
    val heroH = brandH + OrbDockRef.HERO_BRAND_GAP_DP.dp + OrbSpec.heroSize + textBlock
    val rowH = lerp(brandH, heroH, p)
    val textAlpha = (
        (p - OrbDockRef.HERO_TEXT_VISIBLE_START) / OrbDockRef.HERO_TEXT_VISIBLE_RANGE
        ).coerceIn(0f, 1f)
    val socketAlpha = (
        (p - OrbDockRef.SOCKET_VISIBLE_START) / OrbDockRef.SOCKET_VISIBLE_RANGE
        ).coerceIn(0f, 1f)

    Layout(
        modifier = modifier.fillMaxWidth().height(rowH),
        content = {
            Box(Modifier.height(brandH), contentAlignment = Alignment.CenterStart) { brand() }
            Socket(state, visible = socketAlpha) { state.close(scope) }
            Box(
                Modifier.size(canvas)
                    .orbGestures(
                        state,
                        scope,
                        haptic,
                        canvasPx = with(density) { canvas.toPx() },
                        bodyPx = with(density) { body.toPx() }
                    )
                    .orbSemantics(state, scope, haptic)
            ) {
                VoiceOrb(
                    state.motion,
                    bodySize = body,
                    light = !palette.dark,
                    modifier = Modifier.size(canvas)
                )
            }
            HeroText("Ask about your space", textAlpha, MaterialTheme.typography.bodyLarge)
            HeroText(
                state.line,
                textAlpha,
                MaterialTheme.typography.labelMedium.copy(letterSpacing = .6.sp),
                palette.muted
            )
        },
        measurePolicy = dockMeasurePolicy(p, rowH)
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
        if (scrolledPastTop && state.expanded) state.close(scope)
    }
    BackHandler(enabled = state.expanded) { state.close(scope) }
}

@Composable
private fun HeroText(
    text: String,
    alpha: Float,
    style: TextStyle,
    color: Color = Color.Unspecified
) {
    Box(Modifier.alpha(alpha), contentAlignment = Alignment.Center) {
        Text(text, style = style, color = color, textAlign = TextAlign.Center)
    }
}

/** Places brand, socket, orb (on a quadratic arc from the slot to the hero) and the hero text. */
private fun dockMeasurePolicy(p: Float, rowH: Dp) = MeasurePolicy { measurables, constraints ->
    val w = constraints.maxWidth
    val loose = constraints.copy(minWidth = 0, minHeight = 0)
    val brandPl = measurables[0].measure(loose)
    val socketPl = measurables[1].measure(loose)
    val orbPl = measurables[2].measure(loose)
    // the two hero text lines are the last children
    val ctaPl = measurables[measurables.lastIndex - 1].measure(constraints.copy(minHeight = 0))
    val linePl = measurables.last().measure(constraints.copy(minHeight = 0))
    val smallPx = OrbSpec.dockSize.toPx()
    val bigPx = OrbSpec.heroSize.toPx()
    val brandPx = OrbSpec.wordmarkHeight.toPx()
    // home: centred on the trailing slot, vertically centred on the brand row
    val x0 = w - smallPx / 2f
    val y0 = brandPx / 2f
    // hero: centred horizontally, below the brand row
    val x1 = w / 2f
    val y1 = brandPx + OrbDockRef.HERO_BRAND_GAP_DP.dp.toPx() + bigPx / 2f
    // quadratic arc bowing toward the leading edge so the orb swings in
    val cxp = (x0 + x1) / 2f - OrbSpec.ARC_BOW_DP.dp.toPx()
    val cyp = (y0 + y1) / 2f
    val bx = (1 - p) * (1 - p) * x0 + 2 * (1 - p) * p * cxp + p * p * x1
    val by = (1 - p) * (1 - p) * y0 + 2 * (1 - p) * p * cyp + p * p * y1
    val overlap = OrbDockRef.HERO_TEXT_OVERLAP_DP.dp.toPx()
    val textTop = (y1 + bigPx / 2f - overlap).roundToInt()
    layout(w, constraints.maxHeight.coerceAtMost(rowH.roundToPx())) {
        brandPl.place(0, 0)
        socketPl.place(
            (x0 - socketPl.width / 2f).roundToInt(),
            (y0 - socketPl.height / 2f).roundToInt()
        )
        orbPl.place(
            (bx - orbPl.width / 2f).roundToInt(),
            (by - orbPl.height / 2f).roundToInt()
        )
        ctaPl.place((w - ctaPl.width) / 2, textTop)
        linePl.place(
            (w - linePl.width) / 2,
            textTop + ctaPl.height + OrbDockRef.HERO_LINE_GAP_DP.dp.toPx().roundToInt()
        )
    }
}

private fun Modifier.orbGestures(
    state: VoiceOrbDockState,
    scope: kotlinx.coroutines.CoroutineScope,
    haptic: (Int) -> Unit,
    canvasPx: Float,
    bodyPx: Float
) = pointerInput(state) {
    awaitEachGesture {
        val down = awaitFirstDown()
        val centre = Offset(canvasPx / 2f, canvasPx / 2f)
        if (!orbHit(down.position, centre, bodyPx / 2f)) return@awaitEachGesture
        down.consume()
        state.motion.fire(OrbMotion.Transient.Kind.TAP)
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

private fun lerp(a: Dp, b: Dp, t: Float): Dp = a + (b - a) * t
private fun easeInOut(x: Float) = if (x < OrbDockRef.EASE_HALF) {
    OrbDockRef.EASE_CUBIC * x * x * x
} else {
    1 - (2 - 2 * x).pow(OrbDockRef.EASE_EXPONENT) / 2
}
