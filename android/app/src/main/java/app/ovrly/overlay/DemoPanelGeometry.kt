package app.ovrly.overlay

import kotlin.math.roundToInt

internal data class DemoPanelGeometry(val width: Int, val height: Int, val margin: Int) {
    fun bottomY(availableHeight: Int) = availableHeight - margin - height
}

internal fun demoPanelGeometry(width: Int, height: Int, density: Float): DemoPanelGeometry {
    val margin = (16 * density).roundToInt().coerceAtMost(minOf(width, height) / 4)
    return DemoPanelGeometry(
        width = (width - margin * 2).coerceAtLeast(1),
        height = (height / 2 - margin).coerceAtLeast(1),
        margin = margin
    )
}

/**
 * The expanded live panel: the demo panel's frame (left margin, half-height cap),
 * narrowed so the right [LIVE_GUTTER_DP] stays free for the like, comment and share buttons
 * that short-video apps put on the right edge. [DemoPanelGeometry.height] is a cap here: the
 * live panel grows with its content.
 */
internal fun livePanelGeometry(width: Int, height: Int, density: Float): DemoPanelGeometry {
    val demo = demoPanelGeometry(width, height, density)
    val gutter = (LIVE_GUTTER_DP * density).roundToInt().coerceAtMost(demo.width / MAX_GUTTER_SHARE)
    return demo.copy(width = (demo.width - gutter).coerceAtLeast(1))
}

/** The idle bubble snaps to the nearer side edge when released, [margin] inside it. */
internal fun snapToEdge(x: Int, viewWidth: Int, availableWidth: Int, margin: Int): Int {
    val right = (availableWidth - viewWidth - margin).coerceAtLeast(margin)
    return if (x + viewWidth / 2 < availableWidth / 2) margin else right
}

/**
 * A bubble dropped with its centre within [radius] of the dismiss target's centre, both in
 * window coordinates, is dismissed.
 */
internal fun overDismissTarget(
    centerX: Int,
    centerY: Int,
    targetX: Int,
    targetY: Int,
    radius: Int
): Boolean {
    val dx = (centerX - targetX).toLong()
    val dy = (centerY - targetY).toLong()
    return dx * dx + dy * dy <= radius.toLong() * radius
}

internal const val LIVE_GUTTER_DP = 72

/** The gutter never takes more than a third of the panel on very narrow windows. */
private const val MAX_GUTTER_SHARE = 3
