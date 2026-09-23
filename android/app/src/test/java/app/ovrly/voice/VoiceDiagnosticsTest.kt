package app.ovrly.voice

import java.io.IOException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceDiagnosticsTest {
    @Test fun expectedCleanupFailuresAreAccumulatedAndEveryResourceIsAttempted() {
        val events = mutableListOf<String>()
        val released = mutableListOf<String>()
        val failures = VoiceDiagnostics(events::add).cleanup(
            "first" to { released.add("first"); throw IllegalStateException("private details") },
            "second" to { released.add("second"); throw IOException("https://secret.invalid/") },
            "last" to { released.add("last") },
        )
        assertEquals(listOf("first", "second", "last"), released)
        assertEquals(listOf("first", "second"), failures)
        assertTrue(events.any { it.contains("Cleanup diagnostics") })
        assertFalse(events.any { it.contains("private") || it.contains("secret.invalid") })
    }

    @Test fun nestedResourceDiagnosticsRemainVisible() {
        val events = mutableListOf<String>()
        val failures = VoiceDiagnostics(events::add).cleanup(
            "audio" to { throw VoiceCleanupException(listOf("speaker release", "microphone stop")) },
        )
        assertEquals(listOf("audio: speaker release", "audio: microphone stop"), failures)
    }

    @Test fun unexpectedProgrammingFaultStillRunsLaterCleanupThenPropagates() {
        val events = mutableListOf<String>()
        var laterReleased = false
        assertThrows(UnsupportedOperationException::class.java) {
            VoiceDiagnostics(events::add).cleanup(
                "bug" to { throw UnsupportedOperationException("unexpected") },
                "later" to { laterReleased = true },
            )
        }
        assertTrue(laterReleased)
        assertTrue(events.any { it.contains("Unexpected cleanup fault") })
    }
}
