package app.ovrly.ui.voice

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private const val ERROR_BRIGHTNESS = .5f
private const val FINISHING_BRIGHTNESS = .92f
private const val FINISHING_SATURATION = .85f
private const val GLOW_CONNECTING = .30f
private const val GLOW_ERROR = .25f
private const val GLOW_FINISHING = .28f
private const val GLOW_IDLE = .18f
private const val GLOW_LISTENING = .40f
private const val GLOW_MUTED = .15f
private const val GLOW_SPEAKING = .40f
private const val GLOW_THINKING = .35f
private const val GREY_ARGB = 0xFFA0A5B4
private const val ICE_ARGB = 0xFF9FD0FF
private const val LAVENDER_ARGB = 0xFFB8A9E6
private const val MINT_ARGB = 0xFF9FE3C3
private const val MUTED_BRIGHTNESS = .8f
private const val MUTED_SATURATION = .5f
private const val RED_ARGB = 0xFFFF7A7A

/**
 * Every tunable the orb renderer and dock read. Values are the ones settled in the HTML
 * playground (`orb.js`, `orb_dock.html`); keep them here rather than inline so the Kotlin port
 * can be compared against the reference frame by frame.
 */
internal object OrbSpec {
    // ---- sizes ----
    val dockSize = 48.dp // collapsed orb body in the brand row
    val heroSize = 240.dp // expanded orb body
    val wordmarkHeight = 44.dp

    /** Below this body size the renderer switches to the compact ring styles. */
    val compactBelow = 120.dp

    // ---- palette (state tints; chrome body is never tinted) ----
    val ice = Color(ICE_ARGB)
    val lavender = Color(LAVENDER_ARGB)
    val mint = Color(MINT_ARGB)
    val grey = Color(GREY_ARGB)
    val red = Color(RED_ARGB)

    fun tint(phase: OrbPhase): Color = when (phase) {
        OrbPhase.IDLE, OrbPhase.MUTED -> grey
        OrbPhase.CONNECTING -> ice
        OrbPhase.LISTENING -> lavender
        OrbPhase.THINKING, OrbPhase.SPEAKING, OrbPhase.FINISHING -> mint
        OrbPhase.ERROR -> red
    }

    fun glowBase(phase: OrbPhase): Float = when (phase) {
        OrbPhase.IDLE -> GLOW_IDLE
        OrbPhase.CONNECTING -> GLOW_CONNECTING
        OrbPhase.LISTENING -> GLOW_LISTENING
        OrbPhase.THINKING -> GLOW_THINKING
        OrbPhase.SPEAKING -> GLOW_SPEAKING
        OrbPhase.FINISHING -> GLOW_FINISHING
        OrbPhase.MUTED -> GLOW_MUTED
        OrbPhase.ERROR -> GLOW_ERROR
    }

    /** saturation, brightness applied to the chrome body. */
    fun bodyFilter(phase: OrbPhase): Pair<Float, Float> = when (phase) {
        OrbPhase.MUTED -> MUTED_SATURATION to MUTED_BRIGHTNESS
        OrbPhase.ERROR -> 0f to ERROR_BRIGHTNESS
        OrbPhase.FINISHING -> FINISHING_SATURATION to FINISHING_BRIGHTNESS
        else -> 1f to 1f
    }

    // ---- geometry, as fractions of the orb body radius ----
    const val RING_RADIUS_HERO = 1.16f
    const val RING_RADIUS_COMPACT = 1.22f
    const val LISTEN_ARC_INNER = 1.22f
    const val LISTEN_ARC_OUTER = 1.42f
    const val GLOW_RADIUS = 1.35f
    const val GLYPH_FRACTION_HERO = 0.28f
    const val GLYPH_FRACTION_COMPACT = 0.36f
    const val STROKE_COMPACT_MULTIPLIER = 2.2f
    const val LISTEN_TICKS_HERO = 36

    // ---- timing (ms) ----
    const val PHASE_TRANSITION_MS = 420
    const val IDLE_HERO_TIMEOUT_MS = 6_000L
    const val ERROR_HERO_TIMEOUT_MS = 4_000L
    const val SILENCE_WINDOW_MS = 15_000L
    const val SILENCE_FADE_MS = 4_000L
    const val HOLD_THRESHOLD_MS = 400L

    // ---- dock motion ----
    const val OPEN_DAMPING = 0.62f
    const val CLOSE_DAMPING = 1.0f
    const val ARC_BOW_DP = 40f // how far the travel path bows away from the straight line
    const val SIZE_LAG_EXPONENT = 1.3f // size follows position^1.3: the orb arrives, then fills
    const val CONTENT_DIM = 0.30f // page content opacity drop while the hero is open
}

/** What the renderer draws. Engine phases plus the UI-derived Muted. */
internal enum class OrbPhase {
    IDLE,
    CONNECTING,
    LISTENING,
    THINKING,
    SPEAKING,
    FINISHING,
    MUTED,
    ERROR
}

internal fun OrbPhase.isLive() = this == OrbPhase.CONNECTING || this == OrbPhase.LISTENING ||
    this == OrbPhase.THINKING || this == OrbPhase.SPEAKING || this == OrbPhase.FINISHING

internal fun VoiceInteractionPhase.toOrbPhase(muted: Boolean): OrbPhase = when {
    muted && this == VoiceInteractionPhase.IDLE -> OrbPhase.MUTED

    else -> when (this) {
        VoiceInteractionPhase.IDLE -> OrbPhase.IDLE
        VoiceInteractionPhase.CONNECTING -> OrbPhase.CONNECTING
        VoiceInteractionPhase.LISTENING -> OrbPhase.LISTENING
        VoiceInteractionPhase.THINKING -> OrbPhase.THINKING
        VoiceInteractionPhase.SPEAKING -> OrbPhase.SPEAKING
        VoiceInteractionPhase.FINISHING -> OrbPhase.FINISHING
        VoiceInteractionPhase.ERROR -> OrbPhase.ERROR
    }
}
