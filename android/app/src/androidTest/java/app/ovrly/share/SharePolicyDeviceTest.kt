package app.ovrly.share

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * [SharePolicy] on ART: links are parsed by the device's `java.net.URI`, which is not the
 * JVM's, and the video rules must reach the same decision there as in the unit tests.
 */
@RunWith(AndroidJUnit4::class)
class SharePolicyDeviceTest {
    private val limits = ShareLimits(maxBytes = 1_000)

    private fun facts(
        providerType: String? = "video/mp4",
        containerType: String? = "video/mp4",
        hasVideo: Boolean = true,
        durationMs: Long? = 1_000,
        sizeBytes: Long? = 500
    ) = VideoFacts(providerType, containerType, hasVideo, durationMs, sizeBytes)

    @Test fun linksAreSingleWebReferencesOnTheDevice() {
        assertEquals(
            "https://example.org/video?v=42",
            SharePolicy.webReference(" https://example.org/video?v=42 ")
        )
        assertNull(SharePolicy.webReference("Watch this https://example.org"))
        assertNull(SharePolicy.webReference("content://media/external/video/1"))
        assertNull(SharePolicy.webReference("intent://example.org#Intent;end"))
        assertNull(SharePolicy.webReference("https://user:secret@example.org/video"))
        assertNull(SharePolicy.webReference("https:///missing-host"))
        assertNull(SharePolicy.webReference("https://example.org/" + "x".repeat(4096)))
    }

    @Test fun videoRulesOnTheDevice() {
        assertNull(SharePolicy.checkVideo(facts(), limits))
        assertEquals(
            ShareProblem.NOT_VIDEO,
            SharePolicy.checkVideo(facts(providerType = "image/png"), limits)
        )
        assertEquals(
            ShareProblem.NOT_VIDEO,
            SharePolicy.checkVideo(facts(containerType = "audio/mp4"), limits)
        )
        assertEquals(
            ShareProblem.NOT_VIDEO,
            SharePolicy.checkVideo(facts(hasVideo = false), limits)
        )
        assertEquals(
            ShareProblem.NO_DURATION,
            SharePolicy.checkVideo(facts(durationMs = null), limits)
        )
        assertEquals(
            ShareProblem.TOO_LONG,
            SharePolicy.checkVideo(facts(durationMs = limits.maxDurationMs + 1), limits)
        )
        assertEquals(
            ShareProblem.TOO_LARGE,
            SharePolicy.checkVideo(facts(sizeBytes = 1_001), limits)
        )
        assertEquals(ShareProblem.UNREADABLE, SharePolicy.checkVideo(facts(sizeBytes = 0), limits))
    }
}
