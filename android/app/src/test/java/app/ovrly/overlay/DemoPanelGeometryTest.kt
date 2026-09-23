package app.ovrly.overlay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.roundToInt

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
            }
        }
    }
}
