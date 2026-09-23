package app.ovrly.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.max
import kotlin.math.min

class GalleryOverlayTest {
    @Test fun actualGalleryIncludesAllSevenStatesForEveryDesign() {
        assertEquals(7, GalleryFixtures.size)
        assertEquals(7, GalleryFixtures.map { it.title }.toSet().size)
        assertEquals(
            listOf(
                OverlayVisual.Idle, OverlayVisual.Recording(42), OverlayVisual.Recording(72, 2),
                OverlayVisual.Checking, OverlayVisual.Results(4), OverlayVisual.NoClaims, OverlayVisual.NoAudio,
            ),
            GalleryFixtures.map { it.state },
        )
        assertEquals(105, StudyDesign.entries.flatMap { GalleryFixtures }.size)
    }

    @Test fun fixtureInteractionsExplicitlyDiscloseTheirMockBehavior() {
        GalleryFixtures.forEach { fixture ->
            StudyAction.entries.forEach { action ->
                assertTrue(fixture.actionExplanation(action).startsWith("Fixture only."))
            }
        }
        val noAudio = GalleryFixtures.single { it.state == OverlayVisual.NoAudio }
        assertTrue(noAudio.detailsExplanation.contains("playback audio"))
        assertTrue(noAudio.detailsExplanation.contains("share a video or link"))
    }

    @Test fun fifteenDirectionsHaveDistinctNamesAndExplicitSources() {
        assertEquals(15, StudyDesign.entries.size)
        assertEquals(15, StudyDesign.entries.map { it.title }.toSet().size)
        StudyDesign.entries.forEach {
            assertTrue(it.subtitle.isNotBlank())
            assertTrue(it.sourceNote.isNotBlank())
        }
        assertTrue(StudyDesign.LIQUID_LENS.sourceNote.contains("not refraction"))
        assertTrue(StudyDesign.FROSTED_VEIL.sourceNote.contains("not backdrop blur"))
    }

    @Test fun threeFamiliesHaveFiveCompleteDesignsEach() {
        assertEquals(3, StudyFamily.entries.size)
        StudyFamily.entries.forEach { family ->
            assertTrue(family.title.isNotBlank())
            assertTrue(family.description.isNotBlank())
            assertEquals(5, StudyDesign.entries.count { it.family == family })
            assertEquals(5, family.designs.toSet().size)
            assertTrue(family.designs.all { it.family == family })
        }
        assertEquals(StudyDesign.entries.toSet(), StudyFamily.entries.flatMap { it.designs }.toSet())
    }

    @Test fun additionalStudiesIncludeDifferentProfilesAndDensities() {
        assertEquals(14f, StudyDesign.FLOATING_RAIL.radiusDp, 0f)
        assertEquals(16f, StudyDesign.SOFT_SQUIRCLE.radiusDp, 0f)
        assertEquals(20f, StudyDesign.FROSTED_VEIL.radiusDp, 0f)
        assertEquals(26f, StudyDesign.DOMED_GLASS.radiusDp, 0f)
        assertTrue(StudyDesign.RIMLESS_AIR.normalOpacity < StudyDesign.CUSHION_GLASS.normalOpacity)
        assertTrue(StudyDesign.CUSHION_GLASS.normalOpacity < StudyDesign.MILK_GLASS.normalOpacity)
    }

    @Test fun sevenStateActionsDoNotConfuseCaptureResearchAndDetails() {
        assertEquals(StudyAction.SETUP, OverlayVisual.Idle.studyAction())
        assertEquals(StudyAction.STOP_CAPTURE, OverlayVisual.Recording(42).studyAction())
        assertEquals(StudyAction.STOP_CAPTURE, OverlayVisual.Recording(72, 2).studyAction())
        assertEquals(StudyAction.CANCEL_RESEARCH, OverlayVisual.Checking.studyAction())
        assertEquals(StudyAction.DETAILS, OverlayVisual.Results(4).studyAction())
        assertNull(OverlayVisual.NoClaims.studyAction())
        assertEquals(StudyAction.DETAILS, OverlayVisual.NoAudio.studyAction())
    }

    @Test fun quietAndFrostedDirectionsDifferInMaterialDensity() {
        val clear = StudyDesign.CLEAR_FLOAT.palette(dark = true, higherOpacity = false)
        val veil = StudyDesign.FROSTED_VEIL.palette(dark = true, higherOpacity = false)
        assertTrue(clear.fill.alpha < 0.35f)
        assertTrue(veil.fill.alpha > 0.65f)
        assertNotEquals(clear.fill, veil.fill)
    }

    @Test fun backdropPalettesAreExplicitAndHigherOpacityIncreasesReadability() {
        experimentalDesigns.forEach { design ->
            for (dark in listOf(false, true)) {
                val normal = design.palette(dark, false)
                val readable = design.palette(dark, true)
                assertTrue(readable.fill.alpha >= 0.93f)
                assertTrue(readable.fill.alpha > normal.fill.alpha)
                assertEquals(normal.ink, readable.ink)
                assertEquals(1f, normal.ink.alpha, 0f)
                assertEquals(1f, normal.secondary.alpha, 0f)
            }
            if (design != StudyDesign.SMOKED_GLASS && design != StudyDesign.MILK_GLASS) {
                assertNotEquals(design.palette(true, false).ink, design.palette(false, false).ink)
            }
        }
    }

    @Test fun smokedAndMilkStudiesKeepTheirDeliberateMaterialOnEitherBackdrop() {
        for (design in listOf(StudyDesign.SMOKED_GLASS, StudyDesign.MILK_GLASS)) {
            val palette = design.palette(false, false)
            assertEquals(palette, design.palette(true, false))
            for (backing in listOf(Color.Black, Color.White)) {
                assertTrue(contrast(palette.fill.compositeOver(backing), palette.ink) >= 4.5f)
            }
        }
        assertTrue(StudyDesign.SMOKED_GLASS.palette(false, false).dark)
        assertTrue(!StudyDesign.MILK_GLASS.palette(true, false).dark)
    }

    @Test fun readableModeMaintainsContrastEvenOnOppositeExtremeBackdrops() {
        experimentalDesigns.forEach { design ->
            for (dark in listOf(false, true)) {
                val palette = design.palette(dark, true)
                for (backing in listOf(Color.Black, Color.White)) {
                    val surface = palette.fill.compositeOver(backing)
                    assertTrue(contrast(surface, palette.ink) >= 4.5f)
                    assertTrue(contrast(surface, palette.secondary) >= 3f)
                }
            }
        }
    }

    @Test fun normalModeIsReadableWithinTheExplicitSyntheticBackdropRange() {
        experimentalDesigns.forEach { design ->
            for (dark in listOf(false, true)) {
                val palette = design.palette(dark, false)
                val surface = palette.fill.compositeOver(
                    if (dark) Color(0xFF969696) else Color(0xFFBEBEBE),
                )
                assertTrue("${design.title} text contrast", contrast(surface, palette.ink) >= 4.5f)
                assertTrue("${design.title} icon contrast", contrast(surface, palette.secondary) >= 3f)
            }
        }
    }

    @Test fun offlineStatusRemainsHonestIfIncludedInAStressPreview() {
        assertNotNull(OverlayVisual.Captured.studyAction())
        assertEquals(StudyAction.DETAILS, OverlayVisual.Captured.studyAction())
    }

    private val experimentalDesigns = StudyDesign.entries.filter { it != StudyDesign.OPTICAL_RIM }

    private fun contrast(first: Color, second: Color): Float {
        val high = max(first.luminance(), second.luminance())
        val low = min(first.luminance(), second.luminance())
        return (high + 0.05f) / (low + 0.05f)
    }
}
