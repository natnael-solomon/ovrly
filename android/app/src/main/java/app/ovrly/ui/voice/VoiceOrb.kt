package app.ovrly.ui.voice

import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.imageResource
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import app.ovrly.R
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

private const val BURST_REACH = 1.8f
private const val BURST_S = .85f
private const val BURST_STROKE_GAIN = 4f
private const val BURST_STROKE_MIN = 3f
private const val CONNECTING_ALPHA = .95f
private const val CONNECTING_ARCS = 3
private const val CONNECTING_ARC_STEP = 120f
private const val CONNECTING_ARC_SWEEP = 72f
private const val CONNECTING_SPIN_S = 1.6f
private const val CONNECTING_STROKE = 4f
private const val CROWN_ALPHA_LEVEL = .45f
private const val CROWN_ALPHA_MIN = .55f
private const val CROWN_JITTER_HZ = 7f
private const val CROWN_JITTER_MID = .5f
private const val CROWN_LEN_JITTER = .65f
private const val CROWN_LEN_LEVEL = .35f
private const val CROWN_LEN_MIN = .22f
private const val CROWN_LEN_SCALE = .30f
private const val CROWN_TICK_PHASE = 1.7f
private const val CROWN_TICK_STROKE = 3.5f
private const val DEG_PER_RAD = 57.3f
private const val EASE_OUT_POWER = 3
private const val ERROR_ALPHA = .75f
private const val ERROR_BLINK_DIM = .3f
private const val ERROR_BLINK_RATE = 40f
private const val ERROR_BLINK_S = .35f
private const val FINISHING_ALPHA = .55f
private const val FRAME_RATE = 60f
private const val FULL_CIRCLE = 360f
private const val GLOW_CORE_STOP = .6f
private const val GLOW_LEVEL_GAIN = .35f
private const val GLOW_LIGHT_BOOST = 1.5f
private const val GLOW_RADIUS_LEVEL_GAIN = .35f
private const val GLYPH_DARK = 0xFF1C1E26
private const val GLYPH_IN_START = .85f
private const val GLYPH_LEVEL_GAIN = .45f
private const val GLYPH_LEVEL_MIN = .55f
private const val GLYPH_LIGHT_RGB = 0xEBEDF5
private const val GLYPH_SHADOW_OFFSET = 1.6f
private const val GLYPH_SHRINK = .15f
private const val HAIRLINE_EXTRA = 2.4f
private const val HALF_CIRCLE = 180f
private const val HOLD_RING_SCALE = 1.08f
private const val HOLD_STROKE = 3.5f
private const val IDLE_BREATHE = .03f
private const val IDLE_BREATHE_HZ = 1.5f
private const val IDLE_RING_ALPHA = .55f
private const val INK_HAIRLINE = 0x38141620
private const val INK_SHADOW = 0x59141620
private const val INTERRUPT_S = .45f
private const val INTERRUPT_TILT = .14f
private const val LEVEL_EASE = .2f
private const val LISTEN_ALPHA = .95f
private const val LISTEN_JITTER = 0.04f
private const val LISTEN_JITTER_HZ = 9f
private const val LISTEN_OUTER_ALPHA = .85f
private const val LISTEN_OUTER_MIN = .02f
private const val LISTEN_OUTER_RANGE = .60f
private const val LISTEN_OUTER_SPAN = .7f
private const val LISTEN_OUTER_STROKE = .8f
private const val LISTEN_OUTER_THRESHOLD = .40f
private const val LISTEN_SPAN_LEVEL_GAIN = 0.20f
private const val LISTEN_SPAN_MIN = 0.22f
private const val LISTEN_STROKE_LEVEL_GAIN = 1.5f
private const val LISTEN_STROKE_MIN = 4f
private const val LONG_AGO_S = 10f
private const val MAX_FRAME_S = .05f
private const val MIN_VISIBLE_ALPHA = .01f
private const val MS_PER_S = 1000f
private const val MUTED_ALPHA = .7f
private const val MUTED_DASH = 10f
private const val MUTED_GAP = 12f
private const val MUTED_SPIN_S = 14f
private const val NEARLY_ONE = .999f
private const val NS_PER_S = 1e9f
private const val ONSET_S = .7f
private const val ONSET_START_SCALE = 1.55f
private const val ORB_HIT_SCALE = 1.15f
private const val PRESS_DECAY = .20f
private const val PRESS_SHRINK = .08f
private const val QUARTER_TURN = 90f
private const val REFERENCE_RADIUS_PX = 280f
private const val RING_STROKE = 3f
private const val SETTLE_DECAY = .14f
private const val SETTLE_WOBBLE = .035f
private const val SETTLE_WOBBLE_CYCLES = 2.2f
private const val SHAKE_DECAY = .18f
private const val SHAKE_PX = 10f
private const val SHAKE_RATE = 60f
private const val SHAKE_S = .3f
private const val SNAP_S = .28f
private const val SNAP_SHRINK = .25f
private const val SNAP_START_SCALE = 1.25f
private const val SNAP_STROKE = 4f
private const val SPEAK_ALPHA_LEVEL_GAIN = .35f
private const val SPEAK_ALPHA_MIN = .65f
private const val SPEAK_STROKE_LEVEL_GAIN = 9f
private const val SPEAK_STROKE_MIN = 2.5f
private const val SPEAK_SWELL = .10f
private const val TAP_S = .4f
private const val THINK_ALPHA = .85f
private const val THINK_CORE_HZ = 2.6f
private const val THINK_CORE_MID = .5f
private const val THINK_CORE_MIN = .42f
private const val THINK_CORE_RANGE = .14f
private const val THINK_CORE_STOP = .45f
private const val THINK_DASH_MAX = 26f
private const val THINK_DASH_RANGE = 10f
private const val THINK_GAP_MIN = 8f
private const val THINK_GAP_RANGE = 14f
private const val THINK_SPIN_DEG_S = 20f
private const val THINK_WAVE_HZ = 1.3f
private const val THINK_WAVE_MID = .5f
private const val TILT_DECAY_PER_FRAME = .045f
private const val TRANSIENT_CAPACITY = 8
private const val TRANSITION_SCALE = .06f
private const val TRANSITION_SCALE_START = .94f

/**
 * Frame-driven state for one orb. The dock owns one of these; the renderer reads it in the draw
 * phase only, so phase changes, levels and events never recompose the tree. All timing is
 * wall-clock seconds, matching `orb.js`.
 */
@Stable
internal class OrbMotion {
    var phase by mutableStateOf(OrbPhase.IDLE)
        private set
    var previousPhase by mutableStateOf(OrbPhase.IDLE)
        private set
    var time by mutableFloatStateOf(0f)
        internal set
    private var transitionStart = -LONG_AGO_S

    /** Set by the dock while a push-to-talk hold is active (0..1 ring fill). */
    var holdFill by mutableFloatStateOf(0f)

    /** 0..1 progress through the silence fade-out (last 4 s before auto-stop). */
    var silenceFade by mutableFloatStateOf(0f)

    var level = 0f
        internal set
    internal var smoothLevel = 0f
    internal var settle = 0f
    internal var press = 0f
    internal var shake = 0f
    internal var tilt = 0f
    internal var errorStart = -LONG_AGO_S
    internal val transients = ArrayList<Transient>(TRANSIENT_CAPACITY)
    internal var reducedMotion = false

    internal class Transient(
        val kind: Kind,
        val start: Float,
        val color: Color,
        val strength: Float
    ) {
        enum class Kind(val durationSeconds: Float) {
            ONSET(ONSET_S),
            SNAP(SNAP_S),
            BURST(BURST_S),
            TAP(TAP_S),
            INTERRUPT(INTERRUPT_S),
            SHAKE(SHAKE_S)
        }
    }

    fun transitionTo(next: OrbPhase) {
        if (next == phase) return
        val prev = phase
        previousPhase = prev
        phase = next
        transitionStart = time
        settle = 1f
        when (next) {
            OrbPhase.MUTED -> fire(Transient.Kind.SNAP)

            OrbPhase.ERROR -> {
                fire(Transient.Kind.SHAKE)
                errorStart = time
            }

            OrbPhase.LISTENING -> if (prev == OrbPhase.SPEAKING) fire(Transient.Kind.INTERRUPT)

            else -> Unit
        }
    }

    fun fire(kind: Transient.Kind, strength: Float = 1f) {
        transients.removeAll { it.kind == kind }
        transients.add(Transient(kind, time, OrbSpec.tint(phase), strength))
        when (kind) {
            Transient.Kind.TAP -> press = 1f
            Transient.Kind.SHAKE -> shake = 1f
            Transient.Kind.INTERRUPT -> tilt = 1f
            else -> Unit
        }
    }

    fun onEvent(event: VoiceOrbEvent) = when (event) {
        VoiceOrbEvent.Snap -> fire(Transient.Kind.SNAP)
        VoiceOrbEvent.Onset -> fire(Transient.Kind.ONSET)
        is VoiceOrbEvent.Burst -> fire(Transient.Kind.BURST, event.strength.coerceIn(0f, 1f))
        VoiceOrbEvent.Interrupt -> fire(Transient.Kind.INTERRUPT)
        VoiceOrbEvent.Shake -> fire(Transient.Kind.SHAKE)
    }

    /** 0..1 eased progress of the current phase transition. */
    internal fun transition(): Float =
        easeInOut(min(1f, (time - transitionStart) / (OrbSpec.PHASE_TRANSITION_MS / MS_PER_S)))

    internal fun step(dt: Float) {
        val k = { x: Float -> 1f - (1f - x).pow(dt * FRAME_RATE) }
        val target = if (phase == OrbPhase.LISTENING || phase == OrbPhase.SPEAKING) level else 0f
        smoothLevel += (target - smoothLevel) * k(LEVEL_EASE)
        shake *= 1f - k(SHAKE_DECAY)
        tilt = max(0f, tilt - TILT_DECAY_PER_FRAME * dt * FRAME_RATE)
        press *= 1f - k(PRESS_DECAY)
        settle *= 1f - k(SETTLE_DECAY)
        transients.removeAll { time - it.start > it.kind.durationSeconds }
    }
}

/**
 * Draws one orb at the given body size. The ring/glow/glyph overhang the body, so callers should
 * give the composable ~1.5× the body as its layout size; the orb is centred in whatever it gets.
 */
@Composable
internal fun VoiceOrb(
    motion: OrbMotion,
    bodySize: Dp,
    light: Boolean,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val chrome = ImageBitmap.imageResource(R.drawable.orb_chrome)
    val mic = rememberVectorPainter(OrbGlyphs.microphone)
    val micSlash = rememberVectorPainter(OrbGlyphs.microphoneSlash)
    val warning = rememberVectorPainter(OrbGlyphs.warningCircle)
    val bodyPx = with(LocalDensity.current) { bodySize.toPx() }
    val compact = bodySize < OrbSpec.compactBelow

    LaunchedEffect(motion) {
        // Mirror chromeGlare: a zero animator scale freezes continuous loops but keeps transitions.
        motion.reducedMotion = Settings.Global.getFloat(
            context.contentResolver,
            Settings.Global.ANIMATOR_DURATION_SCALE,
            1f
        ) == 0f
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                if (last == 0L) last = now
                val dt = min(MAX_FRAME_S, (now - last) / NS_PER_S)
                last = now
                motion.time += dt
                motion.step(dt)
            }
        }
    }

    Canvas(modifier) {
        val painterFor = { p: OrbPhase ->
            when (OrbGlyphs.forPhase(p)) {
                OrbGlyphs.microphone -> mic
                OrbGlyphs.microphoneSlash -> micSlash
                OrbGlyphs.warningCircle -> warning
                else -> null
            }
        }
        OrbPainter(this, motion, OrbMetrics(bodyPx / 2f, compact, light), chrome, painterFor).draw()
    }
}

private class OrbMetrics(val radius: Float, val compact: Boolean, val light: Boolean)

/** Shared geometry and stroke helpers; the ring and body layers draw on top of it. */
private abstract class OrbLayer(
    protected val scope: DrawScope,
    protected val m: OrbMotion,
    metrics: OrbMetrics
) {
    protected val r = metrics.radius
    protected val compact = metrics.compact
    protected val light = metrics.light
    protected val cx = scope.size.width / 2f
    protected val cy = scope.size.height / 2f
    protected val c = Offset(cx, cy)
    protected val k = if (compact) OrbSpec.STROKE_COMPACT_MULTIPLIER else 1f

    // orb.js draws at R=280 canvas px; scale stroke widths to match
    protected val px = r / REFERENCE_RADIUS_PX
    protected val ring =
        r * if (compact) OrbSpec.RING_RADIUS_COMPACT else OrbSpec.RING_RADIUS_HERO
    protected val t = m.time
    protected val p = m.transition()
    protected val lvl = m.smoothLevel
    protected val still = m.reducedMotion

    private fun stroke(w: Float, dash: FloatArray? = null) = Stroke(
        width = w * k * px,
        cap = StrokeCap.Round,
        pathEffect = dash?.let {
            PathEffect.dashPathEffect(FloatArray(it.size) { i -> it[i] * k * px })
        }
    )

    private fun drawArc(radius: Float, start: Float, sweep: Float, color: Color, style: Stroke) {
        scope.drawArc(
            color,
            start,
            sweep,
            false,
            topLeft = Offset(cx - radius, cy - radius),
            size = Size(radius * 2, radius * 2),
            style = style
        )
    }

    protected fun arc(radius: Float, start: Float, sweep: Float, w: Float, color: Color) =
        drawArc(radius, start, sweep, color, stroke(w))

    /** Coloured arc with an ink hairline underneath on light paper so it separates from the page. */
    protected fun ringArc(radius: Float, start: Float, sweep: Float, w: Float, color: Color) {
        if (light) arc(radius, start, sweep, w + HAIRLINE_EXTRA / k, Color(INK_HAIRLINE))
        arc(radius, start, sweep, w, color)
    }

    /** Full dashed ring, with the same light-paper hairline as [ringArc]. */
    protected fun dashedRing(radius: Float, w: Float, color: Color, dash: FloatArray) {
        if (light) {
            drawArc(
                radius,
                0f,
                FULL_CIRCLE,
                Color(INK_HAIRLINE),
                stroke(w + HAIRLINE_EXTRA / k, dash)
            )
        }
        drawArc(radius, 0f, FULL_CIRCLE, color, stroke(w, dash))
    }
}

/** Phase rings plus the silence-fade and push-to-talk overlays. */
private class OrbRings(scope: DrawScope, m: OrbMotion, metrics: OrbMetrics) :
    OrbLayer(scope, m, metrics) {
    fun draw(phase: OrbPhase, alpha: Float, scale: Float) {
        if (alpha < MIN_VISIBLE_ALPHA) return
        val col = OrbSpec.tint(phase)
        val a = { v: Float -> col.copy(alpha = (v * alpha).coerceIn(0f, 1f)) }
        val tiltDeg = (m.tilt * INTERRUPT_TILT * sin(m.tilt * PI.toFloat())) * DEG_PER_RAD
        scope.withTransform({
            scale(scale, scale, c)
            rotate(tiltDeg, c)
        }) {
            phaseRing(phase, alpha, a)
            overlays(phase, alpha)
        }
    }

    private fun phaseRing(phase: OrbPhase, alpha: Float, a: (Float) -> Color) {
        when (phase) {
            OrbPhase.IDLE -> idleRing(alpha, a)

            OrbPhase.CONNECTING -> connectingRing(a)

            OrbPhase.LISTENING -> if (compact) listeningArcs(a) else listeningCrown(a)

            OrbPhase.THINKING -> thinkingRing(a)

            OrbPhase.SPEAKING -> ringArc(
                ring,
                0f,
                FULL_CIRCLE,
                SPEAK_STROKE_MIN + lvl * SPEAK_STROKE_LEVEL_GAIN,
                a(SPEAK_ALPHA_MIN + lvl * SPEAK_ALPHA_LEVEL_GAIN)
            )

            // Speaking's ring without level: steady weight, dimmed; the mic is off, the reply lands.
            OrbPhase.FINISHING -> ringArc(ring, 0f, FULL_CIRCLE, RING_STROKE, a(FINISHING_ALPHA))

            OrbPhase.MUTED -> mutedRing(a)

            OrbPhase.ERROR -> errorRing(a)
        }
    }

    private fun idleRing(alpha: Float, a: (Float) -> Color) = arc(
        ring,
        0f,
        FULL_CIRCLE,
        2f,
        if (light) {
            Color(INK_SHADOW).copy(
                alpha =
                    alpha * .35f / .35f
            )
        } else {
            a(IDLE_RING_ALPHA)
        }
    )

    private fun connectingRing(a: (Float) -> Color) {
        val rot = if (still) 0f else (t * FULL_CIRCLE / CONNECTING_SPIN_S) % FULL_CIRCLE
        repeat(CONNECTING_ARCS) { i ->
            ringArc(
                ring,
                rot + i * CONNECTING_ARC_STEP,
                CONNECTING_ARC_SWEEP,
                CONNECTING_STROKE,
                a(CONNECTING_ALPHA)
            )
        }
    }

    private fun thinkingRing(a: (Float) -> Color) {
        val w = THINK_WAVE_MID + THINK_WAVE_MID * sin(t * THINK_WAVE_HZ)
        val gap = THINK_GAP_MIN + THINK_GAP_RANGE * w
        val dash = THINK_DASH_MAX - THINK_DASH_RANGE * w
        scope.rotate(if (still) 0f else -t * THINK_SPIN_DEG_S, c) {
            dashedRing(ring, RING_STROKE, a(THINK_ALPHA), floatArrayOf(dash, gap))
        }
    }

    private fun mutedRing(a: (Float) -> Color) {
        scope.rotate(if (still) 0f else t * FULL_CIRCLE / MUTED_SPIN_S, c) {
            dashedRing(ring, RING_STROKE, a(MUTED_ALPHA), floatArrayOf(MUTED_DASH, MUTED_GAP))
        }
    }

    private fun errorRing(a: (Float) -> Color) {
        val age = t - m.errorStart
        val blink = if (age < ERROR_BLINK_S && !still) {
            (if (sin(age * ERROR_BLINK_RATE) > 0) 1f else ERROR_BLINK_DIM)
        } else {
            1f
        }
        ringArc(ring, 0f, FULL_CIRCLE, RING_STROKE, a(ERROR_ALPHA * blink))
    }

    private fun overlays(phase: OrbPhase, alpha: Float) {
        // silence fade: ring colour drains toward idle grey over the last seconds
        if (m.silenceFade > 0f && phase == OrbPhase.LISTENING) {
            arc(
                ring,
                0f,
                FULL_CIRCLE,
                2f,
                OrbSpec.grey.copy(
                    alpha =
                        alpha * .55f * m.silenceFade
                )
            )
        }
        // push-to-talk hold: a fill ring grows clockwise from the top
        if (m.holdFill > 0f) {
            ringArc(
                ring * HOLD_RING_SCALE,
                -QUARTER_TURN,
                FULL_CIRCLE * m.holdFill,
                HOLD_STROKE,
                OrbSpec.lavender.copy(
                    alpha =
                        alpha * .95f
                )
            )
        }
    }

    /** Compact Listening: mirrored waveform arcs `( o )`, span and weight follow the level. */
    private fun listeningArcs(a: (Float) -> Color) {
        val r1 = r * OrbSpec.LISTEN_ARC_INNER
        val r2 = r * OrbSpec.LISTEN_ARC_OUTER
        val span = (LISTEN_SPAN_MIN + LISTEN_SPAN_LEVEL_GAIN * lvl) * HALF_CIRCLE
        val w = (LISTEN_STROKE_MIN + LISTEN_STROKE_LEVEL_GAIN * lvl)
        val jit = if (still) 0f else LISTEN_JITTER * sin(t * LISTEN_JITTER_HZ) * lvl * DEG_PER_RAD
        ringArc(r1, HALF_CIRCLE - span - jit, 2 * (span + jit), w, a(LISTEN_ALPHA))
        ringArc(r1, -span + jit, 2 * (span - jit), w, a(LISTEN_ALPHA))
        val outer = max(0f, (lvl - LISTEN_OUTER_THRESHOLD) / LISTEN_OUTER_RANGE)
        if (outer > LISTEN_OUTER_MIN) {
            val s2 = span * LISTEN_OUTER_SPAN
            ringArc(
                r2,
                HALF_CIRCLE - s2,
                2 * s2,
                w * LISTEN_OUTER_STROKE,
                a(LISTEN_OUTER_ALPHA * outer)
            )
            ringArc(r2, -s2, 2 * s2, w * LISTEN_OUTER_STROKE, a(LISTEN_OUTER_ALPHA * outer))
        }
    }

    /** Hero Listening: tick crown, ticks grow inward with level (receiving). */
    private fun listeningCrown(a: (Float) -> Color) {
        val n = OrbSpec.LISTEN_TICKS_HERO
        val rr = r * OrbSpec.RING_RADIUS_HERO
        val w = CROWN_TICK_STROKE * px
        for (i in 0 until n) {
            val ang = i.toFloat() / n * 2f * PI.toFloat() - PI.toFloat() / 2f
            val jitter = if (still) {
                CROWN_JITTER_MID
            } else {
                CROWN_JITTER_MID +
                    CROWN_JITTER_MID * sin(t * CROWN_JITTER_HZ + i * CROWN_TICK_PHASE)
            }
            val len =
                (CROWN_LEN_MIN + lvl * (CROWN_LEN_LEVEL + CROWN_LEN_JITTER * jitter)) * r *
                    CROWN_LEN_SCALE
            val p0 = Offset(cx + cos(ang) * rr, cy + sin(ang) * rr)
            val p1 = Offset(cx + cos(ang) * (rr - len), cy + sin(ang) * (rr - len))
            if (light) {
                scope.drawLine(
                    Color(INK_HAIRLINE),
                    p0,
                    p1,
                    w + HAIRLINE_EXTRA * px,
                    StrokeCap.Round
                )
            }
            scope.drawLine(a(CROWN_ALPHA_MIN + lvl * CROWN_ALPHA_LEVEL), p0, p1, w, StrokeCap.Round)
        }
    }
}

private class OrbPainter(
    scope: DrawScope,
    m: OrbMotion,
    metrics: OrbMetrics,
    private val chrome: ImageBitmap,
    private val glyphFor: (OrbPhase) -> androidx.compose.ui.graphics.painter.Painter?
) : OrbLayer(scope, m, metrics) {
    private val rings = OrbRings(scope, m, metrics)

    fun draw() {
        glow()
        transients()
        if (p < 1f) {
            rings.draw(m.previousPhase, 1f - p, 1f - TRANSITION_SCALE * p)
            rings.draw(m.phase, p, TRANSITION_SCALE_START + TRANSITION_SCALE * p)
        } else {
            rings.draw(m.phase, 1f, 1f)
        }
        body()
        thinkingCore()
        glyph()
    }

    private fun glow() {
        val col = lerp(OrbSpec.tint(m.previousPhase), OrbSpec.tint(m.phase), p)
        val base = OrbSpec.glowBase(m.previousPhase) * (1 - p) + OrbSpec.glowBase(m.phase) * p
        val alpha = ((base + lvl * GLOW_LEVEL_GAIN) * if (light) GLOW_LIGHT_BOOST else 1f).coerceIn(
            0f,
            1f
        )
        val gr = r * (OrbSpec.GLOW_RADIUS + lvl * GLOW_RADIUS_LEVEL_GAIN)
        scope.drawCircle(
            Brush.radialGradient(
                0f to col.copy(alpha = alpha),
                (r * GLOW_CORE_STOP / gr) to col.copy(alpha = alpha),
                1f to col.copy(alpha = 0f),
                center = c,
                radius = gr
            ),
            radius = gr,
            center = c
        )
    }

    private fun transients() {
        for (tr in m.transients) {
            val age = t - tr.start
            val prog = (age / tr.kind.durationSeconds).coerceIn(0f, 1f)
            val e = 1f - (1f - prog).pow(EASE_OUT_POWER)
            when (tr.kind) {
                OrbMotion.Transient.Kind.ONSET -> {
                    val rad = ring * ONSET_START_SCALE - (ring * ONSET_START_SCALE - r) * e
                    arc(rad, 0f, FULL_CIRCLE, RING_STROKE, tr.color.copy(alpha = .9f * (1 - prog)))
                }

                OrbMotion.Transient.Kind.SNAP -> {
                    val rad = ring * SNAP_START_SCALE - ring * SNAP_SHRINK * e
                    arc(
                        rad,
                        0f,
                        FULL_CIRCLE,
                        SNAP_STROKE * (1 - prog) + 1f,
                        tr.color.copy(
                            alpha =
                                .95f * (1 - prog * .6f)
                        )
                    )
                }

                OrbMotion.Transient.Kind.BURST -> {
                    val rad = r + (ring * BURST_REACH - r) * e * tr.strength
                    arc(
                        rad,
                        0f,
                        FULL_CIRCLE,
                        BURST_STROKE_MIN + BURST_STROKE_GAIN * tr.strength * (1 - prog),
                        tr.color.copy(
                            alpha =
                                .85f * (1 - prog)
                        )
                    )
                }

                else -> Unit
            }
        }
    }

    private fun body() {
        val breathe = if (m.phase == OrbPhase.IDLE &&
            !still
        ) {
            1f + IDLE_BREATHE * sin(t * IDLE_BREATHE_HZ)
        } else {
            1f
        }
        val settleS =
            1f +
                SETTLE_WOBBLE * m.settle *
                sin((1f - m.settle) * PI.toFloat() * SETTLE_WOBBLE_CYCLES)
        val speak = if (m.phase == OrbPhase.SPEAKING) 1f + lvl * SPEAK_SWELL else 1f
        val s = breathe * settleS * speak * (1f - m.press * PRESS_SHRINK)
        val dx = if (still) 0f else sin(t * SHAKE_RATE) * m.shake * SHAKE_PX * px
        val (sat0, bri0) = OrbSpec.bodyFilter(m.previousPhase)
        val (sat1, bri1) = OrbSpec.bodyFilter(m.phase)
        val sat = sat0 * (1 - p) + sat1 * p
        val bri = bri0 * (1 - p) + bri1 * p
        val filter = if (sat < NEARLY_ONE || bri < NEARLY_ONE) {
            ColorFilter.colorMatrix(
                ColorMatrix().apply {
                    setToSaturation(sat)
                    val m2 = ColorMatrix().apply { setToScale(bri, bri, bri, 1f) }
                    timesAssign(m2)
                }
            )
        } else {
            null
        }
        scope.withTransform({
            translate(dx, 0f)
            scale(s, s, c)
        }) {
            drawImage(
                chrome,
                dstOffset = IntOffset((cx - r).toInt(), (cy - r).toInt()),
                dstSize = IntSize((2 * r).toInt(), (2 * r).toInt()),
                colorFilter = filter
            )
        }
    }

    private fun thinkingCore() {
        val a =
            (if (m.phase == OrbPhase.THINKING) p else 0f) +
                (if (m.previousPhase == OrbPhase.THINKING) 1 - p else 0f)
        if (a < MIN_VISIBLE_ALPHA) return
        val kk = if (still) {
            THINK_CORE_MID
        } else {
            THINK_CORE_MID +
                THINK_CORE_MID * sin(t * THINK_CORE_HZ)
        }
        val cr = r * (THINK_CORE_MIN + THINK_CORE_RANGE * kk)
        scope.drawCircle(
            Brush.radialGradient(
                0f to OrbSpec.mint.copy(alpha = .95f * a),
                THINK_CORE_STOP to OrbSpec.mint.copy(alpha = .5f * a),
                1f to OrbSpec.mint.copy(alpha = 0f),
                center = c,
                radius = cr
            ),
            radius = cr,
            center = c,
            blendMode = BlendMode.Screen
        )
    }

    private fun glyph() {
        val cur = OrbGlyphs.forPhase(m.phase)
        val prev = OrbGlyphs.forPhase(m.previousPhase)
        val breathe = 1f - m.press * PRESS_SHRINK
        if (prev != null && prev != cur &&
            p < 1f
        ) {
            glyphStyled(m.previousPhase, 1 - p, (1f - GLYPH_SHRINK * p) * breathe)
        }
        if (cur != null) {
            if (prev == cur && p < 1f) {
                glyphStyled(m.previousPhase, 1 - p, breathe)
                glyphStyled(m.phase, p, breathe)
            } else {
                glyphStyled(
                    m.phase,
                    if (prev ==
                        cur
                    ) {
                        1f
                    } else {
                        p
                    },
                    (if (prev == cur) 1f else GLYPH_IN_START + GLYPH_SHRINK * p) * breathe
                )
            }
        }
    }

    private fun glyphStyled(phase: OrbPhase, alpha: Float, scale: Float) {
        if (alpha < MIN_VISIBLE_ALPHA) return
        val painter = glyphFor(phase) ?: return
        val fraction = if (compact) OrbSpec.GLYPH_FRACTION_COMPACT else OrbSpec.GLYPH_FRACTION_HERO
        val size = r * 2f * fraction * scale
        val dx = if (still) 0f else sin(t * SHAKE_RATE) * m.shake * SHAKE_PX * px
        fun draw(color: Color, dy: Float) {
            scope.translate(cx - size / 2 + dx, cy - size / 2 + dy * px) {
                with(painter) {
                    draw(Size(size, size), alpha = alpha, colorFilter = ColorFilter.tint(color))
                }
            }
        }
        when (phase) {
            OrbPhase.LISTENING -> {
                // lit from within: lavender bloom follows the mic level, white-hot core
                val kk = GLYPH_LEVEL_MIN + GLYPH_LEVEL_GAIN * lvl
                draw(OrbSpec.lavender.copy(alpha = kk), 0f)
                draw(Color.White.copy(alpha = .75f + .25f * lvl), 0f)
            }

            OrbPhase.ERROR -> {
                draw(Color.Black.copy(alpha = .55f), GLYPH_SHADOW_OFFSET)
                draw(OrbSpec.red.copy(alpha = .95f), 0f)
            }

            OrbPhase.MUTED -> {
                draw(Color.Black.copy(alpha = .55f), GLYPH_SHADOW_OFFSET)
                draw(Color(GLYPH_LIGHT_RGB).copy(alpha = .92f), 0f)
            }

            else -> {
                draw(Color.White.copy(alpha = .45f), GLYPH_SHADOW_OFFSET)
                draw(Color(GLYPH_DARK).copy(alpha = .92f), 0f)
            }
        }
    }
}

private fun easeInOut(x: Float) = if (x < EASE_MID) {
    EASE_IN_GAIN * x * x * x
} else {
    1 - (2 - 2 * x).pow(EASE_EXPONENT) / 2
}

private const val EASE_MID = .5f
private const val EASE_IN_GAIN = 4
private const val EASE_EXPONENT = 3

/** Hit test helper for the dock: is [point] inside the orb body (with a little slack)? */
internal fun orbHit(point: Offset, center: Offset, bodyRadius: Float) =
    hypot(point.x - center.x, point.y - center.y) <= bodyRadius * ORB_HIT_SCALE
