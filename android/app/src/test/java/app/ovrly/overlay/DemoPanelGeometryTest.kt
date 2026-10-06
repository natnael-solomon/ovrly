package app.ovrly.overlay

import kotlin.math.roundToInt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DemoPanelGeometryTest {
    @Test fun portraitAndLandscapeStayWithinHalfTheUsableScreen() {
        for ((width, height) in listOf(720 to 1436, 1080 to 2200, 1436 to 720, 600 to 900)) {
            for (density in listOf(1f, 1.875f, 2.75f, 3f)) {
                val panel = demoPanelGeometry(width, height, density)
                assertEquals((16 * density).roundToInt(), panel.margin)
                assertEquals(width, panel.width + panel.margin * 2)
                assertTrue(panel.height < height / 2)
                assertTrue(panel.bottomY(height) >= height / 2)
                assertEquals(height - panel.margin, panel.bottomY(height) + panel.height)
            }
        }
    }

    @Test fun tinyAvailableAreasNeverProduceNegativeDimensions() {
        for (width in 1..80) {
            for (height in 1..80) {
                val panel = demoPanelGeometry(width, height, 3f)
                assertTrue(panel.width >= 1)
                assertTrue(panel.height >= 1)
                assertTrue(panel.bottomY(height) >= 0)
                assertTrue(livePanelGeometry(width, height, 3f).width >= 1)
            }
        }
    }

    @Test fun livePanelIsTheDemoFrameWithTheRightGutterKeptFree() {
        for ((width, height) in listOf(720 to 1436, 1080 to 2200, 1436 to 720)) {
            for (density in listOf(1f, 1.875f, 2.75f)) {
                val demo = demoPanelGeometry(width, height, density)
                val live = livePanelGeometry(width, height, density)
                assertEquals(demo.margin, live.margin)
                assertEquals(demo.height, live.height)
                assertEquals((LIVE_GUTTER_DP * density).roundToInt(), demo.width - live.width)
                // Left-docked at the margin, so the right edge leaves the gutter plus the margin.
                assertEquals(
                    width - live.margin - live.width,
                    live.margin + demo.width - live.width
                )
            }
        }
    }

    @Test fun theBubbleSnapsToTheNearerEdge() {
        assertEquals(8, snapToEdge(x = 100, viewWidth = 64, availableWidth = 720, margin = 8))
        assertEquals(648, snapToEdge(x = 500, viewWidth = 64, availableWidth = 720, margin = 8))
        assertEquals(8, snapToEdge(x = 327, viewWidth = 64, availableWidth = 720, margin = 8))
        assertEquals(648, snapToEdge(x = 328, viewWidth = 64, availableWidth = 720, margin = 8))
        assertEquals(8, snapToEdge(x = 0, viewWidth = 64, availableWidth = 40, margin = 8))
    }

    @Test fun onlyADropOnTheTargetDismisses() {
        assertTrue(overDismissTarget(360, 1300, targetX = 360, targetY = 1300, radius = 96))
        assertTrue(overDismissTarget(420, 1360, targetX = 360, targetY = 1300, radius = 96))
        assertFalse(overDismissTarget(460, 1300, targetX = 360, targetY = 1300, radius = 96))
        assertFalse(overDismissTarget(360, 600, targetX = 360, targetY = 1300, radius = 96))
    }
}
