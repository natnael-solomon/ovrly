package app.ovrly.voice

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSessionPolicyTest {
    private val scope = CoroutineScope(Dispatchers.Unconfined)

    @After fun cancelCollectors() = scope.cancel()

    @Test fun inputWindowCountsFromReadyAndIdleCutoffEndsImmediately() {
        val fixture = Fixture()
        fixture.session.start()
        fixture.advanceTo(20_000)
        fixture.ready()
        var now = 20_000L
        while (now < 310_000L) {
            now += 10_000
            fixture.advanceTo(now)
            fixture.speak()
            fixture.message("""{"type":"turn_complete"}""")
        }
        fixture.advanceTo(319_999)
        assertTrue(fixture.session.state.value.active)
        fixture.advanceTo(320_000)
        assertEquals("Session ended", fixture.session.state.value.status)
        assertEquals(VoiceInteractionPhase.IDLE, fixture.session.state.value.phase)
        assertEquals(1, fixture.transport.closes)
        assertFalse(fixture.events.contains(VoiceOrbEvent.Shake))
    }

    @Test fun finishingStopsMicrophoneRejectsToolsAndWaitsForReplyCompletion() {
        val fixture = Fixture().live()
        fixture.speak()
        fixture.message("""{"type":"audio","data":"AAA="}""")
        fixture.advanceTo(VoiceConfiguration.INPUT_WINDOW_MILLIS)
        val state = fixture.session.state.value
        assertEquals("Finishing reply · microphone off", state.status)
        assertEquals(VoiceInteractionPhase.FINISHING, state.phase)
        assertEquals(1, fixture.audio.inputStops)
        fixture.message(
            """{"type":"tool_call","id":"late","name":"open_tab","args":{"tab":"explore"}}"""
        )
        assertEquals(0, fixture.navigations)
        val result = JSONObject(fixture.transport.sent.last()).getJSONObject("result")
        assertEquals("error", result.getString("status"))
        val sent = fixture.transport.sent.size
        fixture.audio.input(LOUD)
        assertEquals(sent, fixture.transport.sent.size)
        fixture.audio.drained()
        assertTrue("Playback gaps must not end the reply early", fixture.session.state.value.active)
        fixture.message("""{"type":"turn_complete"}""")
        assertEquals("Session ended", fixture.session.state.value.status)
    }

    @Test fun finishingGraceIsCappedAtThirtySeconds() {
        val fixture = Fixture().live()
        fixture.speak()
        fixture.advanceTo(VoiceConfiguration.INPUT_WINDOW_MILLIS)
        assertEquals(VoiceInteractionPhase.FINISHING, fixture.session.state.value.phase)
        fixture.advanceTo(VoiceConfiguration.INPUT_WINDOW_MILLIS + 29_999)
        assertTrue(fixture.session.state.value.active)
        fixture.advanceTo(VoiceConfiguration.INPUT_WINDOW_MILLIS + 30_000)
        assertEquals("Session ended", fixture.session.state.value.status)
        assertEquals(1, fixture.transport.starts)
    }

    @Test fun fifteenSecondsOfListeningSilenceEndsWithoutError() {
        val fixture = Fixture().live()
        fixture.advanceTo(14_999)
        fixture.audio.input(QUIET)
        assertTrue(fixture.session.state.value.active)
        fixture.advanceTo(15_000)
        assertEquals("Voice ended", fixture.session.state.value.status)
        assertEquals(VoiceInteractionPhase.IDLE, fixture.session.state.value.phase)
        assertFalse(fixture.events.contains(VoiceOrbEvent.Shake))
        assertEquals(1, fixture.transport.starts)
    }

    @Test fun speechThinkingAndRepliesPauseSilenceUntilListeningResumes() {
        val fixture = Fixture().live()
        fixture.advanceTo(10_000)
        fixture.speak()
        fixture.advanceTo(50_000)
        assertTrue(fixture.session.state.value.active)
        fixture.message("""{"type":"audio","data":"AAA="}""")
        fixture.advanceTo(80_000)
        assertTrue(fixture.session.state.value.active)
        fixture.audio.drained()
        fixture.advanceTo(94_999)
        assertTrue(fixture.session.state.value.active)
        fixture.advanceTo(95_000)
        assertEquals("Voice ended", fixture.session.state.value.status)
    }

    @Test fun thinkingIsAVisualHintAfterSpeechThenQuiet() {
        val fixture = Fixture().live()
        fixture.advanceTo(1_000)
        fixture.speak()
        fixture.speak()
        assertEquals(1, fixture.events.count { it == VoiceOrbEvent.Onset })
        fixture.advanceTo(1_599)
        assertEquals(VoiceInteractionPhase.LISTENING, fixture.session.state.value.phase)
        fixture.advanceTo(1_600)
        assertEquals(VoiceInteractionPhase.THINKING, fixture.session.state.value.phase)
        assertEquals(0f, fixture.session.level.value)
        fixture.speak()
        assertEquals(VoiceInteractionPhase.LISTENING, fixture.session.state.value.phase)
        fixture.message("""{"type":"audio","data":"AAA="}""")
        assertEquals(VoiceInteractionPhase.SPEAKING, fixture.session.state.value.phase)
    }

    @Test fun releasingHoldToStartBeforeReadyCancelsTheStart() {
        val fixture = Fixture()
        fixture.session.holdStart()
        assertEquals(VoiceInteractionPhase.CONNECTING, fixture.session.state.value.phase)
        fixture.session.holdEnd()
        assertFalse(fixture.session.state.value.active)
        assertEquals("Voice start cancelled.", fixture.session.state.value.message)
        assertEquals(1, fixture.transport.closes)
        assertEquals(0, fixture.audio.starts)
        assertFalse(fixture.events.contains(VoiceOrbEvent.Shake))
    }

    @Test fun holdPausesSilenceAndReleaseResumesContinuousListening() {
        val fixture = Fixture()
        fixture.session.holdStart()
        fixture.ready()
        fixture.advanceTo(40_000)
        assertTrue(fixture.session.state.value.active)
        fixture.session.holdEnd()
        assertTrue(fixture.session.state.value.active)
        fixture.audio.input(QUIET)
        assertEquals("audio_input", JSONObject(fixture.transport.sent.last()).getString("type"))
        fixture.advanceTo(54_999)
        assertTrue(fixture.session.state.value.active)
        fixture.advanceTo(55_000)
        assertEquals("Voice ended", fixture.session.state.value.status)
    }

    @Test fun holdIsIgnoredWhileSpeaking() {
        val fixture = Fixture().live()
        fixture.message("""{"type":"audio","data":"AAA="}""")
        fixture.session.holdStart()
        fixture.audio.drained()
        fixture.advanceTo(15_000)
        assertEquals("Voice ended", fixture.session.state.value.status)
    }

    @Test fun userInterruptFlushesPlaybackAndSendsWireInterrupt() {
        val fixture = Fixture().live()
        fixture.session.interrupt()
        assertTrue(fixture.transport.sent.isEmpty())
        fixture.message("""{"type":"audio","data":"AAA="}""")
        fixture.session.interrupt()
        assertEquals("interrupt", JSONObject(fixture.transport.sent.last()).getString("type"))
        assertEquals(1, fixture.audio.interrupts)
        assertEquals(VoiceInteractionPhase.LISTENING, fixture.session.state.value.phase)
        assertTrue(fixture.events.contains(VoiceOrbEvent.Interrupt))
    }

    @Test fun levelsAreRateLimitedAndBurstsAreSpacedHalfASecond() {
        val fixture = Fixture().live()
        fixture.audio.input(LOUD)
        val first = fixture.session.level.value
        assertTrue(first > 0f)
        fixture.audio.input(QUIET)
        assertEquals(first, fixture.session.level.value)
        fixture.advanceTo(50)
        fixture.audio.input(QUIET)
        assertEquals(0f, fixture.session.level.value)
        repeat(3) { fixture.message("""{"type":"audio","data":"QB9AHw=="}""") }
        fixture.advanceTo(550)
        fixture.message("""{"type":"audio","data":"QB9AHw=="}""")
        assertEquals(2, fixture.events.count { it is VoiceOrbEvent.Burst })
        fixture.audio.output(0.2f)
        assertTrue(fixture.session.level.value > 0f)
    }

    @Test fun readyTurnCompleteAndToolResultsSnapButFailuresShake() {
        val fixture = Fixture().live()
        fixture.message(
            """{"type":"tool_call","id":"one","name":"open_tab","args":{"tab":"explore"}}"""
        )
        fixture.message("""{"type":"turn_complete"}""")
        assertEquals(3, fixture.events.count { it == VoiceOrbEvent.Snap })
        fixture.audio.failed("Microphone disconnected.")
        assertEquals(VoiceInteractionPhase.ERROR, fixture.session.state.value.phase)
        assertEquals(VoiceOrbEvent.Shake, fixture.events.last())
    }

    private inner class Fixture {
        var elapsed = 0L
        var navigations = 0
        val transport = FakeTransport()
        val audio = FakeAudio()
        val timers = mutableListOf<Timer>()
        val events = mutableListOf<VoiceOrbEvent>()
        val session = VoiceSession(
            VoiceConfiguration(true, "https://example.invalid", "vox_pub_fixture"),
            { transport },
            { audio },
            { true },
            { it() },
            { delay, action -> Timer(elapsed + delay, action).also { timers.add(it) } },
            {
                navigations++
                true
            },
            VoiceDiagnostics {},
            VoicePresentation { elapsed }
        )

        init {
            scope.launch(start = CoroutineStart.UNDISPATCHED) {
                session.events.collect { events.add(it) }
            }
        }

        fun live() = apply {
            session.start()
            ready()
        }

        fun ready() = message("""{"type":"ready"}""")

        fun message(text: String) = transport.listener.message(text)

        fun speak() = audio.input(LOUD)

        fun advanceTo(target: Long) {
            require(target >= elapsed)
            elapsed = target
            while (true) {
                val due = timers.filter { !it.cancelled && it.dueAt <= target }
                    .minByOrNull { it.dueAt } ?: break
                due.cancelled = true
                due.action()
            }
        }
    }

    private companion object {
        // 320 PCM16 samples at 8000, about 0.24 RMS.
        val LOUD = ByteArray(640) { if (it % 2 == 0) 0x40 else 0x1f }
        val QUIET = ByteArray(640)
    }

    private class Timer(val dueAt: Long, val action: () -> Unit) : VoiceCancellation {
        var cancelled = false
        override fun cancel() {
            cancelled = true
        }
    }

    private class FakeTransport : VoiceTransport {
        lateinit var listener: VoiceTransport.Listener
        val sent = mutableListOf<String>()
        var starts = 0
        var closes = 0
        override fun start(listener: VoiceTransport.Listener) {
            starts++
            this.listener = listener
        }
        override fun send(text: String): Boolean = (closes == 0).also { if (it) sent.add(text) }
        override fun close() {
            closes++
        }
    }

    private class FakeAudio : VoiceAudio {
        var starts = 0
        var interrupts = 0
        var inputStops = 0
        lateinit var input: (ByteArray) -> Unit
        lateinit var drained: () -> Unit
        lateinit var failed: (String) -> Unit
        lateinit var output: (Float) -> Unit
        override fun start(
            input: (ByteArray) -> Unit,
            drained: () -> Unit,
            failed: (String) -> Unit,
            output: (Float) -> Unit
        ) {
            starts++
            this.input = input
            this.drained = drained
            this.failed = failed
            this.output = output
        }
        override fun enqueue(pcm: ByteArray): Boolean = true
        override fun interrupt() {
            interrupts++
        }
        override fun stopInput() {
            inputStops++
        }
        override fun close() = Unit
    }
}
