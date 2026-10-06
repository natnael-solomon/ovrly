package app.ovrly.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureModelTest {
    @Test fun timerUsesMonotonicIntervalAndClamps() {
        assertEquals(0, CaptureLimits.elapsedSeconds(1000, 900))
        assertEquals(42, CaptureLimits.elapsedSeconds(1000, 43_999))
        assertEquals(180, CaptureLimits.elapsedSeconds(1000, Long.MAX_VALUE))
        assertFalse(CaptureLimits.expired(1000, 180_999))
        assertTrue(CaptureLimits.expired(1000, 181_000))
    }

    @Test fun sharedVideoHasTenMinuteBoundary() {
        assertFalse(CaptureLimits.acceptsSharedDuration(0))
        assertTrue(CaptureLimits.acceptsSharedDuration(600_000))
        assertFalse(CaptureLimits.acceptsSharedDuration(600_001))
    }

    @Test fun storageBoundRejectsOversizeAndOverflow() {
        assertTrue(CaptureLimits.fitsStorage(CaptureLimits.MAX_BYTES - 1, 1))
        assertFalse(CaptureLimits.fitsStorage(CaptureLimits.MAX_BYTES, 1))
        assertFalse(CaptureLimits.fitsStorage(2, Long.MAX_VALUE))
        assertFalse(CaptureLimits.fitsStorage(-1, 0))
    }

    @Test fun consentIsSeparateFromAudioAndDuplicateStartIsBlocked() {
        assertEquals(PermissionStep.AUDIO, nextCapturePermission(false, false))
        assertEquals(PermissionStep.PROJECTION, nextCapturePermission(true, false))
        assertEquals(PermissionStep.ALREADY_RUNNING, nextCapturePermission(false, true))
        assertTrue(CaptureState(phase = CapturePhase.STARTING).busy)
        assertTrue(CaptureState(phase = CapturePhase.RECORDING).busy)
        assertFalse(CaptureState(phase = CapturePhase.FINISHED).busy)
    }

    @Test fun captureTransitionsPreventTokenReuseAndDoubleCleanup() {
        val lifecycle = CaptureLifecycle()
        assertFalse(lifecycle.recording())
        assertTrue(lifecycle.begin())
        assertFalse(lifecycle.begin())
        assertTrue(lifecycle.recording())
        assertFalse(lifecycle.recording())
        assertTrue(lifecycle.stop())
        assertFalse(lifecycle.stop())
        assertFalse(lifecycle.begin())
        assertEquals(CaptureLifecycle.Stage.STOPPED, lifecycle.stage)
    }

    @Test fun deniedOrInterruptedSetupStillCleansUpExactlyOnce() {
        val lifecycle = CaptureLifecycle()
        assertTrue(lifecycle.begin())
        assertTrue(lifecycle.stop())
        assertFalse(lifecycle.recording())
        assertFalse(lifecycle.stop())
        val missingIntent = CaptureLifecycle()
        assertTrue(missingIntent.stop())
        assertFalse(missingIntent.begin())
    }

    @Test fun chunkGridIsTenSecondsAndEndsExactlyAtThreeMinutes() {
        assertEquals(10_000L, CaptureLimits.CHUNK_MS)
        assertEquals(0, ChunkGrid.seqAt(0))
        assertEquals(0, ChunkGrid.seqAt(9_999))
        assertEquals(1, ChunkGrid.seqAt(10_000))
        assertEquals(17, ChunkGrid.seqAt(179_999))
        assertEquals(17, ChunkGrid.seqAt(500_000))
        assertEquals(0, ChunkGrid.seqAt(-5))
        assertEquals(17, ChunkGrid.lastSeq)
        assertEquals(170_000L, ChunkGrid.startOf(17))
        assertEquals(180_000L, ChunkGrid.endOf(17))
        (0 until ChunkGrid.lastSeq).forEach {
            assertEquals(ChunkGrid.endOf(it), ChunkGrid.startOf(it + 1))
        }
    }

    @Test fun uploadSummarySaysWhatIsStillOnTheDevice() {
        assertNull(UploadProgress().summary())
        assertEquals(
            "Saved on device, not yet sent: 2 of 3 chunks.",
            UploadProgress(3, 1, UploadStatus.PENDING).summary()
        )
        assertEquals(
            "Sending to the in-memory test server: 1 of 3 chunks sent.",
            UploadProgress(3, 1, UploadStatus.SENDING, testServer = true).summary()
        )
        assertEquals(
            "Saved on device, not yet sent: 3 of 3 chunks. Not configured.",
            UploadProgress(3, 0, UploadStatus.NOT_SENT, detail = "Not configured.").summary()
        )
        assertEquals(
            "Upload closed; research will not continue. 1 of 3 chunks were sent.",
            UploadProgress(3, 1, UploadStatus.CLOSED, continueResearch = false).summary()
        )
        assertEquals(
            "Saved on device, not yet sent: 2 of 2 chunks. Waiting for Wi-Fi.",
            UploadProgress(2, 0, UploadStatus.PENDING, wifiOnly = true).summary()
        )
        val state = CaptureState(
            message = "Stopped.",
            upload = UploadProgress(3, 1, UploadStatus.PENDING)
        )
        assertEquals(
            "Stopped. Saved on device, not yet sent: 2 of 3 chunks.",
            state.displayMessage
        )
    }

    @Test fun continuationChoiceIsAskedOnlyAfterStopWithChunks() {
        val sent = UploadProgress(2, 2, UploadStatus.SENT)
        val stopped = CaptureState(
            phase = CapturePhase.FINISHED,
            hasLocalCapture = true,
            upload = sent
        )
        assertTrue(stopped.needsContinuationChoice)
        assertFalse(stopped.copy(phase = CapturePhase.RECORDING).needsContinuationChoice)
        assertFalse(
            stopped.copy(upload = sent.copy(continueResearch = true)).needsContinuationChoice
        )
        assertFalse(stopped.copy(upload = UploadProgress()).needsContinuationChoice)
    }

    @Test fun uploadStateIsIgnoredWithoutACapture() {
        CaptureStore.set(CaptureState())
        CaptureStore.upload(UploadProgress(1, 0, UploadStatus.PENDING))
        assertEquals(UploadStatus.NONE, CaptureStore.state.value.upload.status)
        CaptureStore.set(CaptureState(phase = CapturePhase.RECORDING))
        CaptureStore.upload(UploadProgress(1, 0, UploadStatus.PENDING))
        assertEquals(UploadStatus.PENDING, CaptureStore.state.value.upload.status)
        CaptureStore.set(CaptureState())
    }
}
