package app.ovrly.ui

import app.ovrly.AppNotifications
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class LiveOverlayCopyTest {
    @Test fun thePillSaysExaminingWithItsTimeAndClaims() {
        assertEquals("Examining 0:27", examiningLabel(27))
        assertEquals("Examining 2:05", examiningLabel(125))
        assertEquals("Examining 0:00", examiningLabel(-3))
        assertEquals(
            "Examining, 1 minute 12 seconds. 2 claims, updated.",
            pillDescription(72, 2, true)
        )
        assertEquals("Examining, 0 minutes 5 seconds. 1 claim.", pillDescription(5, 1, false))
    }

    @Test fun overlayWordingNeverSaysRecording() {
        val texts = listOf(
            examiningLabel(27),
            pillDescription(72, 2, true),
            KEEP_EXAMINING_LABEL,
            AppNotifications.resultsReadyTitle(2)
        )
        texts.forEach { assertFalse(it, it.contains("record", ignoreCase = true)) }
        assertEquals("Keep examining", KEEP_EXAMINING_LABEL)
        assertEquals("Results ready · 2 claims", AppNotifications.resultsReadyTitle(2))
        assertEquals("Results ready · 1 claim", AppNotifications.resultsReadyTitle(1))
    }
}
