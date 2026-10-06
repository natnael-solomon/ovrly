package app.ovrly.share

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.fixtures.FixtureLength
import app.ovrly.fixtures.FixtureMedia
import app.ovrly.fixtures.FixtureSize
import app.ovrly.fixtures.ShareFixtures
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * [ShareInputReader] on a device against a provider in another app, with real `content://`
 * grants: granted, never granted, revoked and deleted sources, oversize and overlong videos,
 * provider and container MIME types that disagree, and the share intent shapes.
 */
@RunWith(AndroidJUnit4::class)
class ShareInputReaderTest {
    @get:Rule val fixtures = ShareFixtures()

    private val context: Context = ApplicationProvider.getApplicationContext()
    private val limits = ShareLimits(maxBytes = 50_000_000)

    private fun reader(limits: ShareLimits = this.limits) = ShareInputReader(context, limits)

    private fun ShareRead.video(): ShareCandidate.Video {
        val accepted = this as? ShareRead.Accepted ?: error("Expected acceptance, got $this")
        return accepted.candidate as? ShareCandidate.Video ?: error("Not a video: $accepted")
    }

    private fun ShareRead.problem(): ShareProblem =
        (this as? ShareRead.Rejected)?.problem ?: error("Expected a rejection, got $this")

    @Test fun grantedVideoIsAcceptedAndStreamedWhileTheGrantHolds() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))

        val video = reader().readVideo(uri).video()

        assertEquals("video/mp4", video.contentType)
        assertTrue("duration ${video.durationMs}", video.durationMs in 1..limits.maxDurationMs)
        val size = requireNotNull(video.sizeBytes) { "size comes from OpenableColumns.SIZE" }
        assertEquals(size, video.open().use { it.readBytes().size.toLong() })
    }

    @Test fun declaredDescriptorLengthIsTheSize() {
        val uri = fixtures.grant(
            fixtures.uri(
                FixtureMedia.VIDEO,
                length = FixtureLength.DECLARED,
                size = FixtureSize.NONE
            )
        )

        val video = reader().readVideo(uri).video()

        assertEquals(video.open().use { it.readBytes().size.toLong() }, video.sizeBytes)
    }

    @Test fun unknownSizeIsAcceptedAndCappedWhileStaging() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO, size = FixtureSize.NONE))

        val video = reader().readVideo(uri).video()

        assertNull(video.sizeBytes)
        val staging = ShareStaging(File(context.cacheDir, "reader-test-${System.nanoTime()}"))
        try {
            assertThrows(StagingLimitExceeded::class.java) {
                staging.stage(video.open, maxBytes = 1_024)
            }
            assertTrue(staging.directory.listFiles().isNullOrEmpty())
        } finally {
            staging.clear()
        }
    }

    @Test fun ungrantedSourceIsPrivate() {
        val uri = fixtures.uri(FixtureMedia.VIDEO)

        assertEquals(ShareProblem.PRIVATE, reader().readVideo(uri).problem())
    }

    @Test fun grantRevokedBeforeReadingIsPrivate() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))
        fixtures.revoke(uri)

        assertEquals(ShareProblem.PRIVATE, reader().readVideo(uri).problem())
    }

    @Test fun grantRevokedAfterAcceptanceStopsTheCopy() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))
        val video = reader().readVideo(uri).video()
        fixtures.revoke(uri)

        assertThrows(SecurityException::class.java) { video.open().close() }
    }

    @Test fun deletedSourceIsExpired() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.MISSING))

        assertEquals(ShareProblem.EXPIRED, reader().readVideo(uri).problem())
    }

    @Test fun oversizeVideoIsRejectedBeforeCopying() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))

        val read = reader(ShareLimits(maxBytes = 1_024)).readVideo(uri)

        assertEquals(ShareProblem.TOO_LARGE, read.problem())
    }

    @Test fun overlongVideoIsRejected() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))

        val read = reader(limits.copy(maxDurationMs = 100)).readVideo(uri)

        assertEquals(ShareProblem.TOO_LONG, read.problem())
    }

    @Test fun providerTypeMustBeVideo() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO, type = "application/pdf"))

        assertEquals(ShareProblem.NOT_VIDEO, reader().readVideo(uri).problem())
    }

    @Test fun sniffedContainerTypeWinsOverTheProviderClaim() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO, type = "video/quicktime"))

        assertEquals("video/mp4", reader().readVideo(uri).video().contentType)
    }

    @Test fun audioOnlyFileCalledVideoIsRejected() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.AUDIO, type = "video/mp4"))

        assertEquals(ShareProblem.NOT_VIDEO, reader().readVideo(uri).problem())
    }

    @Test fun textFileCalledVideoIsRejected() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.TEXT, type = "video/mp4"))

        assertEquals(ShareProblem.NOT_VIDEO, reader().readVideo(uri).problem())
    }

    @Test fun nonContentUriIsUnsupported() {
        val uri = Uri.fromFile(File(context.cacheDir, "video.mp4"))

        assertEquals(ShareProblem.UNSUPPORTED, reader().readVideo(uri).problem())
    }

    @Test fun sharedVideoIntentWithGrantedStreamIsAccepted() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))
        val intent = Intent(Intent.ACTION_SEND).setType("video/mp4")
            .putExtra(Intent.EXTRA_STREAM, uri)

        assertEquals("video/mp4", reader().read(intent).video().contentType)
    }

    @Test fun intentShapesOutsideOneVideoOrOneLinkAreRejected() {
        val uri = fixtures.grant(fixtures.uri(FixtureMedia.VIDEO))
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, "video/mp4")
        val image = Intent(Intent.ACTION_SEND).setType("image/png")
            .putExtra(Intent.EXTRA_STREAM, uri)
        val noStream = Intent(Intent.ACTION_SEND).setType("video/mp4")
        val linkWithText = Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, "Watch this https://example.org/video")

        assertEquals(ShareProblem.UNSUPPORTED, reader().read(view).problem())
        assertEquals(ShareProblem.UNSUPPORTED, reader().read(image).problem())
        assertEquals(ShareProblem.UNSUPPORTED, reader().read(noStream).problem())
        assertEquals(ShareProblem.INVALID_LINK, reader().read(linkWithText).problem())
    }

    @Test fun sharedLinkIsAccepted() {
        val intent = Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, " https://example.org/video?v=42 ")

        val read = reader().read(intent) as ShareRead.Accepted

        assertEquals(ShareCandidate.Link("https://example.org/video?v=42"), read.candidate)
    }
}
