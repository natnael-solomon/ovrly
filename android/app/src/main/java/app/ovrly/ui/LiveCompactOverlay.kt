package app.ovrly.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
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
import androidx.compose.ui.unit.IntSize
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
    /** After Stop: the bubble was tapped; it opens into the pill. */
    val onOpenPill: () -> Unit = {},
    /** Hides the overlay after Stop; research and capture are untouched. */
    val onDismiss: () -> Unit = {},
    val onOpenClaim: (String) -> Unit = {},
    val onCloseClaim: () -> Unit = {},
    val onDismissNotice: (String) -> Unit = {},
    val onRequestStop: () -> Unit = {},
    val onStopChoice: (Boolean) -> Unit = {},
    val onCancelStop: () -> Unit = {},
    /** The pill's size while the panel is open below it: the pill is the window's drag area. */
    val onPillSize: (IntSize) -> Unit = {}
) {
    companion object {
        val None = LivePanelActions()
    }
}

/**
 * The live overlay in its current [form]: the pill (with the Stop choice under it when
 * asked), the pill with the claims panel below it, the idle bubble after Stop, or the short
 * "Saved to Inbox" pill. Every change is instant: the panel appears under the pill, which
 * stays where it is, and the window never animates its size (that showed as jitter).
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
    val claims = model.results.claims.size
    val expanded = form == LiveOverlayForm.EXPANDED
    // While examining: the timer and Stop. After Stop: the session state and Dismiss.
    val status = if (examining == null) afterStopLabel(model.results.phase) else null
    val button = if (examining == null) actions.onDismiss else actions.onRequestStop
    val pill = @Composable { pillModifier: Modifier ->
        ExaminingPill(
            PillState(examining?.seconds ?: 0, claims, model.panel.unseen, status, expanded),
            onExpand = (if (expanded) actions.onCollapse else actions.onExpand)
                .takeIf { model.results.connected },
            onStop = button.takeIf { examining?.canStop != false },
            modifier = pillModifier,
            frame = frame
        )
    }
    Box(modifier) {
        when (form) {
            LiveOverlayForm.PILL -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                pill(Modifier)
                if (model.panel.stopPrompt) {
                    StopChoicePrompt(
                        actions.onStopChoice,
                        actions.onCancelStop,
                        higherOpacity = frame.higherOpacity
                    )
                }
            }

            LiveOverlayForm.EXPANDED -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // The window spans the panel; the pill keeps its own x inside it.
                pill(
                    Modifier
                        .layout { measurable, constraints ->
                            val placeable = measurable.measure(constraints.copy(minWidth = 0))
                            val room = (constraints.maxWidth - placeable.width).coerceAtLeast(0)
                            val x = frame.pillOffset.roundToPx().coerceIn(0, room)
                            layout(constraints.maxWidth, placeable.height) {
                                placeable.place(x, 0)
                            }
                        }
                        .onSizeChanged(actions.onPillSize)
                )
                LiveExpandedPanel(
                    model,
                    actions,
                    frame = frame.copy(maxHeight = frame.maxHeight - PILL_ROW_DP.dp)
                )
            }

            LiveOverlayForm.BUBBLE -> IdleBubble(
                PillState(0, claims, model.panel.unseen),
                onExpand = actions.onOpenPill,
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

internal fun pillDescription(
    seconds: Int,
    claims: Int,
    unseen: Boolean,
    status: String? = null
): String = buildString {
    fun count(n: Int, unit: String) = "$n $unit${if (n == 1) "" else "s"}"
    val elapsed = seconds.coerceAtLeast(0)
    if (status != null) {
        append("ovrly. $status. ")
    } else {
        append("Examining, ${count(elapsed / SECONDS_PER_MINUTE, "minute")} ")
        append("${count(elapsed % SECONDS_PER_MINUTE, "second")}. ")
    }
    append(claimLabel(claims))
    if (unseen) append(", updated")
    append('.')
}

/**
 * What the pill reads: elapsed time (or, after Stop, [status] instead), claim count and
 * whether anything is unseen. [expanded] when the panel is open below it, so a tap collapses.
 */
@Immutable
internal data class PillState(
    val seconds: Int,
    val claims: Int,
    val unseen: Boolean = false,
    val status: String? = null,
    val expanded: Boolean = false
)

/**
 * The pill: mark, timer (or the session state after Stop), claim count and update dot, then
 * Stop (Dismiss after Stop). Tapping anywhere but the button opens or closes the panel below
 * it ([onExpand] is null until live results are connected); the host's window drag starts
 * only after the touch moves past the touch slop.
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
        // One fixed width in every state (the panel's width when that is narrower, so it is
        // the same with the panel open), so the pill never resizes as the timer, the count,
        // the dot or the open panel change.
        Row(
            modifier
                .width(minOf(PILL_DP.dp, frame.width))
                .mockGlass(frame.higherOpacity)
                .sizeIn(minHeight = 52.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            PillReadout(state, onExpand, frame.animate, Modifier.weight(1f))
            if (onStop != null) {
                Spacer(
                    Modifier.width(
                        1.dp
                    ).height(
                        20.dp
                    ).background(LocalOvrlyPalette.current.ink.copy(alpha = DIVIDER_ALPHA))
                )
                if (state.status == null) {
                    PanelIconButton(Glyph.Stop, "Stop examining", onStop)
                } else {
                    PanelIconButton(Glyph.Close, "Dismiss overlay. Research continues", onStop)
                }
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
    val description = pillDescription(state.seconds, state.claims, state.unseen, state.status)
    val label = if (state.expanded) COLLAPSE_LABEL else EXPAND_LABEL
    val tap = onExpand?.let {
        Modifier.clickable(role = Role.Button, onClickLabel = label, onClick = it)
    }
    Row(
        modifier
            .sizeIn(minHeight = 52.dp)
            .then(tap ?: Modifier)
            .clearAndSetSemantics {
                contentDescription = description
                if (onExpand != null) {
                    onClick(label) {
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
        // "Live" and the timer never give way; after Stop a long session state shortens
        // before the claim count does.
        val flexible = Modifier.weight(1f, fill = false)
        Text(
            state.status ?: "Live",
            modifier = if (state.status != null) flexible else Modifier,
            style = MaterialTheme.typography.labelMedium,
            color = p.muted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        if (state.status == null) {
            Spacer(Modifier.width(6.dp))
            Text(
                examiningLabel(state.seconds).removePrefix("Examining "),
                style = MaterialTheme.typography.titleSmall.copy(
                    fontWeight = FontWeight.SemiBold,
                    fontFeatureSettings = "tnum"
                ),
                softWrap = false
            )
        }
        Spacer(Modifier.width(10.dp))
        Spacer(Modifier.width(1.dp).height(20.dp).background(p.ink.copy(alpha = DIVIDER_ALPHA)))
        Spacer(Modifier.width(10.dp))
        Text(
            claimLabel(state.claims),
            modifier = if (state.status == null) flexible else Modifier,
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
private const val COLLAPSE_LABEL = "Collapse live results"

/** The pill's row above the open panel: its height plus the gap, taken off the panel's cap. */
private const val PILL_ROW_DP = 60

/** The pill's one width: "Live 3:00", "99 claims", the dot and Stop at default text size. */
private const val PILL_DP = 272
internal const val BUBBLE_DP = 64
private const val BUBBLE_INSET_DP = 4

private const val PULSE_MS = 220
private const val PULSE_SCALE = 1.6f
private const val BADGE_PULSE_SCALE = 1.25f
private const val DIVIDER_ALPHA = 0.12f
private const val MS_PER_SECOND = 1000L
private const val SECONDS_PER_MINUTE = 60
