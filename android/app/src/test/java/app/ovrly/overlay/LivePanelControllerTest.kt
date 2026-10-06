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
            panel.setStopPrompt(true)
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
        controller.setStopPrompt(true)
        controller.setStopPrompt(false)
        controller.chooseStop(false)
        assertTrue(choices.isEmpty())
        assertNull(controller.state.value.stopChoice)
    }

    @Test fun collapsingThePanelIsNotStop() {
        controller.setExpanded(true)
        assertTrue(controller.state.value.expanded)
        controller.setExpanded(false)
        assertFalse(controller.state.value.expanded)
        assertFalse(controller.state.value.stopPrompt)
        assertTrue(choices.isEmpty())
    }

    /** The fixture's polls: the first has no claims, later ones add claims and updates. */
    private fun polls(): List<LiveResults> {
        val source = FixtureLiveResultsSource()
        return buildList {
            add(source.results.value)
            while (source.advance()) add(source.results.value)
        }
    }

    @Test fun theFirstClaimsExpandThePanelOnceAndItCollapsesUntouched() {
        val steps = polls()
        controller.onCaptureRunning(true)
        controller.onResults(steps[0])
        assertFalse(controller.state.value.expanded)
        controller.onResults(steps[1])
        assertTrue(controller.state.value.expanded)
        assertTrue(controller.state.value.autoCollapsePending)
        controller.autoCollapse()
        assertFalse(controller.state.value.expanded)
        // Later claims never expand it again in the same capture; they light the dot instead.
        steps.drop(2).forEach(controller::onResults)
        assertFalse(controller.state.value.expanded)
        assertTrue(controller.state.value.unseen)
        controller.setExpanded(true)
        assertFalse("opening the panel clears the dot", controller.state.value.unseen)
        // A new capture may expand once again; its source starts without claims.
        controller.onCaptureRunning(false)
        controller.onCaptureRunning(true)
        controller.onResults(LiveResults.NotConnected)
        controller.onResults(steps[1])
        assertTrue(controller.state.value.expanded)
    }

    @Test fun aSessionExpandsOnceEvenAcrossOverlayRestarts() {
        val steps = polls()
        val memory = AutoExpandMemory()
        val first = LivePanelController(memory) {}
        first.onCaptureRunning(true)
        first.onResults(steps[0], "session-1")
        first.onResults(steps[1], "session-1")
        assertTrue(first.state.value.expanded)

        // The overlay is hidden and shown again mid-capture: a new controller, same session.
        val again = LivePanelController(memory) {}
        again.onCaptureRunning(true)
        again.onResults(steps[0], "session-1")
        again.onResults(steps[1], "session-1")
        assertFalse(again.state.value.expanded)

        val next = LivePanelController(memory) {}
        next.onCaptureRunning(true)
        next.onResults(steps[0], "session-2")
        next.onResults(steps[1], "session-2")
        assertTrue("a new capture session expands once", next.state.value.expanded)
    }

    @Test fun aTouchKeepsTheAutomaticExpandOpen() {
        val steps = polls()
        controller.onResults(steps[0])
        controller.onResults(steps[1])
        controller.touched()
        assertFalse(controller.state.value.autoCollapsePending)
        controller.autoCollapse()
        assertTrue(controller.state.value.expanded)
    }

    @Test fun theAutomaticExpandIsSkippedAfterTheUserChoseOrWhileStopIsAsked() {
        val steps = polls()
        controller.onResults(steps[0])
        controller.setExpanded(true)
        controller.setExpanded(false)
        controller.onResults(steps[1])
        assertFalse("the user already decided", controller.state.value.expanded)

        val prompted = LivePanelController {}
        prompted.onResults(steps[0])
        prompted.setStopPrompt(true)
        prompted.onResults(steps[1])
        assertFalse("the Stop choice stays in front", prompted.state.value.expanded)
        assertTrue(prompted.state.value.stopPrompt)
    }

    @Test fun stopChoicesLeadToTheBubbleOrTheSavedPill() {
        val live = polls()[2]
        val keep = LivePanelController {}
        keep.setExpanded(true)
        keep.setStopPrompt(true)
        keep.chooseStop(true)
        assertFalse(keep.state.value.expanded)
        assertEquals(
            LiveOverlayForm.PILL,
            liveOverlayForm(keep.state.value, live, examining = true)
        )
        val continuing = live.copy(phase = LiveSessionPhase.CONTINUING)
        assertEquals(
            LiveOverlayForm.BUBBLE,
            liveOverlayForm(keep.state.value, continuing, examining = false)
        )
        keep.setExpanded(true)
        assertEquals(
            LiveOverlayForm.EXPANDED,
            liveOverlayForm(keep.state.value, continuing, examining = false)
        )

        val saved = LivePanelController {}
        saved.setStopPrompt(true)
        saved.chooseStop(false)
        assertEquals(
            LiveOverlayForm.SAVED,
            liveOverlayForm(saved.state.value, live, examining = true)
        )
    }

    @Test fun formsFollowCaptureAndConnection() {
        val idle = LivePanelState()
        val open = LivePanelState(expanded = true)
        val notConnected = LiveResults.NotConnected
        val live = polls()[1]
        assertNull(liveOverlayForm(idle, notConnected, examining = false))
        assertEquals(LiveOverlayForm.PILL, liveOverlayForm(idle, notConnected, examining = true))
        // Nothing to expand into until results are connected.
        assertEquals(LiveOverlayForm.PILL, liveOverlayForm(open, notConnected, examining = true))
        assertEquals(LiveOverlayForm.PILL, liveOverlayForm(idle, live, examining = true))
        assertEquals(LiveOverlayForm.EXPANDED, liveOverlayForm(open, live, examining = true))
        assertEquals(LiveOverlayForm.BUBBLE, liveOverlayForm(idle, live, examining = false))
    }

    @Test fun researchIsSettledOnlyWhenEveryContinuedClaimIsFinished() {
        val last = polls().last()
        val continuing = last.copy(phase = LiveSessionPhase.CONTINUING)
        assertFalse(researchSettled(last))
        assertFalse(researchSettled(continuing.copy(claims = emptyList())))
        assertFalse(
            researchSettled(
                continuing.copy(
                    claims = continuing.claims.map {
                        it.copy(state = LiveClaimState.CHECKING_EVIDENCE, assessment = null)
                    }
                )
            )
        )
        val settled = continuing.copy(
            claims = continuing.claims.map {
                it.copy(state = LiveClaimState.FAILED)
            }
        )
        assertTrue(researchSettled(settled))
        assertFalse(
            "keeping only available results is not research that finished",
            researchSettled(settled.copy(phase = LiveSessionPhase.KEEPING_AVAILABLE))
        )
    }

    @Test fun overlayHideActionIsNotTheCaptureStopAction() {
        assertNotEquals(CaptureService.STOP, OverlayService.HIDE)
        assertNotEquals(CaptureService.STOP, OverlayService.SHOW_LIVE_FIXTURE)
    }

    @Test fun captureEndingElsewhereClearsThePromptAndANewCaptureClearsTheChoice() {
        controller.setStopPrompt(true)
        controller.onCaptureRunning(false)
        assertFalse(controller.state.value.stopPrompt)
        controller.setStopPrompt(true)
        controller.chooseStop(true)
        controller.setStopPrompt(true)
        assertFalse("a sent choice is not asked again", controller.state.value.stopPrompt)
        controller.onCaptureRunning(true)
        assertNull(controller.state.value.stopChoice)
        assertEquals(listOf(true), choices)
    }

    @Test fun captureTicksWhileStopIsInFlightCannotReArmStop() {
        controller.onCaptureRunning(true)
        controller.setStopPrompt(true)
        controller.chooseStop(true)
        repeat(5) { controller.onCaptureRunning(true) }
        assertEquals(true, controller.state.value.stopChoice)
        controller.setStopPrompt(true)
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

        controller.setExpanded(false)
        controller.openClaim(LiveResultsFixture.SPEECH_CLAIM)
        assertTrue(controller.state.value.expanded)
        assertEquals(LiveResultsFixture.SPEECH_CLAIM, controller.state.value.detailClaimId)
        assertTrue(controller.state.value.notices.isEmpty())
        controller.openClaim(null)
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
        controller.setStopPrompt(true)
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
