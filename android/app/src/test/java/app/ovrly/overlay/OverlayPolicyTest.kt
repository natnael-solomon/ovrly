package app.ovrly.overlay

import org.junit.Assert.assertEquals
import org.junit.Test

class OverlayPolicyTest {
    @Test fun preAndroid12AlwaysUsesFallback() {
        for (sdk in 29..30) {
            assertEquals(BlurMode.FALLBACK, blurMode(sdk, true, false))
            assertEquals(BlurMode.FALLBACK, blurMode(sdk, false, false))
        }
    }

    @Test fun nativeBlurRequiresSystemSupportAtRuntime() {
        for (sdk in 31..36) {
            assertEquals(BlurMode.NATIVE, blurMode(sdk, true, false))
            assertEquals(BlurMode.FALLBACK, blurMode(sdk, false, false))
        }
    }

    @Test fun readabilityOverrideWinsEvenOnSupportedDevices() {
        for (sdk in 29..36) {
            for (supported in listOf(false, true)) {
                assertEquals(BlurMode.OPAQUE, blurMode(sdk, supported, true))
            }
        }
    }

    @Test fun realSessionsRequireExplicitConfirmation() {
        assertEquals(DemoEntry.CONFIRM_STOP, demoEntry(true, false, false))
        assertEquals(DemoEntry.CONFIRM_STOP, demoEntry(false, true, false))
        assertEquals(DemoEntry.CONFIRM_STOP, demoEntry(true, true, false))
        assertEquals(DemoEntry.READY, demoEntry(false, false, false))
    }

    @Test fun storageMustSettleBeforeChangingModes() {
        for (capture in listOf(false, true)) {
            for (voice in listOf(false, true)) {
                assertEquals(DemoEntry.WAIT_FOR_STORAGE, demoEntry(capture, voice, true))
            }
        }
    }
}
