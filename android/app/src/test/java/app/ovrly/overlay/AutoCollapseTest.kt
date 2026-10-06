package app.ovrly.overlay

import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AutoCollapseTest {
    private val steps: List<LiveResults> = FixtureLiveResultsSource().let { source ->
        buildList {
            add(source.results.value)
            while (source.advance()) add(source.results.value)
        }
    }

    /** Auto-expands [controller], runs the timer with a short delay, then waits it out. */
    private fun autoExpandAndWait(
        controller: LivePanelController,
        touchExploration: Boolean,
        touch: Boolean = false
    ) = runBlocking {
        val timer = launch { runAutoCollapse(controller, { touchExploration }, delayMs = DELAY) }
        controller.onResults(steps[0])
        controller.onResults(steps[1])
        assertTrue(controller.state.value.expanded)
        if (touch) controller.touched()
        delay(DELAY * 4)
        timer.cancel()
    }

    @Test fun theAutomaticExpandCollapsesWhenUntouched() {
        val controller = LivePanelController {}
        autoExpandAndWait(controller, touchExploration = false)
        assertFalse(controller.state.value.expanded)
    }

    @Test fun itNeverCollapsesWhileTalkBackIsOn() {
        val controller = LivePanelController {}
        autoExpandAndWait(controller, touchExploration = true)
        assertTrue(controller.state.value.expanded)
    }

    @Test fun aTouchStopsTheTimer() {
        val controller = LivePanelController {}
        autoExpandAndWait(controller, touchExploration = false, touch = true)
        assertTrue(controller.state.value.expanded)
    }

    private companion object {
        const val DELAY = 50L
    }
}
