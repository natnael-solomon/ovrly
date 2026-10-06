package app.ovrly.overlay

import app.ovrly.capture.CaptureService
import app.ovrly.ui.DemoClaims
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LivePanelControllerTest {
    private val choices = mutableListOf<Boolean>()
    private val controller = LivePanelController { choices += it }

    @Test fun stopChoiceInvokesTheCallbackWithEachValueOnce() {
        for (continueResearch in listOf(true, false)) {
            val sent = mutableListOf<Boolean>()
            val panel = LivePanelController { sent += it }
            panel.requestStop()
            assertTrue(panel.state.value.stopPrompt)
            panel.chooseStop(continueResearch)
            panel.chooseStop(!continueResearch)
            assertEquals(listOf(continueResearch), sent)
            assertEquals(continueResearch, panel.state.value.stopChoice)
            assertFalse(panel.state.value.stopPrompt)
        }
    }

    @Test fun stopNeedsThePromptAndCanBeCancelled() {
        controller.chooseStop(true)
        controller.requestStop()
        controller.cancelStop()
        controller.chooseStop(false)
        assertTrue(choices.isEmpty())
        assertNull(controller.state.value.stopChoice)
    }

    @Test fun hidingThePanelIsNotStop() {
        controller.setPanelVisible(false)
        assertFalse(controller.state.value.panelVisible)
        assertFalse(controller.state.value.stopPrompt)
        assertTrue(choices.isEmpty())
        controller.setPanelVisible(true)
        assertTrue(controller.state.value.panelVisible)
        assertTrue(choices.isEmpty())
    }

    @Test fun overlayHideActionIsNotTheCaptureStopAction() {
        assertNotEquals(CaptureService.STOP, OverlayService.HIDE)
        assertNotEquals(CaptureService.STOP, OverlayService.SHOW_LIVE_FIXTURE)
    }

    @Test fun captureEndingElsewhereClearsThePromptAndANewCaptureClearsTheChoice() {
        controller.requestStop()
        controller.onCaptureRunning(false)
        assertFalse(controller.state.value.stopPrompt)
        controller.requestStop()
        controller.chooseStop(true)
        controller.requestStop()
        assertFalse("a sent choice is not asked again", controller.state.value.stopPrompt)
        controller.onCaptureRunning(true)
        assertNull(controller.state.value.stopChoice)
        assertEquals(listOf(true), choices)
    }

    @Test fun captureTicksWhileStopIsInFlightCannotReArmStop() {
        controller.onCaptureRunning(true)
        controller.requestStop()
        controller.chooseStop(true)
        repeat(5) { controller.onCaptureRunning(true) }
        assertEquals(true, controller.state.value.stopChoice)
        controller.requestStop()
        controller.chooseStop(false)
        assertFalse(controller.state.value.stopPrompt)
        assertEquals(listOf(true), choices)

        controller.onCaptureRunning(false)
        controller.onCaptureRunning(true)
        assertNull(controller.state.value.stopChoice)
    }

    @Test fun updateNoticeAppearsOnceAndOpensClaimDetail() {
        val source = FixtureLiveResultsSource()
        controller.onResults(source.results.value)
        while (source.advance()) {
            controller.onResults(source.results.value)
            if (source.results.value.claims.any { it.change != null }) break
        }
        assertEquals(listOf(LiveResultsFixture.SPEECH_CLAIM), controller.state.value.notices)
        controller.onResults(source.results.value)
        assertEquals(listOf(LiveResultsFixture.SPEECH_CLAIM), controller.state.value.notices)

        controller.setPanelVisible(false)
        controller.openClaim(LiveResultsFixture.SPEECH_CLAIM)
        assertTrue(controller.state.value.panelVisible)
        assertEquals(LiveResultsFixture.SPEECH_CLAIM, controller.state.value.detailClaimId)
        assertTrue(controller.state.value.notices.isEmpty())
        controller.closeClaim()
        assertNull(controller.state.value.detailClaimId)
    }

    @Test fun dismissingANoticeKeepsTheUpdatedState() {
        val source = FixtureLiveResultsSource()
        while (source.advance()) controller.onResults(source.results.value)
        controller.dismissNotice(LiveResultsFixture.SPEECH_CLAIM)
        assertTrue(controller.state.value.notices.isEmpty())
        val claim = source.results.value.claims.single { it.id == LiveResultsFixture.SPEECH_CLAIM }
        assertEquals(LiveClaimState.UPDATED, claim.state)
    }

    @Test fun resetForgetsNoticesAndChoices() {
        val source = FixtureLiveResultsSource()
        while (source.advance()) controller.onResults(source.results.value)
        controller.requestStop()
        controller.reset()
        assertEquals(LivePanelState(), controller.state.value)
    }

    @Test fun demoContentCarriesNoLiveData() {
        val live = FixtureLiveResultsSource().apply { repeat(3) { advance() } }.results.value
        val demo = overlayContent(true, live, FixtureLiveResultsSource.LABEL)
        assertEquals(OverlayContent.Demo, demo)
        assertTrue(OverlayContent.Demo::class.java.declaredFields.none { it.name == "results" })
        val compact = overlayContent(false, live, null)
        assertEquals(OverlayContent.Live(live, null), compact)
    }

    @Test fun demoPanelSignatureAcceptsNoLiveOrContractData() {
        val panels = Class.forName("app.ovrly.ui.DemoOverlayPanelKt").declaredMethods
            .filter { it.name.startsWith("DemoOverlayPanel") }
        assertTrue(panels.isNotEmpty())
        for (method in panels) {
            for (type in method.parameterTypes) {
                val name = type.name
                val message = "$name reaches DemoOverlayPanel"
                assertFalse(message, name.startsWith("app.ovrly.overlay."))
                assertFalse(message, name.startsWith("app.ovrly.contract."))
                assertFalse(message, List::class.java.isAssignableFrom(type))
            }
        }
    }

    @Test fun demoClaimsAreLabelledSamples() {
        assertTrue(DemoClaims.all { it.label.endsWith("/ sample") })
        val reports = listOf(LiveResultsFixture.provisionalReport, LiveResultsFixture.updatedReport)
        val fixtureTexts = reports
            .flatMap { report -> report.claims.map { it.proposition } }
        assertTrue(DemoClaims.none { it.title in fixtureTexts })
    }
}
