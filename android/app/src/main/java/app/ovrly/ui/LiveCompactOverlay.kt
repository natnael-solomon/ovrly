package app.ovrly.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ovrly.R
import app.ovrly.overlay.LiveOverlayForm
import app.ovrly.overlay.LivePanelState
import app.ovrly.overlay.LiveResults
import app.ovrly.overlay.LiveSessionPhase

/** What the live overlay shows: live results, panel interaction state and source label. */
@Immutable
internal data class LiveOverlayModel(
    val results: LiveResults,
    val panel: LivePanelState = LivePanelState(),
    /** Non-null for fixture sources, so their content is never mistaken for research. */
    val sourceLabel: String? = null,
    /** The running capture's timer and Stop; null after Stop. */
    val examining: ExaminingState? = null
)

/**
 * Panel callbacks, wired by the host to `LivePanelController`. Only [onStopChoice] may stop
 * capture, and only after the user picked one of the two Stop answers.
 */
@Immutable
internal data class LivePanelActions(
    val onExpand: () -> Unit = {},
    val onCollapse: () -> Unit = {},
    /** Hides the overlay after Stop; research and capture are untouched. */
    val onDismiss: () -> Unit = {},
    val onOpenClaim: (String) -> Unit = {},
    val onCloseClaim: () -> Unit = {},
    val onDismissNotice: (String) -> Unit = {},
    val onRequestStop: () -> Unit = {},
    val onStopChoice: (Boolean) -> Unit = {},
    val onCancelStop: () -> Unit = {},
    /** Height of the expanded panel's header plus its top padding: the window's drag area. */
    val onHeaderHeight: (Int) -> Unit = {}
) {
    companion object {
        val None = LivePanelActions()
    }
}

/**
 * The live overlay in its current [form]: the examining pill (with the Stop choice under it
 * when asked), the expanded panel, the idle bubble after Stop, or the short "Saved to Inbox"
 * pill. Forms change with a short fade and scale, or at once when [LivePanelFrame.animate] is
 * false (the phone's animations are off).
 */
@Composable
internal fun LiveOverlay(
    form: LiveOverlayForm,
    model: LiveOverlayModel,
    actions: LivePanelActions,
    modifier: Modifier = Modifier,
    frame: LivePanelFrame = LivePanelFrame()
) {
    val examining = model.examining
    AnimatedContent(
        targetState = form,
        modifier = modifier,
        contentAlignment = Alignment.TopStart,
        transitionSpec = {
            // Forms grow from and shrink into the pill's corner, where the window stays. Only
            // scale and fade animate (drawn on the GPU); the size never animates, because each
            // size step relays out the overlay window, which showed as jitter on the phone.
            // Growing takes the new size at once; shrinking keeps the old size until the exit
            // has faded, then takes the new one.
            if (frame.animate) {
                val corner = TransformOrigin(0f, 0f)
                val enter = fadeIn(tween(FORM_MS, delayMillis = FORM_MS / 3)) +
                    scaleIn(tween(FORM_MS, easing = FastOutSlowInEasing), FORM_START_SCALE, corner)
                val exit = fadeOut(tween(FORM_EXIT_MS)) +
                    scaleOut(tween(FORM_EXIT_MS), FORM_END_SCALE, corner)
                (enter togetherWith exit).using(
                    SizeTransform(clip = false) { initial, target ->
                        if (target.width * target.height >= initial.width * initial.height) {
                            snap()
                        } else {
                            snap(delayMillis = FORM_EXIT_MS)
                        }
                    }
                )
            } else {
                fadeIn(snap()).togetherWith(fadeOut(snap()))
            }
        },
        label = "liveOverlayForm"
    ) { shown ->
        val claims = model.results.claims.size
        when (shown) {
            LiveOverlayForm.PILL -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ExaminingPill(
                    PillState(examining?.seconds ?: 0, claims, model.panel.unseen),
                    onExpand = actions.onExpand.takeIf { model.results.connected },
                    onStop = actions.onRequestStop.takeIf { examining?.canStop != false },
                    frame = frame
                )
                if (model.panel.stopPrompt) {
                    StopChoicePrompt(
                        actions.onStopChoice,
                        actions.onCancelStop,
                        higherOpacity = frame.higherOpacity
                    )
                }
            }

            LiveOverlayForm.EXPANDED -> LiveExpandedPanel(model, actions, frame = frame)

            LiveOverlayForm.BUBBLE -> IdleBubble(
                PillState(0, claims, model.panel.unseen),
                onExpand = actions.onExpand,
                onDismiss = actions.onDismiss,
                frame = frame
            )

            LiveOverlayForm.SAVED -> SavedPill(claims, higherOpacity = frame.higherOpacity)
        }
    }
}

private val LiveResults.connected: Boolean
    get() = phase != LiveSessionPhase.NOT_CONNECTED

/** The ovrly mark: the landing page's two-segment ring, tinted for the theme. */
@Composable
internal fun OverlayMark(
    modifier: Modifier = Modifier,
    tint: Color = LocalOvrlyPalette.current.ink
) {
    Icon(painterResource(R.drawable.ic_ovrly), contentDescription = null, modifier, tint = tint)
}

internal fun examiningLabel(seconds: Int): String =
    "Examining ${clockLabel(seconds.coerceAtLeast(0).toLong() * MS_PER_SECOND)}"

internal fun pillDescription(seconds: Int, claims: Int, unseen: Boolean): String = buildString {
    fun count(n: Int, unit: String) = "$n $unit${if (n == 1) "" else "s"}"
    val elapsed = seconds.coerceAtLeast(0)
    append("Examining, ${count(elapsed / SECONDS_PER_MINUTE, "minute")} ")
    append("${count(elapsed % SECONDS_PER_MINUTE, "second")}. ")
    append(claimLabel(claims))
    if (unseen) append(", updated")
    append('.')
}

/** What the examining pill reads: elapsed time, claim count and whether anything is unseen. */
@Immutable
internal data class PillState(val seconds: Int, val claims: Int, val unseen: Boolean = false)

/**
 * The default while examining: mark, timer, claim count and update dot, then Stop. Tapping
 * anywhere but Stop expands the panel ([onExpand] is null until live results are connected);
 * the host's window drag starts only after the touch moves past the touch slop.
 */
@Composable
internal fun ExaminingPill(
    state: PillState,
    onExpand: (() -> Unit)?,
    onStop: (() -> Unit)?,
    modifier: Modifier = Modifier,
    frame: LivePanelFrame = LivePanelFrame()
) {
    CompositionLocalProvider(LocalContentColor provides LocalOvrlyPalette.current.ink) {
        Row(
            modifier.mockGlass(frame.higherOpacity).sizeIn(minHeight = 52.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PillReadout(state, onExpand, frame.animate, Modifier.weight(1f, fill = false))
            if (onStop != null) {
                Spacer(
                    Modifier.width(
                        1.dp
                    ).height(
                        20.dp
                    ).background(LocalOvrlyPalette.current.ink.copy(alpha = DIVIDER_ALPHA))
                )
                PanelIconButton(Glyph.Stop, "Stop examining", onStop)
                Spacer(Modifier.width(2.dp))
            }
        }
    }
}

/**
 * The pill's tappable readout; TalkBack reads it as one sentence on focus. It is not a live
 * region: the timer would be announced every second.
 * It takes the width left after Stop, so Stop stays reachable at any text size.
 */
@Composable
private fun PillReadout(
    state: PillState,
    onExpand: (() -> Unit)?,
    animate: Boolean,
    modifier: Modifier = Modifier
) {
    val p = LocalOvrlyPalette.current
    val description = pillDescription(state.seconds, state.claims, state.unseen)
    val tap = if (onExpand != null) {
        Modifier.clickable(role = Role.Button, onClickLabel = EXPAND_LABEL, onClick = onExpand)
    } else {
        Modifier
    }
    Row(
        modifier
            .sizeIn(minHeight = 52.dp)
            .then(tap)
            .clearAndSetSemantics {
                contentDescription = description
                if (onExpand != null) {
                    onClick(EXPAND_LABEL) {
                        onExpand()
                        true
                    }
                }
            }
            .padding(start = 14.dp, end = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        OverlayMark(Modifier.size(20.dp))
        Spacer(Modifier.width(10.dp))
        Text(
            "Examining",
            style = MaterialTheme.typography.labelMedium,
            color = p.muted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        Spacer(Modifier.width(6.dp))
        Text(
            examiningLabel(state.seconds).removePrefix("Examining "),
            style = MaterialTheme.typography.titleSmall.copy(
                fontWeight = FontWeight.SemiBold,
                fontFeatureSettings = "tnum"
            ),
            softWrap = false
        )
        Spacer(Modifier.width(10.dp))
        Spacer(
            Modifier.width(
                1.dp
            ).height(20.dp).background(LocalOvrlyPalette.current.ink.copy(alpha = DIVIDER_ALPHA))
        )
        Spacer(Modifier.width(10.dp))
        Text(
            claimLabel(state.claims),
            modifier = Modifier.weight(1f, fill = false),
            style = MaterialTheme.typography.labelLarge,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        UpdateDot(state.unseen, animate, Modifier.padding(start = 6.dp))
    }
}

/** After Stop with research continuing: the mark and the claim count; tap to expand. */
@Composable
internal fun IdleBubble(
    state: PillState,
    onExpand: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
    frame: LivePanelFrame = LivePanelFrame()
) {
    val p = LocalOvrlyPalette.current
    val claims = state.claims
    val unseen = state.unseen
    val description = "ovrly. ${claimLabel(claims)}${if (unseen) ", updated" else ""}. " +
        "Research continues."
    Box(modifier.size(BUBBLE_DP.dp)) {
        Box(
            Modifier
                .padding(BUBBLE_INSET_DP.dp)
                .size((BUBBLE_DP - 2 * BUBBLE_INSET_DP).dp)
                .mockGlass(frame.higherOpacity)
                .clickable(role = Role.Button, onClickLabel = EXPAND_LABEL, onClick = onExpand)
                .semantics {
                    contentDescription = description
                    customActions = listOf(
                        CustomAccessibilityAction("Dismiss overlay") {
                            onDismiss()
                            true
                        }
                    )
                },
            contentAlignment = Alignment.Center
        ) {
            OverlayMark(Modifier.size(26.dp))
        }
        // One marker only: the claim count, which pulses when a result changed.
        if (claims > 0) {
            CountBadge(
                claims,
                pulse = unseen && frame.animate,
                // Inside the window's 28 dp rounded corner, which would otherwise clip it.
                Modifier.align(Alignment.TopEnd).padding(top = 7.dp, end = 6.dp)
            )
        }
    }
}

/** The bubble's claim count; it pulses twice when [pulse] turns on. */
@Composable
private fun CountBadge(count: Int, pulse: Boolean, modifier: Modifier = Modifier) {
    val p = LocalOvrlyPalette.current
    val scale = remember { Animatable(1f) }
    LaunchedEffect(pulse) {
        if (pulse) {
            repeat(2) {
                scale.animateTo(BADGE_PULSE_SCALE, tween(PULSE_MS))
                scale.animateTo(1f, tween(PULSE_MS))
            }
        }
    }
    Text(
        count.toString(),
        modifier = modifier
            .graphicsLayer {
                scaleX = scale.value
                scaleY = scale.value
            }
            .sizeIn(minWidth = 18.dp)
            .background(p.accent, CircleShape)
            .padding(horizontal = 5.dp, vertical = 1.dp)
            .clearAndSetSemantics { },
        style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
        color = p.accentInk,
        fontWeight = FontWeight.SemiBold,
        textAlign = TextAlign.Center
    )
}

/** After Stop keeping only available results, shown briefly before the overlay closes. */
@Composable
internal fun SavedPill(claims: Int, modifier: Modifier = Modifier, higherOpacity: Boolean = false) {
    val p = LocalOvrlyPalette.current
    Row(
        modifier
            .mockGlass(higherOpacity)
            .sizeIn(minHeight = 52.dp)
            .semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite }
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        OverlayMark(Modifier.size(20.dp))
        Spacer(Modifier.width(10.dp))
        Text(
            "Saved to Inbox · ${claimLabel(claims)}",
            style = MaterialTheme.typography.labelLarge,
            color = p.ink
        )
    }
}

/** A small accent dot for unseen changes; it pulses twice when it appears, if animations run. */
@Composable
private fun UpdateDot(visible: Boolean, animate: Boolean, modifier: Modifier = Modifier) {
    if (!visible) return
    val p = LocalOvrlyPalette.current
    val pulse = remember { Animatable(1f) }
    LaunchedEffect(visible, animate) {
        if (animate) {
            repeat(2) {
                pulse.animateTo(PULSE_SCALE, tween(PULSE_MS))
                pulse.animateTo(1f, tween(PULSE_MS))
            }
        }
    }
    val color = if (p.dark) p.accent else p.error
    Canvas(
        modifier
            .size(8.dp)
            .graphicsLayer {
                scaleX = pulse.value
                scaleY = pulse.value
            }
            .clearAndSetSemantics { }
    ) { drawCircle(color) }
}

private const val EXPAND_LABEL = "Expand live results"
internal const val BUBBLE_DP = 64
private const val BUBBLE_INSET_DP = 4

private const val FORM_MS = 220
private const val FORM_EXIT_MS = FORM_MS / 2
private const val FORM_START_SCALE = 0.85f
private const val FORM_END_SCALE = 0.92f
private const val PULSE_MS = 220
private const val PULSE_SCALE = 1.6f
private const val BADGE_PULSE_SCALE = 1.25f
private const val DIVIDER_ALPHA = 0.12f
private const val MS_PER_SECOND = 1000L
private const val SECONDS_PER_MINUTE = 60
