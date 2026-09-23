package app.ovrly.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OverlayAppearanceTest {
    @Test fun newInstallsUseTranslucentGlass() {
        assertFalse(OverlayAppearance.DEFAULT_HIGHER_OPACITY)
        OverlayAppearance.fill(false).forEach {
            assertTrue("Reading surface must transmit at least 31% of the backing", it.alpha <= 0.69f)
            assertTrue("Keep a contrast-supporting tint", it.alpha >= 0.63f)
        }
    }

    @Test fun rimIsClearerThanTheReadingSurface() {
        val minimumBodyAlpha = OverlayAppearance.fill(false).minOf { it.alpha }
        OverlayAppearance.rimFill.forEach {
            assertTrue("Rim must remain lightly tinted", it.alpha in 0.35f..0.49f)
            assertTrue("Clear space between the walls must show more backing", it.alpha < minimumBodyAlpha)
        }
    }

    @Test fun fallbackDoesNotTransmitBusyBacking() {
        OverlayAppearance.fill(true).forEach { assertEquals(1f, it.alpha, 0f) }
    }

    @Test fun foregroundsStayOpaque() {
        listOf(OverlayAppearance.text, OverlayAppearance.recordingText, OverlayAppearance.secondaryInk)
            .forEach { assertEquals(1f, it.alpha, 0f) }
    }

    @Test fun foregroundContrastSurvivesDarkestBackingAcrossGradient() {
        for (higherOpacity in listOf(false, true)) {
            val fill = OverlayAppearance.fill(higherOpacity)
            fill.zipWithNext().forEach { (start, end) ->
                for (step in 0..100) {
                    val surface = androidx.compose.ui.graphics.lerp(start, end, step / 100f)
                        .compositeOver(Color.Black)
                    for (text in listOf(OverlayAppearance.text, OverlayAppearance.recordingText)) {
                        assertTrue("Text contrast must be at least 4.5:1", contrast(surface, text) >= 4.5f)
                    }
                    assertTrue("Icon contrast must be at least 3:1",
                        contrast(surface, OverlayAppearance.secondaryInk) >= 3f)
                }
            }
        }
    }

    private fun contrast(background: Color, foreground: Color): Float =
        (background.luminance() + 0.05f) / (foreground.luminance() + 0.05f)
}
