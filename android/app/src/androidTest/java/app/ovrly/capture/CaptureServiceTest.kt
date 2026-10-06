package app.ovrly.capture

import android.Manifest
import android.app.Activity
import android.app.ActivityManager
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.media.AudioManager
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkManager
import app.ovrly.testing.Device
import app.ovrly.testing.ProjectionConsent
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters

/**
 * [CaptureService] on an emulator with a real MediaProjection: start, Stop with and without
 * the continue-research choice, the 3-minute limit, denied permissions and a projection that
 * the system stops mid-capture. After every stop the virtual display, playback recorder,
 * foreground notification and service must be gone.
 *
 * Methods run in name order, so the audio-permission denial (first by name) runs before any
 * test grants RECORD_AUDIO. Revoking a runtime permission kills the app process, so it is
 * never revoked.
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class CaptureServiceTest {
    @get:Rule val consent = ProjectionConsent()

    private val context: Context get() = Device.context
    private var displaysBefore = 0

    @Before fun cleanSlate() {
        reset()
        if (Build.VERSION.SDK_INT >= TIRAMISU) grant(Manifest.permission.POST_NOTIFICATIONS)
        displaysBefore = allCaptureDisplays()
    }

    @After fun cleanUp() {
        if (serviceRunning()) CaptureControl.stop(context)
        Device.await("capture service to stop") { !serviceRunning() }
        reset()
    }

    @Test fun audioPermissionDeniedFailsBeforeRecording() {
        assumeTrue(
            "RECORD_AUDIO was granted earlier in this run and cannot be revoked in-process",
            !audioGranted()
        )

        ContextCompat.startForegroundService(context, consent.startIntent(consent.request()))

        val state = awaitPhase(CapturePhase.ERROR)
        assertTrue(state.message, state.message.startsWith(DENIED))
        assertReleased()
    }

    @Test fun consentNotGrantedFails() {
        grant(Manifest.permission.RECORD_AUDIO)
        val intent = consent.startIntent(consent.request(), Activity.RESULT_CANCELED)

        ContextCompat.startForegroundService(context, intent)

        val state = awaitPhase(CapturePhase.ERROR)
        assertTrue(state.message, state.message.startsWith(DENIED))
        assertReleased()
    }

    @Test fun startRecordsAndStopReleasesEverything() {
        startCapture()
        Device.await("the first sealed chunk", CHUNK_WAIT_MS) {
            CaptureStore.state.value.upload.chunks >= 1
        }

        CaptureControl.stop(context)

        val state = awaitPhase(CapturePhase.FINISHED)
        assertTrue(state.message, state.message.startsWith(CaptureState.STOPPED))
        assertTrue(state.hasLocalCapture)
        assertReleased()
    }

    @Test fun stopWithChoiceRecordsItAndReleases() {
        startCapture()
        Device.await("the first sealed chunk", CHUNK_WAIT_MS) {
            CaptureStore.state.value.upload.chunks >= 1
        }

        assertTrue(CaptureControl.stop(context, continueResearch = false))

        awaitPhase(CapturePhase.FINISHED)
        assertReleased()
        Device.await("the recorded choice") {
            CaptureStore.state.value.upload.continueResearch == false
        }
        assertFalse(
            "a different choice must not replace the first",
            CaptureControl.stop(context, continueResearch = true)
        )
    }

    @Test fun projectionStoppedBySystemFinishesAndReleases() {
        startCapture()

        // A newer projection makes the system stop the running one, the same callback as the
        // user revoking screen capture from the system UI.
        val takeover = consent.request()
        val manager = context.getSystemService(MediaProjectionManager::class.java)
        var other: MediaProjection? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            other = manager.getMediaProjection(takeover.resultCode, checkNotNull(takeover.data))
        }
        try {
            val state = awaitPhase(CapturePhase.FINISHED)
            assertTrue(state.message, state.message.contains("revoked or interrupted"))
            assertFalse(state.message, state.message.contains("Cleanup issue"))
            // The takeover projection belongs to this package too, so the projection check is
            // skipped. On Android 10 (API 29) the platform keeps the display of a projection it
            // stopped until the owning process dies, and the app's release() can no longer
            // remove it, so the display check runs from Android 11 (API 30) on. The same release
            // code is checked strictly on every app-initiated stop path on both API levels.
            assertReleased(
                projectionCheck = false,
                displayCheck = Build.VERSION.SDK_INT >= ANDROID_11
            )
        } finally {
            InstrumentationRegistry.getInstrumentation().runOnMainSync { other?.stop() }
        }
    }

    @LargeTest
    @Test fun threeMinuteLimitStopsAndReleases() {
        startCapture()

        val state = awaitPhase(CapturePhase.FINISHED, CaptureLimits.LIVE_MS + LIMIT_SLACK_MS)

        assertTrue(state.message, state.message.startsWith(CaptureMedia.LIMIT_REACHED))
        assertEquals((CaptureLimits.LIVE_MS / 1_000).toInt(), state.seconds)
        val manifest = checkNotNull(CaptureFiles.manifestOrNull(CaptureFiles.rootOf(context)))
        assertEquals(CaptureLimits.LIVE_MS, manifest.durationMs)
        assertTrue(manifest.finished)
        assertReleased()
    }

    private fun startCapture() {
        grant(Manifest.permission.RECORD_AUDIO)
        displaysBefore = allCaptureDisplays()
        ContextCompat.startForegroundService(context, consent.startIntent(consent.request()))
        awaitPhase(CapturePhase.RECORDING)
        assertTrue("foreground notification", notificationShown())
        assertTrue("service running", serviceRunning())
        Device.await("the projection's virtual display") {
            allCaptureDisplays() == displaysBefore + 1
        }
        Device.await("the app's projection in dumpsys media_projection") { projectionActive() }
    }

    /** Waits for [phase]; an unexpected error ends the wait at once with its message. */
    private fun awaitPhase(phase: CapturePhase, timeoutMs: Long = 20_000): CaptureState {
        Device.await("capture phase $phase", timeoutMs) {
            val state = CaptureStore.state.value
            check(phase == CapturePhase.ERROR || state.phase != CapturePhase.ERROR) {
                "Capture failed while waiting for $phase: ${state.message}"
            }
            state.phase == phase
        }
        return CaptureStore.state.value
    }

    /**
     * Every stop path must release what start acquired. [displayCheck] proves the app called
     * VirtualDisplay.release(): only that removes the display, so the count must return to its
     * value before start. [projectionCheck] proves MediaProjection.stop(): the platform then no
     * longer lists a projection for this package.
     */
    private fun assertReleased(projectionCheck: Boolean = true, displayCheck: Boolean = true) {
        Device.await("the capture service to stop") { !serviceRunning() }
        if (displayCheck) {
            Device.await("VirtualDisplay.release() to remove the display") {
                allCaptureDisplays() == displaysBefore
            }
        }
        if (projectionCheck) {
            Device.await("MediaProjection.stop() to end the projection") { !projectionActive() }
        }
        assertTrue(
            "playback recorder still active",
            context.getSystemService(AudioManager::class.java).activeRecordingConfigurations
                .isEmpty()
        )
        assertFalse("foreground notification", notificationShown())
    }

    /**
     * Capture displays the platform holds. Counts are compared with [displaysBefore], so a
     * display Android 10 kept from an earlier system-stopped projection is not counted twice.
     */
    private fun allCaptureDisplays(): Int = context
        .getSystemService(DisplayManager::class.java).displays
        .count { it.name == DISPLAY_NAME }

    /**
     * True while the platform lists a media projection owned by this package; MediaProjection
     * dumps its owner as `(<package>, uid=<uid>)`. startCapture checks this is true while
     * recording, so a changed dump format fails instead of passing vacuously.
     */
    private fun projectionActive(): Boolean =
        "(${Device.PACKAGE}, uid=" in Device.shell("dumpsys media_projection")

    private fun notificationShown(): Boolean = context
        .getSystemService(NotificationManager::class.java).activeNotifications
        .any { it.id == NOTIFICATION_ID }

    @Suppress("DEPRECATION")
    private fun serviceRunning(): Boolean = context
        .getSystemService(ActivityManager::class.java).getRunningServices(Int.MAX_VALUE)
        .any { it.service.className == CaptureService::class.java.name }

    private fun audioGranted(): Boolean = ContextCompat.checkSelfPermission(
        context,
        Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    private fun grant(permission: String) {
        InstrumentationRegistry.getInstrumentation().uiAutomation
            .grantRuntimePermission(Device.PACKAGE, permission)
    }

    private fun reset() {
        WorkManager.getInstance(context).cancelAllWork().result.get()
        CaptureFiles(context).delete()
        CaptureStore.set(CaptureState())
    }

    private companion object {
        const val TIRAMISU = 33
        const val ANDROID_11 = 30
        const val DISPLAY_NAME = "ovrly-selected-interval"
        const val DENIED = "Capture permission or source access was denied."
        const val NOTIFICATION_ID = 101
        const val CHUNK_WAIT_MS = 25_000L
        const val LIMIT_SLACK_MS = 30_000L
    }
}
