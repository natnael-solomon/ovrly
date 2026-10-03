package app.ovrly.ui.voice

import androidx.compose.foundation.OverscrollEffect
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberOverscrollEffect
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.node.DelegatableNode
import androidx.compose.ui.unit.Velocity
import androidx.compose.ui.unit.dp

/**
 * Scroll-linked collapse: while the hero is open, the user's swipe shrinks it frame for frame
 * before the list moves. The rest of the same swipe then scrolls the list. Releasing part-way
 * settles to the nearer end; scrolling back to the top never reopens it.
 */
internal class DockScrollCollapse(private val state: VoiceOrbDockState) : NestedScrollConnection {
    /** Set while a downward fling's momentum is collapsing the hero. */
    private var flingCollapsing = false

    override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset {
        val range = state.collapseRangePx
        val current = state.visualProgress.coerceIn(0f, 1f)
        val user = source == NestedScrollSource.UserInput
        val collapsing = available.y < 0f && current > 0f &&
            (if (user) state.expanded || state.dragging else flingCollapsing)
        // Reversing within the same drag may reopen what that drag collapsed, nothing more.
        val reopening = user && available.y > 0f && state.dragging && current < 1f
        if (range <= 0f || !(collapsing || reopening)) return Offset.Zero
        val next = (current + available.y / range).coerceIn(0f, 1f)
        state.dragValue = next
        state.dragging = true
        // Fully docked: the gesture hands over to the list for good, so reversing later in the
        // same swipe scrolls the list instead of pulling the hero back out.
        if (next == 0f) {
            flingCollapsing = false
            state.close()
        }
        return Offset(0f, (next - current) * range)
    }

    override suspend fun onPreFling(available: Velocity): Velocity = when {
        !state.dragging || !state.expanded -> Velocity.Zero

        available.y < 0f -> {
            // Let the list fling; its momentum flows through onPreScroll and docks the hero
            // first, so header and list move as one decelerating motion.
            flingCollapsing = true
            Velocity.Zero
        }

        else -> settle(available)
    }

    override suspend fun onPostFling(consumed: Velocity, available: Velocity): Velocity {
        flingCollapsing = false
        // Momentum ran out (or the list was too short) before the hero docked: settle it.
        return if (state.dragging && state.expanded) settle(Velocity.Zero) else Velocity.Zero
    }

    private suspend fun settle(available: Velocity): Velocity {
        val reopen = state.dragValue > SETTLE_MIDPOINT
        state.progress.snapTo(state.dragValue)
        state.dragging = false
        return if (reopen) {
            state.settleRequest++
            state.idleScope?.let(state::scheduleIdleCollapse) // touched: a fresh 6 s
            available // the list stays put while the hero glides back
        } else {
            state.close()
            Velocity.Zero
        }
    }

    private companion object {
        const val SETTLE_MIDPOINT = .5f
    }
}

/**
 * Scrolling column for a screen whose first child is the voice dock: wires the scroll-linked
 * collapse and the bottom-only edge effect. Children are composed once (no lazy layout), so a
 * fast fling never builds content mid-motion.
 */
@Composable
internal fun VoiceDockColumn(
    scrollState: ScrollState,
    dock: VoiceOrbDockState?,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(
        modifier.fillMaxSize()
            .then(if (dock == null) Modifier else Modifier.nestedScroll(dock.scrollCollapse))
            .verticalScroll(scrollState, overscrollEffect = voiceDockOverscroll(dock))
            .padding(DOCK_COLUMN_PADDING),
        verticalArrangement = Arrangement.spacedBy(DOCK_COLUMN_PADDING),
        content = content
    )
}

private val DOCK_COLUMN_PADDING = 24.dp

/**
 * Lists hosting the dock keep the system edge effect at the bottom only. At the top it stretched
 * the header, so a fast fling made the docked orb swell and shift; at the bottom it softens the
 * stop of a fast fling.
 */
@Composable
private fun voiceDockOverscroll(dock: VoiceOrbDockState?): OverscrollEffect? {
    val system = rememberOverscrollEffect() ?: return null
    return if (dock == null) system else remember(system) { BottomOnlyOverscroll(system) }
}

/** Passes top-edge (content moving down) scrolls and flings straight through. */
private class BottomOnlyOverscroll(private val inner: OverscrollEffect) : OverscrollEffect {
    override val isInProgress: Boolean get() = inner.isInProgress
    override val node: DelegatableNode get() = inner.node

    override fun applyToScroll(
        delta: Offset,
        source: NestedScrollSource,
        performScroll: (Offset) -> Offset
    ): Offset = if (delta.y > 0f && !inner.isInProgress) {
        performScroll(delta)
    } else {
        inner.applyToScroll(delta, source, performScroll)
    }

    override suspend fun applyToFling(
        velocity: Velocity,
        performFling: suspend (Velocity) -> Velocity
    ) {
        if (velocity.y > 0f && !inner.isInProgress) {
            performFling(velocity)
        } else {
            inner.applyToFling(velocity, performFling)
        }
    }
}
