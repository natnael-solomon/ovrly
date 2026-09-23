package app.ovrly.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.max
import kotlin.math.min

class OvrlyThemeTest {
    @Test fun themesMatchTheTwoMockReferences() {
        assertEquals(Color(0xFFF0EFE5), paletteFor(false).paper)
        assertEquals(Color(0xFF080910), paletteFor(true).paper)
        assertEquals(Color(0xFFD5EB97), paletteFor(false).accent)
        assertEquals(Color(0xFFC5B4FA), paletteFor(true).accent)
    }

    @Test fun textMeetsAAOnBothThemeSurfaces() {
        for (p in listOf(PaperPalette, ChromePalette)) {
            for (surface in listOf(p.paper, p.surface)) {
                assertTrue(contrast(p.ink, surface) >= 7f)
                assertTrue(contrast(p.muted, surface) >= 4.5f)
                assertTrue(contrast(p.error, surface) >= 4.5f)
            }
            assertTrue(contrast(p.accentInk, p.accent) >= 7f)
        }
    }

    @Test fun overlayReadableOnExtremeBackdropsWithOrWithoutBlur() {
        for (p in listOf(PaperPalette, ChromePalette)) {
            for (blur in listOf(false, true)) {
                for (opaque in listOf(false, true)) {
                    for (backdrop in listOf(Color.White, Color.Black)) {
                        val surface = p.glassFill(blur, opaque).compositeOver(backdrop)
                        assertTrue("primary text, dark=${p.dark}", contrast(p.ink, surface) >= 4.5f)
                        assertTrue("secondary text, dark=${p.dark}", contrast(p.muted, surface) >= 4.5f)
                    }
                }
            }
        }
    }

    @Test fun fallbackIsOpaqueRatherThanFakeBlur() {
        for (p in listOf(PaperPalette, ChromePalette)) {
            assertEquals(1f, p.glassFill(false, false).alpha, 0f)
            assertEquals(1f, p.glassFill(true, true).alpha, 0f)
            assertTrue(p.glassFill(true, false).alpha < 1f)
        }
    }

    @Test fun everyDemoAssessmentDisclosesThatItIsASample() {
        assertEquals(3, DemoClaims.size)
        DemoClaims.forEach {
            assertTrue(it.label.endsWith("/ sample"))
            assertTrue(it.explanation.startsWith("This illustrates"))
        }
    }

    private fun contrast(a: Color, b: Color): Float =
        (max(a.luminance(), b.luminance()) + 0.05f) / (min(a.luminance(), b.luminance()) + 0.05f)
}
