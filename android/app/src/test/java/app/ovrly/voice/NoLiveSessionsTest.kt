package app.ovrly.voice

import app.ovrly.BuildConfig
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Unit and CI builds never open a real Voxide session (BC-D04 session budget). */
class NoLiveSessionsTest {
    @Test
    fun testBuildsUseTheOfflineSimulationWithoutAKey() {
        assertFalse(BuildConfig.VOXIDE_LIVE)
        assertFalse(BuildConfig.VOXIDE_ENABLED)
        assertEquals("", BuildConfig.VOXIDE_PUBLISHABLE_KEY)
        assertTrue(voiceSimulated(BuildConfig.VOXIDE_LIVE))
    }

    @Test
    fun everyTestThatBuildsTheRealTransportPointsItAtAFake() {
        val users = File("src/test/java").walkTopDown()
            .filter { it.isFile && it.extension == "kt" && it.name != "NoLiveSessionsTest.kt" }
            .filter { "VoxideTransport(" in it.readText() }
            .toList()
        assertTrue("expected the transport tests", users.isNotEmpty())
        for (file in users) {
            val text = file.readText()
            assertTrue(file.name, "example.invalid" in text)
            assertTrue(file.name, "addInterceptor" in text || "MockWebServer" in text)
        }
    }
}
