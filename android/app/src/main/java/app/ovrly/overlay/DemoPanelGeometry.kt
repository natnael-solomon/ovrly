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
        margin = margin,
    )
}
