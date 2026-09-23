package app.ovrly.share

import org.junit.Assert.*
import org.junit.Test

class SharePolicyTest {
    @Test fun acceptsOnlySingleWebReferenceNotAutomaticDownloads() {
        assertEquals("https://example.org/video?v=42", SharePolicy.webReference(" https://example.org/video?v=42 "))
        assertNull(SharePolicy.webReference("Watch this https://example.org"))
        assertNull(SharePolicy.webReference("file:///storage/video.mp4"))
        assertNull(SharePolicy.webReference("javascript:alert(1)"))
        assertNull(SharePolicy.webReference("https://user:pass@example.org/video"))
        assertNull(SharePolicy.webReference("https:///missing-host"))
        assertNull(SharePolicy.webReference("https://example.org/" + "x".repeat(4096)))
    }
}
