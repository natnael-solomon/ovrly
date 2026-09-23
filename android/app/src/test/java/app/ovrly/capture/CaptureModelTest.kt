package app.ovrly.capture

import org.junit.Assert.*
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
}
