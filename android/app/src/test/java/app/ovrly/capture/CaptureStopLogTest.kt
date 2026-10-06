package app.ovrly.capture

import java.io.IOException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureStopLogTest {
    @Test fun stoppingTheCaptureIsNotReportedAsAnInterruption() = runBlocking {
        val reports = mutableListOf<String>()
        val loop = launch(start = CoroutineStart.UNDISPATCHED) {
            guardPlaybackLoop({ message, _ -> reports += message }) { delay(Long.MAX_VALUE) }
        }
        loop.cancelAndJoin()
        assertTrue(loop.isCancelled)
        assertEquals(emptyList<String>(), reports)
    }

    @Test fun theCancellationOfAStopIsAnIllegalStateExceptionSoItMustBeExcluded() = runBlocking {
        var caught: Throwable? = null
        val loop = launch(start = CoroutineStart.UNDISPATCHED) {
            try {
                delay(Long.MAX_VALUE)
            } catch (error: CancellationException) {
                caught = error
                throw error
            }
        }
        loop.cancelAndJoin()
        assertTrue(caught is IllegalStateException)
    }

    @Test fun realPlaybackFailuresAreStillReported() = runBlocking {
        val reports = mutableListOf<String>()
        guardPlaybackLoop({ message, _ -> reports += message }) {
            throw IllegalStateException("recorder released")
        }
        guardPlaybackLoop({ message, _ -> reports += message }) {
            throw IOException("read failed")
        }
        assertEquals(
            listOf("Playback capture was interrupted.", "Playback capture stopped: read failed"),
            reports
        )
    }

    @Test fun stopIntentCarriesTheOverlayChoiceOnlyWhenTheExtraIsPresent() {
        assertEquals("app.ovrly.extra.CONTINUE_RESEARCH", CaptureService.EXTRA_CONTINUE_RESEARCH)
        assertEquals(true, CaptureControl.stopChoice(hasChoice = true, continueResearch = true))
        assertEquals(false, CaptureControl.stopChoice(hasChoice = true, continueResearch = false))
        assertEquals(null, CaptureControl.stopChoice(hasChoice = false, continueResearch = false))
    }
}
