package app.ovrly.ui.voice

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

private object OrbSocketRef {
    const val SOCKET_SIZE_DP = 40
    const val SOCKET_LIVE_ALPHA = .9f
    const val SOCKET_IDLE_ALPHA = .45f
    const val SOCKET_STROKE_DP = 1.5f
    const val SOCKET_DASH_DP = 4
    const val SOCKET_GAP_DP = 5
    const val SOCKET_CHEVRON_DP = 5
    const val SOCKET_CHEVRON_STROKE_DP = 2
    const val SOCKET_TAP_THRESHOLD = .5f
    const val SOCKET_RADIUS_INSET_DP = 1
}

/** Ghost ring in the vacated slot while the orb is away. Tints with the live phase. */
@Composable
internal fun Socket(state: VoiceOrbDockState, visible: Float, onTap: () -> Unit) {
    val live = state.phase.isLive()
    val tint = if (live) OrbSpec.tint(state.phase) else OrbSpec.grey
    val ringAlpha = if (live) OrbSocketRef.SOCKET_LIVE_ALPHA else OrbSocketRef.SOCKET_IDLE_ALPHA
    val tappable = visible > OrbSocketRef.SOCKET_TAP_THRESHOLD
    Box(
        Modifier.size(OrbSocketRef.SOCKET_SIZE_DP.dp).alpha(visible)
            .drawBehind { drawSocket(tint, ringAlpha) }
            .then(if (tappable) Modifier.socketTap(onTap) else Modifier)
            .semantics {
                if (tappable) {
                    role = Role.Button
                    contentDescription = "Put the voice orb back"
                }
            }
    )
}

private fun DrawScope.drawSocket(tint: Color, ringAlpha: Float) {
    drawCircle(
        tint.copy(alpha = ringAlpha),
        radius = size.minDimension / 2f - OrbSocketRef.SOCKET_RADIUS_INSET_DP.dp.toPx(),
        style = Stroke(
            OrbSocketRef.SOCKET_STROKE_DP.dp.toPx(),
            cap = StrokeCap.Round,
            pathEffect = PathEffect.dashPathEffect(
                floatArrayOf(
                    OrbSocketRef.SOCKET_DASH_DP.dp.toPx(),
                    OrbSocketRef.SOCKET_GAP_DP.dp.toPx()
                )
            )
        )
    )
    // chevron
    val s = OrbSocketRef.SOCKET_CHEVRON_DP.dp.toPx()
    val c = center
    val chevron = tint.copy(alpha = OrbSocketRef.SOCKET_LIVE_ALPHA)
    val width = OrbSocketRef.SOCKET_CHEVRON_STROKE_DP.dp.toPx()
    val half = s / 2
    drawLine(chevron, Offset(c.x - s, c.y + half), Offset(c.x, c.y - half), width, StrokeCap.Round)
    drawLine(chevron, Offset(c.x, c.y - half), Offset(c.x + s, c.y + half), width, StrokeCap.Round)
}

private fun Modifier.socketTap(onTap: () -> Unit) = pointerInput(Unit) {
    awaitEachGesture {
        awaitFirstDown()
        if (waitForUpOrCancellation() != null) onTap()
    }
}
