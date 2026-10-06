package app.ovrly.capture

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.NetworkType
import androidx.work.WorkInfo
import androidx.work.WorkManager
import app.ovrly.testing.Device
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The upload side of capture on a device: the Wi-Fi-only preference, the WorkManager chain it
 * reschedules, and [CaptureUploadWorker] sending a finished capture to the debug build's
 * in-memory server and closing it with the user's choice. No network request leaves the device.
 */
@RunWith(AndroidJUnit4::class)
class CaptureUploadsTest {
    private val context: Context get() = Device.context
    private val workManager get() = WorkManager.getInstance(context)
    private val api = InMemoryCaptureSessionApi()

    @Before fun cleanSlate() {
        reset()
        CaptureApis.replace(api)
    }

    @After fun cleanUp() {
        reset()
        CapturePreferences.setWifiOnly(context, false)
        CaptureApis.replace(null)
    }

    @Test fun wifiOnlyIsSavedAndReschedulesTheUploadOnUnmeteredNetworks() {
        // Offline, the first request keeps retrying, so the replaced one is cancelled.
        api.offline = true
        val session = recordCapture()

        CapturePreferences.setWifiOnly(context, true)

        assertTrue(CapturePreferences.wifiOnly(context))
        assertEquals(NetworkType.UNMETERED, pending(session).constraints.requiredNetworkType)
        assertTrue(CaptureStore.state.value.upload.wifiOnly)

        CapturePreferences.setWifiOnly(context, false)

        assertFalse(CapturePreferences.wifiOnly(context))
        assertEquals(NetworkType.CONNECTED, pending(session).constraints.requiredNetworkType)
    }

    @Test fun wifiOnlyWithoutACaptureOnlySavesThePreference() {
        CapturePreferences.setWifiOnly(context, true)

        assertTrue(CapturePreferences.wifiOnly(context))
        val scheduled = workManager.getWorkInfosByTag(CaptureUploadWorker::class.java.name).get()
        assertTrue(scheduled.isEmpty())
    }

    @Test fun workerSendsEveryChunkAndClosesWithTheChoice() {
        val session = recordCapture()

        assertTrue(CaptureControl.stop(context, continueResearch = true))

        Device.await("the upload to close", UPLOAD_WAIT_MS) {
            CaptureLedger(CaptureFiles.rootOf(context), session).closed
        }
        val ledger = CaptureLedger(CaptureFiles.rootOf(context), session)
        val manifest = checkNotNull(CaptureFiles.manifestOrNull(CaptureFiles.rootOf(context)))
        assertTrue(manifest.chunks.isNotEmpty())
        assertTrue(manifest.chunks.all { ledger.isSent(it.seq) })
        assertEquals(true, ledger.choice)
        Device.await("the worker to finish") {
            workManager.getWorkInfosForUniqueWork("capture-upload-$session").get()
                .all { it.state.isFinished }
        }
    }

    @Test fun choosingNotToContinueClosesWithoutSendingMore() {
        val session = recordCapture()

        assertTrue(CaptureControl.stop(context, continueResearch = false))

        Device.await("the upload to close", UPLOAD_WAIT_MS) {
            CaptureLedger(CaptureFiles.rootOf(context), session).closed
        }
        val ledger = CaptureLedger(CaptureFiles.rootOf(context), session)
        assertEquals(false, ledger.choice)
        assertEquals(UploadStatus.CLOSED, CaptureStore.state.value.upload.status)
        assertFalse("the first choice wins", CaptureControl.stop(context, continueResearch = true))
    }

    /** A finished two-chunk capture with audio, as CaptureService leaves it on Stop. */
    private fun recordCapture(): String {
        val files = CaptureFiles(context)
        files.begin()
        val audio = ByteArray(AUDIO_BYTES) { (it % 64).toByte() }
        files.writeAudio(audio, audio.size, 1_000)
        files.advance(CaptureLimits.CHUNK_MS + 1)
        files.writeAudio(audio, audio.size, CaptureLimits.CHUNK_MS + 1_000)
        files.finish(CaptureLimits.CHUNK_MS + 2_000, "Capture stopped.", signal = true)
        CaptureStore.set(CaptureState(phase = CapturePhase.FINISHED, hasLocalCapture = true))
        val session = checkNotNull(files.sessionId)
        CaptureUploads.schedule(context, session)
        return session
    }

    /** The chain's current request; a replaced request is cancelled. */
    private fun pending(session: String): WorkInfo = workManager
        .getWorkInfosForUniqueWork("capture-upload-$session").get()
        .single { it.state != WorkInfo.State.CANCELLED }

    private fun reset() {
        workManager.cancelAllWork().result.get()
        workManager.pruneWork().result.get()
        CaptureFiles(context).delete()
        CaptureStore.set(CaptureState())
    }

    private companion object {
        const val AUDIO_BYTES = 3_200
        const val UPLOAD_WAIT_MS = 30_000L
    }
}
