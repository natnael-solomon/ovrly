package app.ovrly.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import kotlin.math.max
import kotlin.math.min
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OvrlyThemeTest {
    @Test fun materialTypographyKeepsFunctionalSurfacesInLexend() {
        with(OvrlyTypography) {
            listOf(
                displayLarge, displayMedium, displaySmall,
                headlineLarge, headlineMedium, headlineSmall,
                titleLarge, titleMedium, titleSmall,
                bodyLarge, bodyMedium, bodySmall,
                labelLarge, labelMedium, labelSmall
            ).forEach { assertEquals(OvrlySans, it.fontFamily) }
        }
    }

    @Test fun dialogAndDemoClaimHeadingsKeepTheirSizeWithMediumWeight() {
        with(OvrlyTypography) {
            assertEquals(28.sp, headlineSmall.fontSize)
            assertEquals(32.sp, headlineSmall.lineHeight)
            assertEquals(FontWeight.Medium, headlineSmall.fontWeight)
            assertEquals(26.sp, titleLarge.fontSize)
            assertEquals(32.sp, titleLarge.lineHeight)
            assertEquals(FontWeight.Medium, titleLarge.fontWeight)
        }
    }

    @Test fun editorialStylesKeepInstrumentSerifAndExistingMetrics() {
        with(OvrlyEditorialTypography) {
            listOf(display, title, wordmark).forEach { assertEquals(OvrlySerif, it.fontFamily) }
            assertEquals(40.sp, display.fontSize)
            assertEquals(44.sp, display.lineHeight)
            assertEquals(26.sp, title.fontSize)
            assertEquals(32.sp, title.lineHeight)
            assertEquals(36.sp, wordmark.fontSize)
            assertEquals(40.sp, wordmark.lineHeight)
        }
    }

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
            for (overlay in listOf(false, true)) {
                for (blur in listOf(false, true)) {
                    for (opaque in listOf(false, true)) {
                        for (backdrop in listOf(Color.White, Color.Black, Color(0xFF808080))) {
                            val surface = p.glassFill(blur, opaque, overlay).compositeOver(backdrop)
                            val where = "dark=${p.dark} overlay=$overlay blur=$blur"
                            assertTrue("primary text, $where", contrast(p.ink, surface) >= 4.5f)
                            assertTrue("secondary text, $where", contrast(p.muted, surface) >= 4.5f)
                            assertTrue("error text, $where", contrast(p.error, surface) >= 4.5f)
                        }
                    }
                }
            }
        }
    }

    @Test fun companionFallbackIsOpaqueAndOverlayFallbackShowsTheVideoFaintly() {
        for (p in listOf(PaperPalette, ChromePalette)) {
            assertEquals(1f, p.glassFill(false, false).alpha, 0f)
            assertEquals(1f, p.glassFill(true, true).alpha, 0f)
            assertTrue(p.glassFill(true, false).alpha < 1f)
            assertEquals(0.88f, p.glassFill(false, false, overlay = true).alpha, 0.005f)
            assertEquals(1f, p.glassFill(false, true, overlay = true).alpha, 0f)
        }
        // Light overlay glass is tinted toward the accent; dark glass and the companion are not.
        assertNotEquals(PaperPalette.surface, PaperPalette.glassFill(false, true, overlay = true))
        assertEquals(ChromePalette.surface, ChromePalette.glassFill(false, true, overlay = true))
        assertEquals(PaperPalette.surface, PaperPalette.glassFill(false, true))
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
