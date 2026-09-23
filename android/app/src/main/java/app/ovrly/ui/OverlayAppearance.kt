package app.ovrly.ui

import androidx.compose.ui.graphics.Color

internal object OverlayAppearance {
    const val DEFAULT_HIGHER_OPACITY = false

    val text = Color(0xFF2B302C)
    val recordingText = Color(0xFF482621)
    val secondaryInk = Color(0xFF444E45)

    private val translucentFill = listOf(
        Color(0xFFF8FAF6).copy(alpha = 0.68f),
        Color(0xFFEEF1EC).copy(alpha = 0.64f),
        Color(0xFFF6F8F3).copy(alpha = 0.66f),
    )
    val rimFill = listOf(
        Color(0xFFE5ECEB).copy(alpha = 0.48f),
        Color(0xFFDCE5E3).copy(alpha = 0.36f),
        Color(0xFFEAF0EB).copy(alpha = 0.46f),
    )
    private val readableFill = listOf(
        Color(0xFFF6F8F3), Color(0xFFF0F2ED), Color(0xFFF7F8F5),
    )

    fun fill(higherOpacity: Boolean): List<Color> =
        if (higherOpacity) readableFill else translucentFill
}
